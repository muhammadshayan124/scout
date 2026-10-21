"""Test harness.

- SQLite in memory instead of Postgres: JSONB is rendered as JSON so the real ORM models
  load unchanged. Nothing under test depends on Postgres-specific behaviour.
- A tripwire on smtplib / imaplib: the developer's real `.env` may hold live Gmail
  credentials, so no test is allowed to open a real mail connection. Tests that need a
  server replace the tripwire explicitly.
- `settings` is overridden so tests never read the local `.env`.
"""

import imaplib
import smtplib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from scout.config import settings
from scout.db.models import Base


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


@pytest.fixture(autouse=True)
def _no_real_mail_and_no_dotenv(monkeypatch):
    def tripwire(*args, **kwargs):
        raise AssertionError("test tried to open a real mail connection")

    monkeypatch.setattr(smtplib, "SMTP", tripwire)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", tripwire)
    for name, value in {
        "smtp_username": "sender@example.test",
        "smtp_password": "not-a-real-password",
        "smtp_from_address": "sender@example.test",
        "imap_username": "sender@example.test",
        "imap_password": "not-a-real-password",
        "email_warmup_start_date": "2026-01-01",
        "scout_session_id": "test-session",
    }.items():
        monkeypatch.setattr(settings, name, value)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    yield s
    s.close()
    engine.dispose()
