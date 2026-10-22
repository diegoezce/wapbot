import logging
import re
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl

from anthropic import APIError
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator

from app.agent import ClinicAgent, create_agent
from app.config import Settings, get_settings, require_twilio_settings
from app.db import DatabasePath, database_session, initialize_database

logger = logging.getLogger(__name__)


def _public_request_url(public_base_url: str, request: Request) -> str:
    url = f"{public_base_url.rstrip('/')}{request.url.path}"
    query_string = request.scope.get("query_string", b"").decode("latin-1")
    return f"{url}?{query_string}" if query_string else url


def _record_handoff(database_path: DatabasePath, phone: str, reason: str) -> None:
    with database_session(database_path) as connection:
        connection.execute(
            "INSERT INTO handoffs (phone, reason) VALUES (?, ?)",
            (phone, reason),
        )


def _send_reply(
    client: Client,
    settings: Settings,
    destination: str,
    reply: str,
) -> None:
    _, _, whatsapp_number, _ = require_twilio_settings(settings)
    client.messages.create(
        body=reply,
        from_=whatsapp_number,
        to=destination,
    )


def _process_message(
    agent: ClinicAgent,
    client: Client,
    settings: Settings,
    phone: str,
    text: str,
    database_path: DatabasePath,
) -> None:
    try:
        reply = agent.respond(phone, text)
    except APIError:
        logger.exception("Anthropic API request failed while handling WhatsApp message")
        _record_handoff(database_path, phone, "Assistant API request failed")
        reply = (
            "Un miembro de nuestro equipo se pondrá en contacto contigo pronto."
            if _looks_spanish(text)
            else "A member of our team will follow up with you soon."
        )

    try:
        _send_reply(client, settings, phone, reply)
    except TwilioRestException:
        logger.exception("Twilio failed to send the WhatsApp reply")
        raise


def _looks_spanish(text: str) -> bool:
    return bool(
        re.search(
            r"\b(hola|quiero|quisiera|necesito|cita|disponible|cancelar|"
            r"gracias|precio|cuanto|cuánto|donde|dónde|ayuda|persona|humano|agente|"
            r"sí|si|por favor)\b",
            text.lower(),
        )
    )


def create_app(
    database_path: DatabasePath = None,
    settings: Settings | None = None,
    agent: ClinicAgent | None = None,
    twilio_client: Client | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        active_settings = settings or get_settings()
        require_twilio_settings(active_settings)
        initialize_database(database_path)
        application.state.settings = active_settings
        application.state.agent = agent or create_agent(database_path)
        account_sid, auth_token, _, _ = require_twilio_settings(active_settings)
        application.state.twilio_client = twilio_client or Client(
            account_sid, auth_token
        )
        yield

    application = FastAPI(
        title="Demo Clinic WhatsApp Booking Bot",
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/webhook")
    async def webhook(request: Request, background_tasks: BackgroundTasks):
        active_settings: Settings = (
            request.app.state.settings
            if hasattr(request.app.state, "settings")
            else settings or get_settings()
        )
        account_sid, auth_token, _, public_base_url = require_twilio_settings(
            active_settings
        )
        signature = request.headers.get("X-Twilio-Signature", "")
        body = await request.body()
        try:
            parameters = dict(
                parse_qsl(body.decode("utf-8"), keep_blank_values=True)
            )
        except UnicodeDecodeError as error:
            raise HTTPException(status_code=400, detail="Invalid form encoding") from error

        signature_valid = RequestValidator(auth_token).validate(
            _public_request_url(public_base_url, request),
            parameters,
            signature,
        )
        if not signature_valid:
            return PlainTextResponse("Invalid Twilio signature", status_code=403)

        phone = parameters.get("From", "").strip()
        text = parameters.get("Body", "").strip()
        if not phone or not text:
            raise HTTPException(
                status_code=400,
                detail="Twilio request must include From and Body",
            )

        background_tasks.add_task(
            _process_message,
            request.app.state.agent,
            request.app.state.twilio_client,
            active_settings,
            phone,
            text,
            database_path,
        )
        return Response(
            content=str(MessagingResponse()),
            status_code=200,
            media_type="application/xml",
        )

    return application


app = create_app()
