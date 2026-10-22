import json
import re
from typing import Any

from anthropic import Anthropic

from app.config import get_settings
from app.db import DatabasePath, database_session, initialize_database
from app.tools import (
    book_appointment,
    cancel_appointment,
    check_availability,
    get_faq,
    get_services,
    list_my_appointments,
)

SYSTEM_PROMPT = """You are the friendly, concise WhatsApp receptionist for Demo Clinic, an aesthetics clinic.
Reply in the customer's language, English or Spanish. Keep every reply under 1,000 characters and WhatsApp-friendly; do not use markdown tables.

Use tools for all service, price, availability, appointment, and FAQ facts. Never guess or invent any of them. Availability and prices must come only from tool results.
Before booking or cancelling, get the relevant details and ask the customer to confirm them. A tool result with requires_confirmation=true means the action has NOT happened: state the exact details and ask for a clear yes/no confirmation. Never say a booking or cancellation succeeded unless the corresponding tool returns success.
Call request_human when the customer asks for a person or you cannot help. Do not pretend a human has already replied.
"""

TOOL_DEFINITIONS = [
    {
        "name": "get_services",
        "description": "Get the clinic's services, durations, and prices.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_availability",
        "description": "Get free appointment slots for a service on a date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "integer"},
                "date": {"type": "string", "description": "Date as YYYY-MM-DD"},
                "staff_id": {"type": "integer"},
            },
            "required": ["service_id", "date"],
        },
    },
    {
        "name": "book_appointment",
        "description": "Request to book a slot returned by check_availability. Booking requires a separate explicit customer confirmation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "integer"},
                "staff_id": {"type": "integer"},
                "start_time": {
                    "type": "string",
                    "description": "Exact ISO 8601 time returned by check_availability",
                },
                "customer_name": {"type": "string"},
                "phone": {"type": "string"},
            },
            "required": [
                "service_id",
                "staff_id",
                "start_time",
                "customer_name",
                "phone",
            ],
        },
    },
    {
        "name": "list_my_appointments",
        "description": "List active appointments belonging to a phone number.",
        "input_schema": {
            "type": "object",
            "properties": {"phone": {"type": "string"}},
            "required": ["phone"],
        },
    },
    {
        "name": "cancel_appointment",
        "description": "Request to cancel an active appointment after the customer explicitly confirms.",
        "input_schema": {
            "type": "object",
            "properties": {
                "appointment_id": {"type": "integer"},
                "phone": {"type": "string"},
            },
            "required": ["appointment_id", "phone"],
        },
    },
    {
        "name": "get_faq",
        "description": "Get clinic FAQ answers about address, parking, cancellation, and payments.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "request_human",
        "description": "Log a handoff when the customer requests a human or the question cannot be handled.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]

def _is_spanish(text: str) -> bool:
    normalized = text.lower()
    return bool(
        re.search(
            r"\b(hola|quiero|quisiera|necesito|cita|disponible|cancelar|"
            r"gracias|precio|cuanto|cuánto|donde|dónde|ayuda|persona|humano|agente|"
            r"confirmo|sí|si|por favor)\b",
            normalized,
        )
    )


def _is_confirmation(text: str) -> bool:
    normalized = " ".join(
        re.sub(r"[.!?,¡¿]+", " ", text.lower()).split()
    )
    return normalized in {
        "yes",
        "yes please",
        "i confirm",
        "confirm",
        "book it",
        "go ahead",
        "please do",
        "sí",
        "si",
        "sí por favor",
        "si por favor",
        "confirmo",
        "adelante",
        "hazlo",
        "de acuerdo",
    }


def _handoff_reply(spanish: bool) -> str:
    if spanish:
        return "Un miembro de nuestro equipo se pondrá en contacto contigo pronto."
    return "A member of our team will follow up with you soon."


def _remember_message(
    connection: Any, phone: str, role: str, content: str
) -> None:
    connection.execute(
        """
        INSERT INTO conversations (phone) VALUES (?)
        ON CONFLICT(phone) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
        """,
        (phone,),
    )
    conversation_id = connection.execute(
        "SELECT id FROM conversations WHERE phone = ?", (phone,)
    ).fetchone()["id"]
    connection.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
        (conversation_id, role, content),
    )


def _get_pending_action(connection: Any, phone: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT action, arguments_json FROM pending_actions WHERE phone = ?",
        (phone,),
    ).fetchone()
    if row is None:
        return None
    return {"action": row["action"], "arguments": json.loads(row["arguments_json"])}


def _set_pending_action(
    connection: Any, phone: str, action: str, arguments: dict[str, Any]
) -> None:
    connection.execute(
        """
        INSERT INTO pending_actions (phone, action, arguments_json)
        VALUES (?, ?, ?)
        ON CONFLICT(phone) DO UPDATE SET
            action = excluded.action,
            arguments_json = excluded.arguments_json,
            created_at = CURRENT_TIMESTAMP
        """,
        (phone, action, json.dumps(arguments, sort_keys=True)),
    )


def _delete_pending_action(connection: Any, phone: str) -> None:
    connection.execute("DELETE FROM pending_actions WHERE phone = ?", (phone,))


def _booking_preview(
    arguments: dict[str, Any], database_path: DatabasePath
) -> dict[str, Any]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    slots = check_availability(
        arguments["service_id"],
        arguments["start_time"][:10],
        arguments["staff_id"],
        database_path=database_path,
    )["slots"]
    chosen = next(
        (slot for slot in slots if slot["start_time"] == arguments["start_time"]),
        None,
    )
    if chosen is None:
        raise ValueError("That appointment time is no longer available")
    service = next(
        (
            item
            for item in get_services(database_path=database_path)
            if item["id"] == arguments["service_id"]
        ),
        None,
    )
    if service is None:
        raise ValueError("That service does not exist")
    start = datetime.fromisoformat(chosen["start_time"]).astimezone(
        ZoneInfo("America/New_York")
    )
    return {
        "service": service["name"],
        "price_usd": service["price_usd"],
        "staff_name": chosen["staff_name"],
        "start_time": chosen["start_time"],
        "end_time": chosen["end_time"],
        "customer_name": arguments["customer_name"].strip(),
        "date_and_time": start.strftime("%A, %B %-d at %-I:%M %p"),
    }


def _execute_tool(
    name: str,
    arguments: dict[str, Any],
    phone: str,
    database_path: DatabasePath,
    confirmation_allowed: bool,
) -> tuple[dict[str, Any], bool, bool]:
    if name == "get_services":
        return (
            {"result": get_services(database_path=database_path)},
            confirmation_allowed,
            False,
        )
    if name == "check_availability":
        return {
            "result": check_availability(
                **arguments, database_path=database_path
            )
        }, confirmation_allowed, False
    if name == "list_my_appointments":
        return {
            "result": list_my_appointments(phone, database_path=database_path)
        }, confirmation_allowed, False
    if name == "get_faq":
        return (
            {"result": get_faq(database_path=database_path)},
            confirmation_allowed,
            False,
        )
    if name == "request_human":
        reason = str(arguments["reason"])
        with database_session(database_path) as connection:
            connection.execute(
                "INSERT INTO handoffs (phone, reason) VALUES (?, ?)",
                (phone, reason),
            )
        return {"handoff": True}, False, True

    if name in {"book_appointment", "cancel_appointment"}:
        effective_arguments = dict(arguments)
        if name == "book_appointment":
            effective_arguments["phone"] = phone
            _booking_preview(effective_arguments, database_path)
        else:
            effective_arguments["phone"] = phone
            appointment = next(
                (
                    item
                    for item in list_my_appointments(
                        phone, database_path=database_path
                    )
                    if item["appointment_id"] == effective_arguments["appointment_id"]
                ),
                None,
            )
            if appointment is None:
                raise ValueError("No active appointment found for that customer")

        confirmed_action = False
        with database_session(database_path) as connection:
            pending = _get_pending_action(connection, phone)
            pending_matches = pending == {
                "action": name,
                "arguments": effective_arguments,
            }
            if confirmation_allowed and pending_matches:
                confirmed_action = True
            else:
                _set_pending_action(connection, phone, name, effective_arguments)

        if confirmed_action:
            if name == "book_appointment":
                result = book_appointment(
                    **effective_arguments, database_path=database_path
                )
            else:
                result = cancel_appointment(
                    **effective_arguments, database_path=database_path
                )
            with database_session(database_path) as connection:
                _delete_pending_action(connection, phone)
            return {"result": result}, False, False

        preview: dict[str, Any]
        if name == "book_appointment":
            preview = _booking_preview(effective_arguments, database_path)
        else:
            preview = {
                "appointment_id": appointment["appointment_id"],
                "service": appointment["service_name"],
                "staff_name": appointment["staff_name"],
                "start_time": appointment["start_time"],
            }
        return {
            "requires_confirmation": True,
            "action": name,
            "details": preview,
            "confirmation_phrase": "YES",
        }, False, False

    raise ValueError(f"Unknown tool: {name}")


class ClinicAgent:
    def __init__(
        self,
        client: Any | None = None,
        database_path: DatabasePath = None,
        model: str | None = None,
    ) -> None:
        settings = get_settings() if client is None or model is None else None
        if client is None:
            if settings is None:
                raise RuntimeError("Anthropic client configuration is unavailable")
            client = Anthropic(api_key=settings.anthropic_api_key)
        self.client = client
        self.database_path = database_path
        self.model = model or (
            settings.anthropic_model if settings is not None else "claude-haiku-5-5"
        )
        initialize_database(database_path)

    def respond(self, phone: str, text: str) -> str:
        spanish = _is_spanish(text)
        is_confirmation = _is_confirmation(text)
        with database_session(self.database_path) as connection:
            pending_before_message = _get_pending_action(connection, phone)
            if pending_before_message and not is_confirmation:
                _delete_pending_action(connection, phone)
            _remember_message(connection, phone, "user", text)
            history = connection.execute(
                """
                SELECT role, content FROM (
                    SELECT role, content, id
                    FROM messages
                    WHERE conversation_id = (
                        SELECT id FROM conversations WHERE phone = ?
                    )
                    ORDER BY id DESC LIMIT 10
                ) ORDER BY id
                """,
                (phone,),
            ).fetchall()

        messages: list[dict[str, Any]] = [
            {"role": item["role"], "content": item["content"]} for item in history
        ]
        confirmation_allowed = bool(pending_before_message and is_confirmation)
        system_prompt = SYSTEM_PROMPT
        if confirmation_allowed:
            system_prompt += (
                "\nThe newest customer message is explicit confirmation of the exact "
                "pending action. Complete only that previously confirmed action."
            )

        for _ in range(8):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                system=system_prompt,
                messages=messages,
                tools=TOOL_DEFINITIONS,
            )
            tool_calls = [
                block for block in response.content if block.type == "tool_use"
            ]
            if not tool_calls:
                reply = "".join(
                    block.text for block in response.content if block.type == "text"
                ).strip()
                if not reply:
                    raise RuntimeError("The assistant returned an empty reply")
                reply = reply[:997] + "..." if len(reply) > 1000 else reply
                with database_session(self.database_path) as connection:
                    _remember_message(connection, phone, "assistant", reply)
                return reply

            messages.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                        for block in tool_calls
                    ],
                }
            )
            tool_results = []
            for block in tool_calls:
                try:
                    result, confirmation_allowed, handoff = _execute_tool(
                        block.name,
                        block.input,
                        phone,
                        self.database_path,
                        confirmation_allowed,
                    )
                except ValueError as error:
                    result = {"error": str(error)}
                    handoff = False
                if handoff:
                    reply = _handoff_reply(spanish)
                    with database_session(self.database_path) as connection:
                        _remember_message(connection, phone, "assistant", reply)
                    return reply
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        with database_session(self.database_path) as connection:
            connection.execute(
                "INSERT INTO handoffs (phone, reason) VALUES (?, ?)",
                (phone, "Assistant reached the tool-use limit"),
            )
        reply = _handoff_reply(spanish)
        with database_session(self.database_path) as connection:
            _remember_message(connection, phone, "assistant", reply)
        return reply


def create_agent(database_path: DatabasePath = None) -> ClinicAgent:
    return ClinicAgent(database_path=database_path)
