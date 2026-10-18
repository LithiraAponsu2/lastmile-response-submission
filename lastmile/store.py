"""Run + published-alert storage. In-memory, optionally mirrored to JSON files.

(On Cloud Run, mount a volume or swap this class for Firestore; the interface is tiny.)
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Optional

from .schemas import PublishedAlert, Run, utcnow_iso


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class Store:
    def __init__(self, data_dir: Optional[os.PathLike | str] = None) -> None:
        self._lock = threading.RLock()
        self.dir = Path(data_dir) if data_dir else None
        self.runs: dict[str, Run] = {}
        self.published: list[PublishedAlert] = []
        if self.dir:
            (self.dir / "runs").mkdir(parents=True, exist_ok=True)
            (self.dir / "outbox").mkdir(parents=True, exist_ok=True)
            self._load()

    # ---------------------------------------------------------------- ids
    @staticmethod
    def new_id() -> str:
        return "run_" + uuid.uuid4().hex[:10]

    # --------------------------------------------------------------- runs
    def put(self, run: Run) -> Run:
        with self._lock:
            self.runs[run.id] = run
            if self.dir:
                _atomic_write(self.dir / "runs" / f"{run.id}.json", run.model_dump_json(indent=1))
        return run

    def get(self, run_id: str) -> Optional[Run]:
        with self._lock:
            return self.runs.get(run_id)

    def list(self, limit: int = 50) -> list[Run]:
        with self._lock:
            return sorted(self.runs.values(), key=lambda r: r.created_at, reverse=True)[:limit]

    def audit(self, run: Run, actor: str, action: str, detail: str = "", data: Optional[dict] = None) -> None:
        with self._lock:
            entry = {"at": utcnow_iso(), "actor": actor, "action": action, "detail": detail}
            if data:
                entry.update(data)
            run.audit.append(entry)
            self.put(run)

    # ---------------------------------------------------------- published
    def active_alerts(self) -> list[PublishedAlert]:
        with self._lock:
            return [a for a in self.published if not a.cancelled]

    def add_published(self, alert: PublishedAlert, supersedes: list[str]) -> None:
        with self._lock:
            for a in self.published:
                # never let an Exercise close an Actual alert (or the reverse), whatever references say
                if a.identifier in supersedes and a.status == alert.status:
                    a.cancelled = True
            self.published.append(alert)
            self._save_published()

    def write_outbox(self, run_id: str, name: str, content: str | bytes) -> Optional[Path]:
        if not self.dir:
            return None
        d = self.dir / "outbox" / run_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return path

    # ---------------------------------------------------------- internals
    def _save_published(self) -> None:
        if self.dir:
            _atomic_write(self.dir / "published.json",
                          json.dumps([a.model_dump() for a in self.published], ensure_ascii=False, indent=1))

    def _load(self) -> None:
        assert self.dir
        for f in sorted((self.dir / "runs").glob("run_*.json")):
            try:
                run = Run.model_validate_json(f.read_text(encoding="utf-8"))
                self.runs[run.id] = run
            except (OSError, ValueError):
                continue
        pf = self.dir / "published.json"
        if pf.is_file():
            try:
                self.published = [PublishedAlert.model_validate(a)
                                  for a in json.loads(pf.read_text(encoding="utf-8"))]
            except (OSError, ValueError):
                self.published = []
