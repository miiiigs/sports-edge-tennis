"""Owner-only authentication store for the Tennis dashboard.

Authentication data lives in its own PostgreSQL schema so dashboard access
state is persistent across Railway deploys without being mixed into canonical
Tennis model, odds, or decision tables.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

SESSION_COOKIE_NAME = "sports_edge_tennis_session"
AUTH_SCHEMA = "sports_edge_auth"
PASSWORD_ITERATIONS = 310_000
SESSION_DAYS = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def allowed_emails() -> frozenset[str]:
    raw = os.getenv("TENNIS_DASHBOARD_AUTH_ALLOWED_EMAILS", "")
    return frozenset(
        normalized
        for normalized in (_normalize_email(value) for value in raw.split(","))
        if normalized
    )


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
        dklen=32,
    )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool
    allow_signup: bool
    cookie_secure: bool

    @classmethod
    def from_env(cls) -> "AuthSettings":
        return cls(
            enabled=_flag("TENNIS_DASHBOARD_AUTH_ENABLED", default=False),
            allow_signup=_flag("TENNIS_DASHBOARD_AUTH_ALLOW_SIGNUP", default=False),
            cookie_secure=_flag("TENNIS_DASHBOARD_AUTH_COOKIE_SECURE", default=True),
        )


class AuthStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, autocommit=True)

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {AUTH_SCHEMA}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {AUTH_SCHEMA}.users (
                    user_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_salt BYTEA NOT NULL,
                    password_hash BYTEA NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {AUTH_SCHEMA}.sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL
                        REFERENCES {AUTH_SCHEMA}.users(user_id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_sports_edge_auth_sessions_expires
                ON {AUTH_SCHEMA}.sessions (expires_at)
                """
            )

    def create_user(self, email: str, password: str) -> dict[str, Any]:
        normalized = _normalize_email(email)
        if not normalized or "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("Enter a valid email address.")
        authorized = allowed_emails()
        if not authorized:
            raise ValueError("No owner email allowlist is configured.")
        if normalized not in authorized:
            raise ValueError("This email is not authorized for this dashboard.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")

        salt = secrets.token_bytes(16)
        password_hash = _hash_password(password, salt)
        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"""
                    INSERT INTO {AUTH_SCHEMA}.users (email, password_salt, password_hash)
                    VALUES (%s, %s, %s)
                    RETURNING user_id, created_at
                    """,
                    (normalized, salt, password_hash),
                ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError("An account with that email already exists.") from exc
        assert row is not None
        return {"id": int(row[0]), "email": normalized, "created_at": row[1]}

    def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        normalized = _normalize_email(email)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT user_id, email, password_salt, password_hash, created_at
                FROM {AUTH_SCHEMA}.users
                WHERE email = %s
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        supplied = _hash_password(password, bytes(row[2]))
        if not hmac.compare_digest(bytes(row[3]), supplied):
            return None
        return {"id": int(row[0]), "email": str(row[1]), "created_at": row[4]}

    def create_session(self, user_id: int) -> str:
        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)
        now = _utcnow()
        expires_at = now + timedelta(days=SESSION_DAYS)
        with self._connect() as conn:
            conn.execute(
                f"DELETE FROM {AUTH_SCHEMA}.sessions WHERE expires_at <= %s",
                (now,),
            )
            conn.execute(
                f"""
                INSERT INTO {AUTH_SCHEMA}.sessions
                    (token_hash, user_id, created_at, expires_at)
                VALUES (%s, %s, %s, %s)
                """,
                (token_hash, int(user_id), now, expires_at),
            )
        return raw_token

    def get_user_for_session(self, raw_token: str | None) -> dict[str, Any] | None:
        if not raw_token:
            return None
        token_hash = _hash_token(raw_token)
        now = _utcnow()
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT u.user_id, u.email, u.created_at, s.expires_at
                FROM {AUTH_SCHEMA}.sessions s
                JOIN {AUTH_SCHEMA}.users u ON u.user_id = s.user_id
                WHERE s.token_hash = %s
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if row[3] <= now:
                conn.execute(
                    f"DELETE FROM {AUTH_SCHEMA}.sessions WHERE token_hash = %s",
                    (token_hash,),
                )
                return None
        return {"id": int(row[0]), "email": str(row[1]), "created_at": row[2]}

    def delete_session(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        with self._connect() as conn:
            conn.execute(
                f"DELETE FROM {AUTH_SCHEMA}.sessions WHERE token_hash = %s",
                (_hash_token(raw_token),),
            )
