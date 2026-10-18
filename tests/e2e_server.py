"""Start the real app (`uvicorn lastmile.api:app_factory --factory`) in a subprocess for end-to-end tests.

Configured only through environment variables, exactly like the container and Cloud Run.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AppServer:
    def __init__(self, env: Optional[dict[str, str]] = None, port: Optional[int] = None) -> None:
        self.port = port or free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("LASTMILE_", "GOOGLE_"))}
        self.env.update({"LASTMILE_LLM": "fake", "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"})
        # The app persists by default. Give each test server its own directory;
        # restart tests explicitly pass their shared data directory below.
        self.env["LASTMILE_DATA_DIR"] = str(ROOT / "var" / "test-servers" / uuid.uuid4().hex)
        self.env.update(env or {})
        self.proc: Optional[subprocess.Popen] = None

    def start(self, timeout: float = 30.0) -> "AppServer":
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "lastmile.api:app_factory", "--factory",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=ROOT, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited early:\n{self.proc.stdout.read() if self.proc.stdout else ''}")
            try:
                if httpx.get(self.url + "/api/health", timeout=1.0).status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("server did not become healthy in time")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.proc and self.proc.stdout:
            self.proc.stdout.close()

    def client(self, **kw) -> httpx.Client:
        return httpx.Client(base_url=self.url, timeout=60.0, **kw)

    def __enter__(self) -> "AppServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
