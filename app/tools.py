from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.db import DatabasePath, database_session

BUSINESS_TIMEZONE = ZoneInfo("America/New_York")
SLOT_INTERVAL_MINUTES = 30


def _now_local() -> datetime:
    return datetime.now(BUSINESS_TIMEZONE)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("date must use YYYY-MM-DD format") from error


def _parse_start_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("start_time must be an ISO 8601 datetime with timezone") from error

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("start_time must include a timezone offset")

    local = parsed.astimezone(BUSINESS_TIMEZONE)
    if parsed.utcoffset() != local.utcoffset():
        raise ValueError("start_time must use the Demo Clinic timezone offset")
    if local.second or local.microsecond:
        raise ValueError("start_time must be on a whole minute")
    return local


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _list_availability(
    connection: Any,
    service_id: int,
    requested_date: date,
    staff_id: int | None,
    now: datetime,
) -> list[dict[str, Any]]:
    service = connection.execute(
        "SELECT duration_minutes FROM services WHERE id = ?",
        (service_id,),
    ).fetchone()
    if service is None:
        raise ValueError(f"service_id {service_id} does not exist")

    if requested_date < now.date():
        return []

    query = """
        SELECT staff.id, staff.name, weekly_hours.opens_at, weekly_hours.closes_at
        FROM staff
        JOIN weekly_hours ON weekly_hours.staff_id = staff.id
        WHERE weekly_hours.weekday = ?
    """
    parameters: list[Any] = [requested_date.weekday()]
    if staff_id is not None:
        query += " AND staff.id = ?"
        parameters.append(staff_id)

    schedules = connection.execute(query, parameters).fetchall()
    if staff_id is not None and not connection.execute(
        "SELECT 1 FROM staff WHERE id = ?", (staff_id,)
    ).fetchone():
        raise ValueError(f"staff_id {staff_id} does not exist")

    requested_day_start = datetime.combine(
        requested_date, time.min, BUSINESS_TIMEZONE
    ).astimezone(timezone.utc)
    requested_day_end = requested_day_start + timedelta(days=1)
    duration = timedelta(minutes=service["duration_minutes"])
    now_utc = now.astimezone(timezone.utc)
    slots: list[dict[str, Any]] = []

    for schedule in schedules:
        appointments = connection.execute(
            """
            SELECT starts_at, ends_at
            FROM appointments
            WHERE staff_id = ? AND status = 'booked'
              AND starts_at < ? AND ends_at > ?
            """,
            (
                schedule["id"],
                _utc_iso(requested_day_end),
                _utc_iso(requested_day_start),
            ),
        ).fetchall()
        busy = [
            (
                datetime.fromisoformat(appointment["starts_at"]),
                datetime.fromisoformat(appointment["ends_at"]),
            )
            for appointment in appointments
        ]
        opening = datetime.combine(
            requested_date,
            time.fromisoformat(schedule["opens_at"]),
            BUSINESS_TIMEZONE,
        )
        closing = datetime.combine(
            requested_date,
            time.fromisoformat(schedule["closes_at"]),
            BUSINESS_TIMEZONE,
        )
        start = opening
        while start + duration <= closing:
            end = start + duration
            start_utc = start.astimezone(timezone.utc)
            end_utc = end.astimezone(timezone.utc)
            if start_utc > now_utc and all(
                start_utc >= busy_end or end_utc <= busy_start
                for busy_start, busy_end in busy
            ):
                slots.append(
                    {
                        "staff_id": schedule["id"],
                        "staff_name": schedule["name"],
                        "start_time": start.isoformat(timespec="minutes"),
                        "end_time": end.isoformat(timespec="minutes"),
                    }
                )
            start += timedelta(minutes=SLOT_INTERVAL_MINUTES)

    return slots


def get_services(*, database_path: DatabasePath = None) -> list[dict[str, Any]]:
    with database_session(database_path) as connection:
        services = connection.execute(
            """
            SELECT id, name, duration_minutes, price_cents, description
            FROM services ORDER BY id
            """
        ).fetchall()
    return [
        {
            "id": service["id"],
            "name": service["name"],
            "duration_minutes": service["duration_minutes"],
            "price_usd": f"{service['price_cents'] / 100:.2f}",
            "description": service["description"],
        }
        for service in services
    ]


def check_availability(
    service_id: int,
    date: str,
    staff_id: int | None = None,
    *,
    database_path: DatabasePath = None,
) -> dict[str, Any]:
    requested_date = _parse_date(date)
    with database_session(database_path) as connection:
        slots = _list_availability(
            connection, service_id, requested_date, staff_id, _now_local()
        )
    return {
        "date": requested_date.isoformat(),
        "timezone": str(BUSINESS_TIMEZONE),
        "slots": slots,
    }


def book_appointment(
    service_id: int,
    staff_id: int,
    start_time: str,
    customer_name: str,
    phone: str,
    *,
    database_path: DatabasePath = None,
) -> dict[str, Any]:
    local_start = _parse_start_time(start_time)
    if not customer_name.strip():
        raise ValueError("customer_name cannot be empty")
    if not phone.strip():
        raise ValueError("phone cannot be empty")

    with database_session(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        slots = _list_availability(
            connection, service_id, local_start.date(), staff_id, _now_local()
        )
        chosen_slot = next(
            (
                slot
                for slot in slots
                if datetime.fromisoformat(slot["start_time"]).astimezone(timezone.utc)
                == local_start.astimezone(timezone.utc)
            ),
            None,
        )
        if chosen_slot is None:
            raise ValueError("That appointment time is no longer available")

        start_utc = local_start.astimezone(timezone.utc)
        end_local = datetime.fromisoformat(chosen_slot["end_time"])
        end_utc = end_local.astimezone(timezone.utc)
        cursor = connection.execute(
            """
            INSERT INTO appointments
                (service_id, staff_id, customer_name, phone, starts_at, ends_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                service_id,
                staff_id,
                customer_name.strip(),
                phone.strip(),
                _utc_iso(start_utc),
                _utc_iso(end_utc),
            ),
        )
        appointment_id = cursor.lastrowid

    return {
        "appointment_id": appointment_id,
        "service_id": service_id,
        "staff_id": staff_id,
        "staff_name": chosen_slot["staff_name"],
        "customer_name": customer_name.strip(),
        "phone": phone.strip(),
        "start_time": chosen_slot["start_time"],
        "end_time": chosen_slot["end_time"],
        "status": "booked",
    }


def list_my_appointments(
    phone: str, *, database_path: DatabasePath = None
) -> list[dict[str, Any]]:
    with database_session(database_path) as connection:
        appointments = connection.execute(
            """
            SELECT appointments.id, appointments.service_id, services.name AS service_name,
                   appointments.staff_id, staff.name AS staff_name,
                   appointments.customer_name, appointments.starts_at,
                   appointments.ends_at, appointments.status
            FROM appointments
            JOIN services ON services.id = appointments.service_id
            JOIN staff ON staff.id = appointments.staff_id
            WHERE appointments.phone = ? AND appointments.status = 'booked'
            ORDER BY appointments.starts_at
            """,
            (phone,),
        ).fetchall()
    return [
        {
            "appointment_id": appointment["id"],
            "service_id": appointment["service_id"],
            "service_name": appointment["service_name"],
            "staff_id": appointment["staff_id"],
            "staff_name": appointment["staff_name"],
            "customer_name": appointment["customer_name"],
            "start_time": datetime.fromisoformat(
                appointment["starts_at"]
            ).astimezone(BUSINESS_TIMEZONE).isoformat(timespec="minutes"),
            "end_time": datetime.fromisoformat(
                appointment["ends_at"]
            ).astimezone(BUSINESS_TIMEZONE).isoformat(timespec="minutes"),
            "status": appointment["status"],
        }
        for appointment in appointments
    ]


def cancel_appointment(
    appointment_id: int,
    phone: str,
    *,
    database_path: DatabasePath = None,
) -> dict[str, Any]:
    with database_session(database_path) as connection:
        cursor = connection.execute(
            """
            UPDATE appointments
            SET status = 'cancelled'
            WHERE id = ? AND phone = ? AND status = 'booked'
            """,
            (appointment_id, phone),
        )
        if cursor.rowcount != 1:
            raise ValueError("No active appointment found for that customer")
    return {"appointment_id": appointment_id, "status": "cancelled"}


def get_faq(*, database_path: DatabasePath = None) -> list[dict[str, str]]:
    with database_session(database_path) as connection:
        entries = connection.execute(
            "SELECT key, question, answer FROM faq ORDER BY key"
        ).fetchall()
    return [
        {
            "key": entry["key"],
            "question": entry["question"],
            "answer": entry["answer"],
        }
        for entry in entries
    ]
