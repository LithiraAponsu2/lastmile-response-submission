#!/usr/bin/env python3
"""Smoke-test a running LastMile Alert over HTTP: draft -> preview -> approve -> CAP -> outbox. Stdlib only.

  python scripts/smoke_http.py http://localhost:8080
  python scripts/smoke_http.py https://lastmile-alert-xxxx.a.run.app --api-key "$LASTMILE_API_KEY"
  python scripts/smoke_http.py <url> --no-approve      # read-only on a shared demo: draft and preview only

Uses the server's own sample bulletins (/api/samples), so it needs nothing from this repository. Approving
publishes an EXERCISE alert to the outbox of that deployment (no live channel is connected in this build).
For a private Cloud Run service, LASTMILE_SMOKE_ID_TOKEN supplies a short-lived Google identity token
through the environment. It is sent only as an Authorization header, never printed or added to URLs.
Exit code 0 = every step behaved; 1 = something did not (the failing step is printed).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


class Smoke:
    def __init__(self, base: str, api_key: str | None, timeout: float) -> None:
        self.base, self.api_key, self.timeout = base.rstrip("/"), api_key, timeout

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, bytes, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("X-API-Key", self.api_key)
        identity_token = os.environ.get("LASTMILE_SMOKE_ID_TOKEN")
        if identity_token:
            req.add_header("Authorization", "Bearer " + identity_token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def json(self, method: str, path: str, body: dict | None = None, want: int = 200):
        status, raw, _ = self.call(method, path, body)
        if status != want:
            raise AssertionError(f"{method} {path}: HTTP {status} (wanted {want}): {raw[:300]!r}")
        return json.loads(raw)


def step(msg: str) -> None:
    print(f"  ok  {msg}", flush=True)


def run(base: str, api_key: str | None, approve: bool, sample: str, timeout: float) -> None:
    s = Smoke(base, api_key, timeout)
    health = s.json("GET", "/api/health")
    step(f"health: model={health['llm']} drafting={health.get('drafting', 'model')} "
         f"allow_actual={health['allow_actual']} auth={health['auth_required']}")
    samples = {x["id"]: x for x in s.json("GET", "/api/samples")}
    case = samples[sample]
    status, page, headers = s.call("GET", "/")
    assert status == 200 and b"LastMile" in page, "coordination app not served"
    assert "script-src 'self'" in headers.get("content-security-policy", headers.get("Content-Security-Policy", ""))
    step("app served with a strict CSP")

    run_ = s.json("POST", "/api/runs", {"pack_id": case["pack"], "source_text": case["text"], "mode": "replay"})
    rid = run_["id"]
    blocking = [i["code"] for i in run_["issues"] if i["severity"] == "block"]
    assert run_["status"] == "ready", f"sample drafted as {run_['status']}: {blocking}"
    step(f"drafted {rid}: READY in {', '.join(run_['drafts'])}; attempts {run_['attempts']}")
    status, _, _ = s.call("GET", f"/api/runs/{rid}/cap.xml")
    assert status == 409, "a final CAP exists before approval"
    status, prev, _ = s.call("GET", f"/api/runs/{rid}/cap-preview.xml")
    assert status == 200 and b"<status>Draft</status>" in prev, "CAP preview missing"
    step("no final CAP before approval; preview is status=Draft")
    if not approve:
        return
    done = s.json("POST", f"/api/runs/{rid}/review", {"reviewer": "smoke-test", "decision": "approve",
                                                     "note": "automated smoke test"})
    assert done["status"] == "published", f"approval left the run {done['status']}"
    status, xml, _ = s.call("GET", f"/api/runs/{rid}/cap.xml")
    assert status == 200 and b"<status>Exercise</status>" in xml, "final CAP is not an Exercise"
    outbox = s.json("GET", f"/api/runs/{rid}/outbox")
    assert set(outbox["sms"]) == set(run_["drafts"]), "outbox is missing a language"
    step(f"approved and published {done['cap_identifier']} (status=Exercise); outbox has {', '.join(outbox['sms'])}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--no-approve", action="store_true")
    ap.add_argument("--sample", default="lk-clean-multilevel")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds per request (live model calls are slow)")
    a = ap.parse_args()
    print(f"Smoke test {a.base_url}")
    try:
        run(a.base_url, a.api_key, not a.no_approve, a.sample, a.timeout)
    except (AssertionError, KeyError, OSError, ValueError) as e:
        print(f"  FAIL {type(e).__name__}: {e}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
