"""Small shared helpers for Yuii Reader."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc


def format_utc(value: datetime) -> str:
    """Return an ISO 8601 UTC timestamp with a trailing Z."""

    return (
        value.astimezone(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def utc_now() -> str:
    return format_utc(datetime.now(UTC))


def iso_after(
    *,
    seconds: float = 0,
    minutes: float = 0,
    hours: float = 0,
    days: float = 0,
) -> str:
    delta = timedelta(seconds=seconds, minutes=minutes, hours=hours, days=days)
    return format_utc(datetime.now(UTC) + delta)


def iso_before(days: float) -> str:
    return format_utc(datetime.now(UTC) - timedelta(days=days))


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def clean_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_pattern(value: str) -> str:
    return "%" + escape_like(value) + "%"
