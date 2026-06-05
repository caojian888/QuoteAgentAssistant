from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


SESSION_COOKIE_NAME = "quote_session"


@dataclass
class AuthContext:
    user_id: int | None
    username: str
    role: str


def database_url() -> str:
    return os.getenv("QUOTE_DATABASE_URL") or os.getenv("DATABASE_URL") or ""


def db_enabled() -> bool:
    return bool(database_url().strip())


def session_ttl_days() -> int:
    try:
        return max(int(os.getenv("QUOTE_SESSION_TTL_DAYS", "7")), 1)
    except ValueError:
        return 7


def cookie_secure() -> bool:
    value = os.getenv("QUOTE_COOKIE_SECURE", "true").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def connect():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url(), row_factory=dict_row)


def password_hash(password: str, salt: bytes | None = None, iterations: int = 310_000) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(iterations, salt.hex(), digest.hex())


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = password_hash(password, bytes.fromhex(salt_hex), int(iterations_text))
        return hmac.compare_digest(candidate, encoded)
    except Exception:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def init_db() -> None:
    if not db_enabled():
        return

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_login_at TIMESTAMPTZ
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id BIGSERIAL PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    user_agent TEXT,
                    ip_address TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    revoked_at TIMESTAMPTZ
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_identities (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    provider_user_id TEXT NOT NULL,
                    union_id TEXT,
                    email TEXT,
                    name TEXT,
                    avatar_url TEXT,
                    raw_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_login_at TIMESTAMPTZ
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                    prompt TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    review_status TEXT,
                    report_path TEXT,
                    assets_path TEXT,
                    review_path TEXT,
                    error TEXT,
                    review_error TEXT,
                    file_names JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS user_hidden_at TIMESTAMPTZ;")
            cur.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS user_hidden_by BIGINT REFERENCES users(id) ON DELETE SET NULL;")
            cur.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS admin_deleted_at TIMESTAMPTZ;")
            cur.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS admin_deleted_by BIGINT REFERENCES users(id) ON DELETE SET NULL;")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS job_assets (
                    id BIGSERIAL PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    asset_index INTEGER NOT NULL,
                    kind TEXT,
                    type TEXT,
                    label TEXT,
                    source TEXT,
                    page INTEGER,
                    mime_type TEXT,
                    path TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(job_id, asset_index)
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS job_files (
                    id BIGSERIAL PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    file_index INTEGER NOT NULL,
                    original_name TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    mime_type TEXT,
                    size_bytes BIGINT,
                    sha256 TEXT,
                    page_count INTEGER,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(job_id, file_index)
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS job_feedback (
                    id BIGSERIAL PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                    verdict TEXT NOT NULL CHECK (verdict IN ('qualified', 'unqualified')),
                    note TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'ui',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(job_id, user_id)
                );
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_updated ON jobs(user_id, updated_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_visible_updated ON jobs(user_id, user_hidden_at, updated_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_admin_visible_updated ON jobs(admin_deleted_at, updated_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_job_files_job ON job_files(job_id, file_index);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_job_feedback_job_updated ON job_feedback(job_id, updated_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_job_feedback_verdict_updated ON job_feedback(verdict, updated_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_identities_provider_user ON user_identities(provider, provider_user_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_identities_user ON user_identities(user_id);")

    seed_admin_user()


def seed_admin_user() -> None:
    username = os.getenv("QUOTE_ADMIN_USERNAME", "").strip()
    password = os.getenv("QUOTE_ADMIN_PASSWORD", "")
    if not username or not password:
        return

    role = os.getenv("QUOTE_ADMIN_ROLE", "admin").strip() or "admin"
    reset_password = os.getenv("QUOTE_ADMIN_RESET_PASSWORD", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }

    encoded = password_hash(password)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            existing = cur.fetchone()
            if existing and reset_password:
                cur.execute(
                    """
                    UPDATE users
                    SET password_hash = %s, role = %s, is_active = TRUE, updated_at = NOW()
                    WHERE username = %s
                    """,
                    (encoded, role, username),
                )
            elif not existing:
                cur.execute(
                    """
                    INSERT INTO users (username, password_hash, role, is_active)
                    VALUES (%s, %s, %s, TRUE)
                    """,
                    (username, encoded, role),
                )


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    if not db_enabled():
        return None

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, password_hash, role, is_active
                FROM users
                WHERE username = %s
                """,
                (username,),
            )
            user = cur.fetchone()
            if not user or not user["is_active"] or not verify_password(password, user["password_hash"]):
                return None
            cur.execute("UPDATE users SET last_login_at = NOW(), updated_at = NOW() WHERE id = %s", (user["id"],))
            return dict(user)


def _oauth_username_seed(provider: str, provider_user_id: str, profile: dict[str, Any]) -> str:
    import re

    raw = str(
        profile.get("name")
        or profile.get("en_name")
        or profile.get("email")
        or f"{provider}_{provider_user_id[-8:]}"
    ).strip()
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    clean = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", raw).strip("._")
    if not clean:
        clean = f"{provider}_{provider_user_id[-8:]}"
    return clean[:48]


def _unique_username(cur: Any, seed: str) -> str:
    base = seed or "user"
    for index in range(0, 100):
        candidate = base if index == 0 else f"{base}_{index + 1}"
        cur.execute("SELECT 1 FROM users WHERE username = %s", (candidate,))
        if not cur.fetchone():
            return candidate
    return f"{base}_{secrets.token_hex(4)}"[:64]


def find_or_create_oauth_user(
    provider: str,
    provider_user_id: str,
    profile: dict[str, Any],
    default_role: str = "user",
) -> dict[str, Any]:
    if not db_enabled():
        raise RuntimeError("Database is not enabled.")

    from psycopg.types.json import Jsonb

    provider = (provider or "").strip().lower()
    provider_user_id = (provider_user_id or "").strip()
    if not provider or not provider_user_id:
        raise ValueError("OAuth provider and provider_user_id are required.")

    default_role = (default_role or "user").strip() or "user"
    union_id = str(profile.get("union_id") or "").strip() or None
    email = str(profile.get("email") or "").strip() or None
    name = str(profile.get("name") or profile.get("en_name") or "").strip() or None
    avatar_url = str(
        profile.get("avatar_url")
        or profile.get("avatar_thumb")
        or profile.get("avatar_middle")
        or profile.get("avatar_big")
        or ""
    ).strip() or None

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT users.id, users.username, users.role, users.is_active
                FROM user_identities
                JOIN users ON users.id = user_identities.user_id
                WHERE user_identities.provider = %s
                  AND user_identities.provider_user_id = %s
                """,
                (provider, provider_user_id),
            )
            user = cur.fetchone()
            if user:
                if not user["is_active"]:
                    raise PermissionError("User is disabled.")
                cur.execute(
                    """
                    UPDATE user_identities
                    SET union_id = COALESCE(%s, union_id),
                        email = COALESCE(%s, email),
                        name = COALESCE(%s, name),
                        avatar_url = COALESCE(%s, avatar_url),
                        raw_profile = %s,
                        updated_at = NOW(),
                        last_login_at = NOW()
                    WHERE provider = %s
                      AND provider_user_id = %s
                    """,
                    (union_id, email, name, avatar_url, Jsonb(profile), provider, provider_user_id),
                )
                cur.execute("UPDATE users SET last_login_at = NOW(), updated_at = NOW() WHERE id = %s", (user["id"],))
                return {"id": user["id"], "username": user["username"], "role": user["role"], "is_active": user["is_active"]}

            username = _unique_username(cur, _oauth_username_seed(provider, provider_user_id, profile))
            cur.execute(
                """
                INSERT INTO users (username, password_hash, role, is_active)
                VALUES (%s, %s, %s, TRUE)
                RETURNING id, username, role, is_active
                """,
                (username, password_hash(secrets.token_urlsafe(32)), default_role),
            )
            created = cur.fetchone()
            cur.execute(
                """
                INSERT INTO user_identities (
                    user_id, provider, provider_user_id, union_id, email, name, avatar_url, raw_profile, last_login_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    created["id"],
                    provider,
                    provider_user_id,
                    union_id,
                    email,
                    name,
                    avatar_url,
                    Jsonb(profile),
                ),
            )
            cur.execute("UPDATE users SET last_login_at = NOW(), updated_at = NOW() WHERE id = %s", (created["id"],))
            return dict(created)


def create_session(user_id: int, user_agent: str = "", ip_address: str = "") -> str:
    token = secrets.token_urlsafe(32)
    expires_at = utc_now() + timedelta(days=session_ttl_days())
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sessions (token_hash, user_id, user_agent, ip_address, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (token_hash(token), user_id, user_agent[:500], ip_address[:128], expires_at),
            )
    return token


def get_session_user(token: str | None) -> AuthContext | None:
    if not token or not db_enabled():
        return None

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT users.id, users.username, users.role
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = %s
                  AND sessions.revoked_at IS NULL
                  AND sessions.expires_at > NOW()
                  AND users.is_active = TRUE
                """,
                (token_hash(token),),
            )
            user = cur.fetchone()
            if not user:
                return None
            return AuthContext(user_id=int(user["id"]), username=str(user["username"]), role=str(user["role"]))


def revoke_session(token: str | None) -> None:
    if not token or not db_enabled():
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET revoked_at = NOW() WHERE token_hash = %s AND revoked_at IS NULL",
                (token_hash(token),),
            )


def upsert_job(record: Any, file_names: list[str] | None = None) -> None:
    if not db_enabled():
        return

    from psycopg.types.json import Jsonb

    names = file_names if file_names is not None else None
    has_file_names = file_names is not None
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                    job_id, user_id, prompt, status, review_status, report_path, assets_path,
                    review_path, error, review_error, file_names, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, '[]'::jsonb), %s, %s)
                ON CONFLICT (job_id) DO UPDATE SET
                    user_id = COALESCE(EXCLUDED.user_id, jobs.user_id),
                    prompt = EXCLUDED.prompt,
                    status = EXCLUDED.status,
                    review_status = EXCLUDED.review_status,
                    report_path = EXCLUDED.report_path,
                    assets_path = EXCLUDED.assets_path,
                    review_path = EXCLUDED.review_path,
                    error = EXCLUDED.error,
                    review_error = EXCLUDED.review_error,
                    file_names = CASE WHEN %s THEN EXCLUDED.file_names ELSE jobs.file_names END,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    record.job_id,
                    getattr(record, "user_id", None),
                    record.prompt,
                    record.status,
                    record.review_status,
                    record.report_path,
                    record.assets_path,
                    record.review_path,
                    record.error,
                    record.review_error,
                    Jsonb(names) if names is not None else None,
                    record.created_at,
                    record.updated_at,
                    has_file_names,
                ),
            )


def replace_job_files(job_id: str, files: list[dict[str, Any]]) -> None:
    if not db_enabled():
        return

    from psycopg.types.json import Jsonb

    file_names = [str(item.get("original_name") or item.get("stored_name") or "") for item in files]
    file_names = [name for name in file_names if name]

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM jobs WHERE job_id = %s", (job_id,))
            if not cur.fetchone():
                return

            cur.execute("DELETE FROM job_files WHERE job_id = %s", (job_id,))
            for index, item in enumerate(files, start=1):
                cur.execute(
                    """
                    INSERT INTO job_files (
                        job_id, file_index, original_name, stored_name, mime_type,
                        size_bytes, sha256, page_count
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (job_id, file_index) DO UPDATE SET
                        original_name = EXCLUDED.original_name,
                        stored_name = EXCLUDED.stored_name,
                        mime_type = EXCLUDED.mime_type,
                        size_bytes = EXCLUDED.size_bytes,
                        sha256 = EXCLUDED.sha256,
                        page_count = EXCLUDED.page_count
                    """,
                    (
                        job_id,
                        int(item.get("file_index") or index),
                        str(item.get("original_name") or item.get("stored_name") or "upload.bin"),
                        str(item.get("stored_name") or item.get("original_name") or "upload.bin"),
                        item.get("mime_type"),
                        item.get("size_bytes"),
                        item.get("sha256"),
                        item.get("page_count"),
                    ),
                )

            cur.execute(
                "UPDATE jobs SET file_names = %s, updated_at = updated_at WHERE job_id = %s",
                (Jsonb(file_names), job_id),
            )


def replace_job_assets(job_id: str, assets: list[dict[str, Any]]) -> None:
    if not db_enabled():
        return

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM job_assets WHERE job_id = %s", (job_id,))
            for index, asset in enumerate(assets):
                cur.execute(
                    """
                    INSERT INTO job_assets (
                        job_id, asset_index, kind, type, label, source, page, mime_type, path
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        job_id,
                        index,
                        asset.get("kind"),
                        asset.get("type"),
                        asset.get("label"),
                        asset.get("source"),
                        asset.get("page"),
                        asset.get("mime_type"),
                        asset.get("path"),
                    ),
                )


def _public_feedback(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    for key in ["created_at", "updated_at"]:
        value = item.get(key)
        if hasattr(value, "isoformat"):
            item[key] = value.isoformat()
    return item


def get_job_feedback(job_id: str, user_id: int | None) -> dict[str, Any] | None:
    if not db_enabled():
        return None

    with connect() as conn:
        with conn.cursor() as cur:
            if user_id is None:
                cur.execute(
                    """
                    SELECT id, job_id, user_id, verdict, note, source, created_at, updated_at
                    FROM job_feedback
                    WHERE job_id = %s
                      AND user_id IS NULL
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (job_id,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, job_id, user_id, verdict, note, source, created_at, updated_at
                    FROM job_feedback
                    WHERE job_id = %s
                      AND user_id = %s
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (job_id, user_id),
                )
            row = cur.fetchone()
    return _public_feedback(row)


def upsert_job_feedback(
    job_id: str,
    user_id: int | None,
    verdict: str,
    note: str = "",
    source: str = "ui",
) -> dict[str, Any]:
    if not db_enabled():
        raise RuntimeError("Database is not enabled.")

    verdict = verdict.strip().lower()
    if verdict not in {"qualified", "unqualified"}:
        raise ValueError("Invalid feedback verdict.")
    note = str(note or "")[:2000]
    source = (str(source or "ui").strip() or "ui")[:80]

    with connect() as conn:
        with conn.cursor() as cur:
            if user_id is None:
                cur.execute("DELETE FROM job_feedback WHERE job_id = %s AND user_id IS NULL", (job_id,))
                cur.execute(
                    """
                    INSERT INTO job_feedback (job_id, user_id, verdict, note, source)
                    VALUES (%s, NULL, %s, %s, %s)
                    RETURNING id, job_id, user_id, verdict, note, source, created_at, updated_at
                    """,
                    (job_id, verdict, note, source),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO job_feedback (job_id, user_id, verdict, note, source)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (job_id, user_id) DO UPDATE SET
                        verdict = EXCLUDED.verdict,
                        note = EXCLUDED.note,
                        source = EXCLUDED.source,
                        updated_at = NOW()
                    RETURNING id, job_id, user_id, verdict, note, source, created_at, updated_at
                    """,
                    (job_id, user_id, verdict, note, source),
                )
            row = cur.fetchone()
    feedback = _public_feedback(row)
    if feedback is None:
        raise RuntimeError("Feedback was not saved.")
    return feedback


def job_owner(job_id: str) -> int | None:
    if not db_enabled():
        return None
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            return int(row["user_id"]) if row and row["user_id"] is not None else None


def job_admin_deleted_at(job_id: str) -> str | None:
    if not db_enabled():
        return None

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT admin_deleted_at FROM jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            if not row or not row["admin_deleted_at"]:
                return None
            value = row["admin_deleted_at"]
            return value.isoformat() if hasattr(value, "isoformat") else str(value)


def hide_job_for_user(job_id: str, user_id: int | None) -> bool:
    if not db_enabled() or user_id is None:
        return False

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET user_hidden_at = COALESCE(user_hidden_at, NOW()),
                    user_hidden_by = COALESCE(user_hidden_by, %s)
                WHERE job_id = %s
                  AND user_id = %s
                RETURNING job_id
                """,
                (user_id, job_id, user_id),
            )
            return cur.fetchone() is not None


def admin_soft_delete_job(job_id: str, admin_user_id: int | None) -> bool:
    if not db_enabled():
        return False

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET admin_deleted_at = COALESCE(admin_deleted_at, NOW()),
                    admin_deleted_by = COALESCE(admin_deleted_by, %s),
                    updated_at = NOW()
                WHERE job_id = %s
                RETURNING job_id
                """,
                (admin_user_id, job_id),
            )
            return cur.fetchone() is not None


def count_jobs_for_user(user_id: int | None, include_all: bool = False, max_items: int | None = None) -> int:
    if not db_enabled():
        return 0

    with connect() as conn:
        with conn.cursor() as cur:
            if include_all:
                cur.execute("SELECT COUNT(*) AS count FROM jobs WHERE admin_deleted_at IS NULL")
            else:
                cur.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM jobs
                    WHERE user_id = %s
                      AND user_hidden_at IS NULL
                    """,
                    (user_id,),
                )
            row = cur.fetchone()

    count = int(row["count"] if row else 0)
    return min(count, max_items) if max_items is not None else count


def list_jobs_for_user(
    user_id: int | None,
    include_all: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if not db_enabled():
        return []

    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    with connect() as conn:
        with conn.cursor() as cur:
            if include_all:
                cur.execute(
                    """
                    WITH file_names_by_job AS (
                        SELECT job_id, jsonb_agg(original_name ORDER BY file_index) AS file_names
                        FROM job_files
                        GROUP BY job_id
                    )
                    SELECT jobs.job_id, jobs.user_id, owners.username, jobs.prompt, jobs.status,
                           jobs.review_status, jobs.error,
                           COALESCE(file_names_by_job.file_names, jobs.file_names, '[]'::jsonb) AS file_names,
                           jobs.created_at, jobs.updated_at, jobs.report_path, jobs.assets_path,
                           jobs.user_hidden_at, jobs.admin_deleted_at,
                           deleters.username AS admin_deleted_by_username
                    FROM jobs
                    LEFT JOIN users AS owners ON owners.id = jobs.user_id
                    LEFT JOIN users AS deleters ON deleters.id = jobs.admin_deleted_by
                    LEFT JOIN file_names_by_job ON file_names_by_job.job_id = jobs.job_id
                    WHERE jobs.admin_deleted_at IS NULL
                    ORDER BY jobs.updated_at DESC
                    LIMIT %s
                    OFFSET %s
                    """,
                    (limit, offset),
                )
            else:
                cur.execute(
                    """
                    WITH file_names_by_job AS (
                        SELECT job_id, jsonb_agg(original_name ORDER BY file_index) AS file_names
                        FROM job_files
                        GROUP BY job_id
                    )
                    SELECT jobs.job_id, jobs.user_id, owners.username, jobs.prompt, jobs.status,
                           jobs.review_status, jobs.error,
                           COALESCE(file_names_by_job.file_names, jobs.file_names, '[]'::jsonb) AS file_names,
                           jobs.created_at, jobs.updated_at, jobs.report_path, jobs.assets_path,
                           jobs.user_hidden_at, jobs.admin_deleted_at,
                           deleters.username AS admin_deleted_by_username
                    FROM jobs
                    LEFT JOIN users AS owners ON owners.id = jobs.user_id
                    LEFT JOIN users AS deleters ON deleters.id = jobs.admin_deleted_by
                    LEFT JOIN file_names_by_job ON file_names_by_job.job_id = jobs.job_id
                    WHERE jobs.user_id = %s
                      AND jobs.user_hidden_at IS NULL
                    ORDER BY jobs.updated_at DESC
                    LIMIT %s
                    OFFSET %s
                    """,
                    (user_id, limit, offset),
                )
            rows = cur.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if not include_all:
            item.pop("username", None)
        for key in ["created_at", "updated_at", "user_hidden_at", "admin_deleted_at"]:
            value = item.get(key)
            if hasattr(value, "isoformat"):
                item[key] = value.isoformat()
        file_names = item.get("file_names")
        if isinstance(file_names, str):
            try:
                item["file_names"] = json.loads(file_names)
            except json.JSONDecodeError:
                item["file_names"] = []
        item["status_url"] = f"/api/jobs/{item['job_id']}"
        item["report_url"] = f"/api/jobs/{item['job_id']}/report"
        item["assets_url"] = f"/api/jobs/{item['job_id']}/assets"
        return_path_keys = ["report_path", "assets_path"]
        for path_key in return_path_keys:
            item.pop(path_key, None)
        results.append(item)
    return results
