from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DB_URL = f"sqlite:///{DATA_DIR / 'chess_match.db'}"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DB_URL)
ENVIRONMENT = os.getenv("FLASK_ENV", "").lower()
IS_PRODUCTION = ENVIRONMENT == "production"

if IS_PRODUCTION and DATABASE_URL == DEFAULT_DB_URL:
    raise RuntimeError(
        "DATABASE_URL must be set to a persistent database (e.g., Supabase Postgres) "
        "when running in production."
    )

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, future=True, echo=False, connect_args=connect_args)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)

Base = declarative_base()


def redacted_database_url(url: str | None = None) -> str:
    raw_url = url or DATABASE_URL
    try:
        parsed = urlsplit(raw_url)
    except Exception:
        return "<invalid database url>"

    if not parsed.scheme:
        return "<invalid database url>"

    hostname = parsed.hostname or ""
    username = parsed.username or ""
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""

    auth = ""
    if username:
        auth = f"{username}:***@"
    elif hostname:
        auth = ""

    return f"{parsed.scheme}://{auth}{hostname}{port}{path}{query}"


def database_url_warnings(url: str | None = None) -> list[str]:
    raw_url = url or DATABASE_URL
    warnings: list[str] = []

    try:
        parsed = urlsplit(raw_url)
    except Exception:
        return ["DATABASE_URL is not a valid URL."]

    scheme = parsed.scheme
    if not scheme:
        warnings.append("DATABASE_URL has no scheme.")
        return warnings

    if "postgresql" in scheme:
        if "+psycopg" not in scheme:
            warnings.append(
                "Postgres URL does not use the psycopg SQLAlchemy driver "
                "(expected postgresql+psycopg://...)."
            )
        params = parse_qs(parsed.query)
        if params.get("sslmode", [None])[0] != "require":
            warnings.append(
                "Postgres URL is missing sslmode=require; many cloud environments require SSL."
            )

    if raw_url == DEFAULT_DB_URL and IS_PRODUCTION:
        warnings.append("Production is using local SQLite fallback URL.")

    return warnings


@contextmanager
def session_scope() -> Iterator:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
