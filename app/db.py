import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TypeAlias

DatabasePath: TypeAlias = str | Path | None

DEFAULT_DATABASE_PATH = Path(
    os.environ.get("DATABASE_PATH", "demo_clinic.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS business_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS weekly_hours (
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    opens_at TEXT NOT NULL,
    closes_at TEXT NOT NULL,
    UNIQUE (staff_id, weekday),
    CHECK (opens_at < closes_at)
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY,
    service_id INTEGER NOT NULL REFERENCES services(id),
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    customer_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked'
        CHECK (status IN ('booked', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (starts_at < ends_at)
);

CREATE INDEX IF NOT EXISTS appointments_staff_time
    ON appointments (staff_id, starts_at, ends_at, status);
CREATE INDEX IF NOT EXISTS appointments_phone
    ON appointments (phone, status);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    phone TEXT NOT NULL UNIQUE,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id)
        ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS messages_conversation_time
    ON messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY,
    phone TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending_actions (
    phone TEXT PRIMARY KEY,
    action TEXT NOT NULL CHECK (action IN ('book_appointment', 'cancel_appointment')),
    arguments_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS faq (
    key TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL
);
"""

SETTINGS = (
    ("business_name", "Demo Clinic"),
    ("timezone", "America/New_York"),
)

SERVICES = (
    (
        1,
        "Skin Consultation",
        30,
        5000,
        "A personalized consultation to discuss your skincare goals.",
    ),
    (
        2,
        "Signature Facial",
        60,
        12000,
        "A relaxing facial tailored to your skin.",
    ),
    (
        3,
        "Chemical Peel",
        45,
        15000,
        "A professional peel selected for your skin and goals.",
    ),
)

STAFF = ((1, "Ava"), (2, "Jordan"))

WEEKLY_HOURS = (
    (1, 0, "09:00", "17:00"),
    (1, 1, "09:00", "17:00"),
    (1, 2, "09:00", "17:00"),
    (1, 3, "09:00", "17:00"),
    (1, 4, "09:00", "17:00"),
    (2, 1, "10:00", "18:00"),
    (2, 2, "10:00", "18:00"),
    (2, 3, "10:00", "18:00"),
    (2, 4, "10:00", "18:00"),
    (2, 5, "10:00", "16:00"),
)

FAQ = (
    (
        "address",
        "Where are you located?",
        "Demo Clinic is at 125 Madison Avenue, New York, NY.",
    ),
    (
        "parking",
        "Is parking available?",
        "Metered street parking and nearby paid garages are available.",
    ),
    (
        "cancellation",
        "What is your cancellation policy?",
        "Please give at least 24 hours' notice to cancel or reschedule.",
    ),
    (
        "payment",
        "What payment methods do you accept?",
        "We accept major credit and debit cards.",
    ),
)


def connect(database_path: DatabasePath = None) -> sqlite3.Connection:
    path = Path(database_path) if database_path is not None else DEFAULT_DATABASE_PATH
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def database_session(
    database_path: DatabasePath = None,
) -> Iterator[sqlite3.Connection]:
    connection = connect(database_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db(database_path: DatabasePath = None) -> None:
    with database_session(database_path) as connection:
        connection.executescript(SCHEMA)


def seed_db(database_path: DatabasePath = None) -> None:
    with database_session(database_path) as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO business_settings (key, value) VALUES (?, ?)",
            SETTINGS,
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO services
                (id, name, duration_minutes, price_cents, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            SERVICES,
        )
        connection.executemany(
            "INSERT OR IGNORE INTO staff (id, name) VALUES (?, ?)",
            STAFF,
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO weekly_hours
                (staff_id, weekday, opens_at, closes_at)
            VALUES (?, ?, ?, ?)
            """,
            WEEKLY_HOURS,
        )
        connection.executemany(
            "INSERT OR IGNORE INTO faq (key, question, answer) VALUES (?, ?, ?)",
            FAQ,
        )


def initialize_database(database_path: DatabasePath = None) -> None:
    init_db(database_path)
    seed_db(database_path)
