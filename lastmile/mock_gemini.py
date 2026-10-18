"""A local stand-in for the Gemini REST API, so the REAL Gemini code path runs end to end without credentials.

    python -m lastmile.mock_gemini --port 8090
    # in another shell, exactly the production settings except where the requests go:
    export LASTMILE_LLM=gemini GOOGLE_API_KEY=offline-mock GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:8090
    uvicorn lastmile.api:app_factory --factory --port 8080

Everything between the app and the "model" is real: make_llm -> GeminiLLM -> google-genai SDK -> HTTP with
system instruction, JSON response schema and temperature. The answers come from the deterministic fake model
(llm.answer_prompt), so this measures the plumbing, not Gemini's language skill. When the real credentials
arrive, drop GOOGLE_GEMINI_BASE_URL / GOOGLE_API_KEY and set the Vertex variables (docs/DEPLOYMENT.md).

Fault injection, to see degraded modes through the real SDK:
    --fail localize=503        every Localizer call returns HTTP 503   (-> approved-template fallback, flagged)
    --fail intake=500          Intake fails                            (-> visible blocked run, llm_error)
    --fail verify=429          Verifier fails                          (-> verifier_unavailable warning)
    --fault localize:drop_number:si   the fake model makes one mistake (-> repair loop)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .llm import FakeLLM, Fault, answer_prompt
from .packs import Pack, PackRegistry

_GENERATE = re.compile(r"/models/([^/:]+):generateContent$")
ROLES = ("intake", "localize", "verify")


def role_of(system: str) -> str:
    if "Intake agent" in system:
        return "intake"
    if "Localizer agent" in system:
        return "localize"
    if "independent Verifier" in system:
        return "verify"
    return "unknown"


def pack_for(registry: PackRegistry, system: str, user: str) -> Pack:
    """Which pack a prompt belongs to, from what the prompt itself carries (timezone + measures, place ids or names)."""
    for pack in registry.all():
        measures = str(list(pack.hazard.measures.keys()) or ["other"])
        if f"({pack.region.timezone})" in system and measures in system:            # intake
            return pack
        for g in pack.region.gazetteer:
            if f'"{g.id}"' in user or f'"{g.names.get("en", "")}"' in user:   # localize: ids; verify: en names
                return pack
    raise ValueError("no pack matches this prompt")


def _wrap(text: str) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0}}


def _error(status: int) -> dict:
    names = {400: "INVALID_ARGUMENT", 429: "RESOURCE_EXHAUSTED", 500: "INTERNAL", 503: "UNAVAILABLE"}
    return {"error": {"code": status, "message": f"mock failure ({status})", "status": names.get(status, "UNKNOWN")}}


class MockGemini:
    """Serve the Gemini `generateContent` and `models` endpoints on 127.0.0.1. Thread-safe; start()/close()."""

    MODELS = ("gemini-3.5-flash-lite", "gemini-3.6-flash")

    def __init__(self, registry: Optional[PackRegistry] = None, faults: Optional[list[Fault]] = None,
                 fail: Optional[dict[str, int]] = None, host: str = "127.0.0.1", port: int = 0) -> None:
        self.registry = registry or PackRegistry()
        self.fake = FakeLLM(faults)
        self.fail = dict(fail or {})
        self.requests: list[dict] = []          # {"model", "role", "status"} per call, for tests and the console log
        self._lock = threading.Lock()
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            disable_nagle_algorithm = True  # headers and body go out as separate writes: avoid the ~40 ms ACK stall

            def _send(self, status: int, payload: dict) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", "application/json; charset=utf-8")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802 — models.list (scripts/live_smoke.py --list-models)
                path = self.path.split("?", 1)[0]
                if path.rstrip("/").endswith("/models"):
                    return self._send(200, {"models": [{"name": f"models/{m}", "displayName": m,
                                                        "supportedActions": ["generateContent"]}
                                                       for m in mock.MODELS]})
                if path == "/health":
                    return self._send(200, {"ok": True, "calls": len(mock.requests)})
                self._send(404, _error(404))

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("content-length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self._send(400, _error(400))
                m = _GENERATE.search(self.path.split("?", 1)[0])
                if not m:
                    return self._send(404, _error(404))
                status, payload = mock.generate(m.group(1), body)
                self._send(status, payload)

            def log_message(self, *a):  # keep test output clean
                pass

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.url = f"http://{host}:{self.server.server_address[1]}"
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------- serving
    def generate(self, model: str, body: dict) -> tuple[int, dict]:
        si = body.get("systemInstruction") or {}
        system = "".join(p.get("text", "") for p in si.get("parts", []))
        user = "\n".join(p.get("text", "") for c in body.get("contents", []) for p in c.get("parts", []))
        role = role_of(system)
        status = self.fail.get(role, 200)
        if status == 200:
            if (body.get("generationConfig") or {}).get("responseMimeType") != "application/json":
                status = 400                     # the app always asks for JSON; anything else is a client bug
        payload: dict
        if status != 200:
            payload = _error(status)
        else:
            try:
                payload = _wrap(answer_prompt(self.fake, pack_for(self.registry, system, user), system, user))
            except (ValueError, KeyError, IndexError, StopIteration) as e:
                status, payload = 400, {"error": {"code": 400, "message": f"mock: {e}", "status": "INVALID_ARGUMENT"}}
        with self._lock:
            self.requests.append({"model": model, "role": role, "status": status})
        return status, payload

    def start(self) -> "MockGemini":
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self) -> "MockGemini":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()


def _parse_fail(items: list[str]) -> dict[str, int]:
    out = {}
    for it in items:
        role, _, code = it.partition("=")
        if role not in ROLES or not code.isdigit():
            raise argparse.ArgumentTypeError(f"--fail wants role=HTTP_STATUS with role in {ROLES}, got {it!r}")
        out[role] = int(code)
    return out


def _parse_fault(items: list[str]) -> list[Fault]:
    out = []
    for it in items:
        parts = it.split(":")
        if len(parts) not in (2, 3) or parts[0] not in ROLES:
            raise argparse.ArgumentTypeError(f"--fault wants role:kind[:lang], got {it!r}")
        out.append(Fault(parts[0], parts[1], parts[2] if len(parts) == 3 else None, (0,)))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--fail", action="append", default=[], metavar="ROLE=STATUS")
    ap.add_argument("--fault", action="append", default=[], metavar="ROLE:KIND[:LANG]")
    a = ap.parse_args(argv)
    try:
        mock = MockGemini(faults=_parse_fault(a.fault), fail=_parse_fail(a.fail), host=a.host, port=a.port)
    except argparse.ArgumentTypeError as e:
        ap.error(str(e))
    print(f"Mock Gemini listening on {mock.url}  (answers come from the deterministic fake model)\n"
          f"  export LASTMILE_LLM=gemini GOOGLE_API_KEY=offline-mock GOOGLE_GEMINI_BASE_URL={mock.url}", flush=True)
    try:
        mock.server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
