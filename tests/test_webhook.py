from urllib.parse import urlencode

from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.config import Settings
from app.main import create_app

PUBLIC_BASE_URL = "https://appointments.example.com"
AUTH_TOKEN = "test-auth-token"


class FakeAgent:
    def __init__(self):
        self.messages = []

    def respond(self, phone, text):
        self.messages.append((phone, text))
        return "Thanks! How can I help?"


class FakeTwilioClient:
    class Messages:
        def __init__(self):
            self.sent = []

        def create(self, **kwargs):
            self.sent.append(kwargs)

    def __init__(self):
        self.messages = self.Messages()


def setup_client(tmp_path):
    settings = Settings(
        anthropic_api_key="test-key",
        anthropic_model="test-model",
        twilio_account_sid="AC00000000000000000000000000000000",
        twilio_auth_token=AUTH_TOKEN,
        twilio_whatsapp_number="whatsapp:+14155238886",
        public_base_url=PUBLIC_BASE_URL,
    )
    agent = FakeAgent()
    twilio = FakeTwilioClient()
    application = create_app(
        database_path=tmp_path / "clinic.sqlite3",
        settings=settings,
        agent=agent,
        twilio_client=twilio,
    )
    return TestClient(application), agent, twilio


def test_valid_twilio_signature_uses_public_tunnel_url_and_sends_reply(tmp_path):
    client, agent, twilio = setup_client(tmp_path)
    parameters = {
        "From": "whatsapp:+15551234567",
        "To": "whatsapp:+14155238886",
        "Body": "Hi",
    }
    public_url = f"{PUBLIC_BASE_URL}/webhook"
    signature = RequestValidator(AUTH_TOKEN).compute_signature(
        public_url, parameters
    )

    with client:
        response = client.post(
            "/webhook",
            content=urlencode(parameters),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Twilio-Signature": signature,
            },
        )

    assert response.status_code == 200
    assert agent.messages == [("whatsapp:+15551234567", "Hi")]
    assert twilio.messages.sent == [
        {
            "body": "Thanks! How can I help?",
            "from_": "whatsapp:+14155238886",
            "to": "whatsapp:+15551234567",
        }
    ]


def test_invalid_twilio_signature_is_rejected(tmp_path):
    client, agent, twilio = setup_client(tmp_path)

    with client:
        response = client.post(
            "/webhook",
            data={"From": "whatsapp:+15551234567", "Body": "Hi"},
            headers={"X-Twilio-Signature": "invalid"},
        )

    assert response.status_code == 403
    assert agent.messages == []
    assert twilio.messages.sent == []
