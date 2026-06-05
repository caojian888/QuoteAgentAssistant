from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any


logger = logging.getLogger("uvicorn.error")

RULES_DIR = Path(__file__).resolve().parent / "rules"
DEFAULT_RULE_FILES = (
    "drawing_marking_rules.json",
    "material_visual_rules.json",
)


def _as_lines(value: Any, *, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}- {key}:")
                lines.extend(_as_lines(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}- {key}: {item}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.extend(_as_lines(item, indent=indent))
            else:
                lines.append(f"{prefix}- {item}")
        return lines
    return [f"{prefix}- {value}"]


@lru_cache(maxsize=16)
def load_rule_file(name: str) -> dict[str, Any]:
    path = RULES_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("quote knowledge rule file missing: %s", path)
    except json.JSONDecodeError as exc:
        logger.warning("quote knowledge rule file invalid: %s error=%s", path, exc)
    return {}


@lru_cache(maxsize=8)
def row_image_knowledge_text() -> str:
    sections: list[str] = []
    for item in iter_rule_documents():
        sections.append(item["text"])
    if not sections:
        return ""
    return "\n".join(sections)


def rule_file_names() -> list[str]:
    rule_names = list(DEFAULT_RULE_FILES)
    try:
        extra_rule_names = sorted(path.name for path in RULES_DIR.glob("*.json") if path.name not in rule_names)
    except OSError as exc:
        logger.warning("quote knowledge rules dir scan failed: %s error=%s", RULES_DIR, exc)
        extra_rule_names = []
    rule_names.extend(extra_rule_names)
    return rule_names


def iter_rule_documents() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for name in rule_file_names():
        rule = load_rule_file(name)
        title = str(rule.get("title") or "").strip()
        if not title:
            continue
        lines = [f"## {title}"]
        for section in rule.get("sections") or []:
            heading = str(section.get("name") or "").strip()
            if heading:
                lines.append(f"{heading}:")
            lines.extend(_as_lines(section.get("rules") or [], indent=0))
        documents.append(
            {
                "name": name,
                "title": title,
                "text": "\n".join(lines),
                "metadata": {
                    "source": "quote_assistant_rules",
                    "file": name,
                    "stage": "row_image",
                },
            }
        )
    return documents
