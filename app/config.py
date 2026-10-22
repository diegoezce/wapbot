import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv(override=True)


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    anthropic_model: str
    twilio_account_sid: str | None
    twilio_auth_token: str | None
    twilio_whatsapp_number: str | None
    public_base_url: str | None


def get_settings() -> Settings:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required")

    return Settings(
        anthropic_api_key=api_key,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "").strip()
        or "claude-haiku-5-5",
        twilio_account_sid=_optional_environment_value("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=_optional_environment_value("TWILIO_AUTH_TOKEN"),
        twilio_whatsapp_number=_optional_environment_value(
            "TWILIO_WHATSAPP_NUMBER"
        ),
        public_base_url=_optional_environment_value("PUBLIC_BASE_URL"),
    )


def _optional_environment_value(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def require_twilio_settings(settings: Settings) -> tuple[str, str, str, str]:
    account_sid = settings.twilio_account_sid
    auth_token = settings.twilio_auth_token
    whatsapp_number = settings.twilio_whatsapp_number
    public_base_url = settings.public_base_url
    if not account_sid or not auth_token or not whatsapp_number or not public_base_url:
        raise RuntimeError(
            "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
            "TWILIO_WHATSAPP_NUMBER, and PUBLIC_BASE_URL are required"
        )
    return account_sid, auth_token, whatsapp_number, public_base_url
