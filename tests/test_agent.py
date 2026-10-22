from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.agent import ClinicAgent
from app.db import database_session, initialize_database

NEW_YORK = ZoneInfo("America/New_York")
BOOKING_INPUT = {
    "service_id": 2,
    "staff_id": 1,
    "start_time": "2027-01-04T09:00-05:00",
    "customer_name": "Taylor",
    "phone": "+15550000000",
}


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **_kwargs):
        return next(self.responses)


def tool_response(tool_name, arguments):
    return SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id="call-1",
                name=tool_name,
                input=arguments,
            )
        ]
    )


def text_response(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


@pytest.fixture
def database(tmp_path, monkeypatch):
    database_path = tmp_path / "clinic.sqlite3"
    initialize_database(database_path)
    monkeypatch.setattr(
        "app.tools._now_local", lambda: datetime(2026, 10, 1, 12, tzinfo=NEW_YORK)
    )
    return database_path


def test_booking_requires_a_separate_explicit_confirmation(database):
    fake_client = FakeClient(
        [
            tool_response("book_appointment", BOOKING_INPUT),
            text_response(
                "Please confirm: Signature Facial with Ava on Monday, January 4 at 9:00 AM for $120.00."
            ),
            tool_response("book_appointment", BOOKING_INPUT),
            text_response("Your appointment is booked."),
        ]
    )
    agent = ClinicAgent(client=fake_client, database_path=database, model="test")

    first_reply = agent.respond("+15550000000", "Book me the facial on Monday.")
    with database_session(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0] == 1

    second_reply = agent.respond("+15550000000", "Yes, please.")
    with database_session(database) as connection:
        appointment = connection.execute(
            "SELECT phone, status FROM appointments"
        ).fetchone()
        assert appointment["phone"] == "+15550000000"
        assert appointment["status"] == "booked"
        assert connection.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0] == 0

    assert "confirm" in first_reply.lower()
    assert "booked" in second_reply.lower()


def test_first_message_that_says_yes_cannot_confirm_an_unrequested_action(database):
    fake_client = FakeClient(
        [
            tool_response("book_appointment", BOOKING_INPUT),
            text_response("Please confirm these appointment details."),
        ]
    )
    agent = ClinicAgent(client=fake_client, database_path=database, model="test")

    agent.respond("+15550000000", "Yes, book this.")

    with database_session(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 0


def test_booking_is_bound_to_the_inbound_phone_number(database):
    fake_client = FakeClient(
        [
            tool_response("book_appointment", BOOKING_INPUT),
            text_response("Please confirm these appointment details."),
        ]
    )
    agent = ClinicAgent(client=fake_client, database_path=database, model="test")

    agent.respond("+15559999999", "Book a facial.")

    with database_session(database) as connection:
        pending = connection.execute(
            "SELECT arguments_json FROM pending_actions"
        ).fetchone()
        assert "+15559999999" in pending["arguments_json"]
        assert "+15550000000" not in pending["arguments_json"]


def test_a_new_question_clears_an_unconfirmed_booking(database):
    fake_client = FakeClient(
        [
            tool_response("book_appointment", BOOKING_INPUT),
            text_response("Please confirm these appointment details."),
            tool_response("get_faq", {}),
            text_response("We have metered street parking nearby."),
        ]
    )
    agent = ClinicAgent(client=fake_client, database_path=database, model="test")

    agent.respond("+15550000000", "Book the facial.")
    agent.respond("+15550000000", "Where can I park?")

    with database_session(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0] == 0


def test_human_handoff_is_logged(database):
    fake_client = FakeClient(
        [tool_response("request_human", {"reason": "Customer asked for a person"})]
    )
    agent = ClinicAgent(client=fake_client, database_path=database, model="test")

    reply = agent.respond("+15550000000", "I need a human.")

    with database_session(database) as connection:
        handoff = connection.execute(
            "SELECT phone, reason FROM handoffs"
        ).fetchone()
    assert "follow up" in reply
    assert handoff["phone"] == "+15550000000"
    assert handoff["reason"] == "Customer asked for a person"
