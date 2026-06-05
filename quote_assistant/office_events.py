from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


EVENT_FILE_NAME = "office_events.jsonl"
_context: ContextVar[dict[str, Any] | None] = ContextVar("office_event_context", default=None)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def office_event_path(job_dir: Path) -> Path:
    return job_dir / EVENT_FILE_NAME


@contextmanager
def office_event_context(job_id: str, job_dir: Path) -> Iterator[None]:
    token = _context.set({"job_id": job_id, "job_dir": Path(job_dir)})
    try:
        yield
    finally:
        _context.reset(token)


def current_office_event_context() -> dict[str, Any] | None:
    return _context.get()


def bind_office_event_context(job_id: str, job_dir: Path) -> object:
    return _context.set({"job_id": job_id, "job_dir": Path(job_dir)})


def reset_office_event_context(token: object) -> None:
    _context.reset(token)


def append_office_event(
    *,
    job_id: str,
    job_dir: Path,
    agent_id: str,
    event: str,
    status: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_id": uuid.uuid4().hex,
        "job_id": job_id,
        "agent_id": agent_id,
        "event": event,
        "status": status,
        "message": message,
        "created_at": utc_now_iso(),
        "metadata": metadata or {},
    }
    if error:
        payload["error"] = str(error)

    path = office_event_path(Path(job_dir))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as event_file:
        event_file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        event_file.write("\n")
    return payload


def log_office_event(
    agent_id: str,
    event: str,
    *,
    status: str = "running",
    message: str = "",
    metadata: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    context = current_office_event_context()
    if not context:
        return None

    return append_office_event(
        job_id=str(context["job_id"]),
        job_dir=Path(context["job_dir"]),
        agent_id=agent_id,
        event=event,
        status=status,
        message=message,
        metadata=metadata,
        error=error,
    )


def read_office_events(job_dir: Path, *, limit: int = 80) -> list[dict[str, Any]]:
    path = office_event_path(Path(job_dir))
    if not path.exists() or not path.is_file():
        return []

    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for line in lines[-max(limit, 1):]:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events
