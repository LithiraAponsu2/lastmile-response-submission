"""Live alerts are checked against the real clock, so live-mode tests need a bulletin that is valid *now*."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

SL = timezone(timedelta(hours=5, minutes=30))


def live_text(text: str, now: datetime | None = None, valid_hours: int = 24) -> str:
    """Rewrite a synthetic bulletin's Issued / Valid until lines to (now - 5 min, now + valid_hours), Sri Lanka time."""
    now = (now or datetime.now(timezone.utc)).astimezone(SL)
    issued = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
    until = (now + timedelta(hours=valid_hours)).strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"(Issued:\s*)\d{4}-\d{2}-\d{2} \d{2}:\d{2}", rf"\g<1>{issued}", text)
    return re.sub(r"(Valid until:\s*)\d{4}-\d{2}-\d{2} \d{2}:\d{2}", rf"\g<1>{until}", text)
