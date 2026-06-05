from __future__ import annotations


ENDPOINT_SUFFIXES = ("/responses", "/chat/completions", "/completions")


def normalize_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return base_url

    url = base_url.rstrip("/")
    lowered = url.lower()
    for suffix in ENDPOINT_SUFFIXES:
        if lowered.endswith(suffix):
            return url[: -len(suffix)]
    return url
