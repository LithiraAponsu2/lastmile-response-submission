"""Runtime settings, all from environment variables so the same image runs anywhere."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    llm: str = "fake"                       # LASTMILE_LLM: fake | gemini
    data_dir: Optional[str] = None          # LASTMILE_DATA_DIR: persist runs/outbox here (else memory only)
    storage_bucket: Optional[str] = None    # LASTMILE_STORAGE_BUCKET: private durable cloud demo store
    allow_actual: bool = False              # LASTMILE_ALLOW_ACTUAL: without it everything is CAP status=Exercise
    api_key: Optional[str] = None           # LASTMILE_API_KEY: if set, mutating endpoints need X-API-Key
    rate_limit_per_min: int = 10            # LASTMILE_RATE_LIMIT_PER_MIN: per client, for POST /api/runs
    max_source_chars: int = 20000           # input cap (cost + abuse guard)
    plugins: str = ""                       # LASTMILE_PLUGINS: e.g. "issuer,http:https://x/inspect;post_draft"
    drafting: str = "model"                 # LASTMILE_DRAFTING: model | templates (template-first: AI reads + verifies only)
    mail: str = "file"                      # LASTMILE_MAIL: file (write .eml, default) | smtp | off
    public_url: Optional[str] = None        # LASTMILE_PUBLIC_URL: base URL used in email links (else the request's)
    dev_mailbox: bool = False               # LASTMILE_DEV_MAILBOX: expose /api/dev/mailbox (file mailer only; demos)
    public_show_exercise: bool = True       # LASTMILE_PUBLIC_SHOW_EXERCISE: show drills on the public map, watermarked
    public_rate_limit_per_min: int = 10     # LASTMILE_PUBLIC_RATE_LIMIT_PER_MIN: subscribe/sign-in/unsubscribe per client

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            llm=os.environ.get("LASTMILE_LLM", "fake").lower(),
            data_dir=os.environ.get("LASTMILE_DATA_DIR") or None,
            storage_bucket=os.environ.get("LASTMILE_STORAGE_BUCKET") or None,
            allow_actual=_flag("LASTMILE_ALLOW_ACTUAL", False),
            api_key=os.environ.get("LASTMILE_API_KEY") or None,
            rate_limit_per_min=int(os.environ.get("LASTMILE_RATE_LIMIT_PER_MIN", "10")),
            max_source_chars=int(os.environ.get("LASTMILE_MAX_SOURCE_CHARS", "20000")),
            plugins=os.environ.get("LASTMILE_PLUGINS", ""),
            drafting=os.environ.get("LASTMILE_DRAFTING", "model").strip().lower() or "model",
            mail=os.environ.get("LASTMILE_MAIL", "file").strip().lower() or "file",
            public_url=(os.environ.get("LASTMILE_PUBLIC_URL") or "").rstrip("/") or None,
            dev_mailbox=_flag("LASTMILE_DEV_MAILBOX", False),
            public_show_exercise=_flag("LASTMILE_PUBLIC_SHOW_EXERCISE", True),
            public_rate_limit_per_min=int(os.environ.get("LASTMILE_PUBLIC_RATE_LIMIT_PER_MIN", "10")),
        )
