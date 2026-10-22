from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.db import database_session, initialize_database
from app.tools import check_availability

NEW_YORK = ZoneInfo("America/New_York")
MONDAY = "2025-01-06"


@pytest.fixture
def database(tmp_path, monkeypatch):
    database_path = tmp_path / "clinic.sqlite3"
    initialize_database(database_path)
    monkeypatch.setattr(
        "app.tools._now_local", lambda: datetime(2025, 1, 1, 12, tzinfo=NEW_YORK)
    )
    return database_path


def test_availability_excludes_slots_overlapping_bookings(database):
    with database_session(database) as connection:
        connection.execute(
            """
            INSERT INTO appointments
                (service_id, staff_id, customer_name, phone, starts_at, ends_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                1,
                "Existing Customer",
                "+15550000000",
                "2025-01-06T14:15:00+00:00",
                "2025-01-06T14:45:00+00:00",
            ),
        )

    result = check_availability(1, MONDAY, 1, database_path=database)
    starts = [slot["start_time"] for slot in result["slots"]]

    assert "2025-01-06T09:00-05:00" not in starts
    assert "2025-01-06T09:30-05:00" not in starts
    assert "2025-01-06T10:00-05:00" in starts


def test_availability_does_not_include_slots_outside_working_hours(database):
    result = check_availability(1, MONDAY, 1, database_path=database)
    starts = [slot["start_time"] for slot in result["slots"]]

    assert "2025-01-06T08:30-05:00" not in starts
    assert "2025-01-06T16:30-05:00" in starts
    assert "2025-01-06T17:00-05:00" not in starts


def test_availability_is_empty_for_past_dates(database):
    result = check_availability(1, "2024-12-31", database_path=database)

    assert result["slots"] == []


def test_availability_is_empty_for_a_date_with_no_staff_hours(database):
    result = check_availability(1, "2025-01-05", database_path=database)

    assert result["slots"] == []
