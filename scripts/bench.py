#!/usr/bin/env python3
"""How long does one alert take? Millisecond timings, per stage, with percentiles.

  python scripts/bench.py                         # offline, fake model: our own code, stage by stage
  python scripts/bench.py --model-wait 1200,2000  # add a pretend model wait per call (fast,verifier ms): shows
                                                  # what parallel Localizers/Verifiers save; NOT Gemini's real latency
  LASTMILE_LLM=gemini python scripts/bench.py --llm gemini -n 5   # real model (needs credentials): real numbers
  python scripts/bench.py --http http://localhost:8080            # HTTP round trips against a running instance
  python scripts/bench.py --json bench.json       # machine-readable copy (to compare Python versions, machines)

Numbers are wall-clock (time.perf_counter) on this machine. Each scenario runs once untimed first (warm-up),
like a server that has been up for a minute; the cold start is reported separately.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lastmile import geo, notify, public, publish  # noqa: E402
from lastmile.config import Settings  # noqa: E402
from lastmile.eval import load_corpus  # noqa: E402
from lastmile.llm import FakeLLM, make_llm  # noqa: E402
from lastmile.mailer import FileMailer  # noqa: E402
from lastmile.packs import PackRegistry  # noqa: E402
from lastmile.pipeline import Options, Pipeline  # noqa: E402
from lastmile.store import Store  # noqa: E402
from lastmile.subscriptions import Subscriber, SubscriptionStore  # noqa: E402


class WaitingLLM:
    """The fake model plus a fixed wait per call, to show how the pipeline's shape (parallel calls) affects the
    total. The waits are whatever you pass in, not a measurement of any real model."""

    def __init__(self, inner, fast_ms: float, verifier_ms: float) -> None:
        self.inner, self.fast, self.verifier = inner, fast_ms / 1000, verifier_ms / 1000
        self.name = f"{inner.name}+wait"
        self.back_translations = inner.back_translations

    def intake(self, *a, **k):
        time.sleep(self.fast)
        return self.inner.intake(*a, **k)

    def localize(self, *a, **k):
        time.sleep(self.fast)
        return self.inner.localize(*a, **k)

    def verify(self, *a, **k):
        time.sleep(self.verifier)
        return self.inner.verify(*a, **k)


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summary(xs: list[float]) -> dict:
    return {"p50": round(pct(xs, 50), 2), "p95": round(pct(xs, 95), 2), "max": round(max(xs), 2) if xs else 0.0,
            "mean": round(statistics.fmean(xs), 2) if xs else 0.0, "n": len(xs)}


def make_model(kind: str, wait: tuple[float, float] | None):
    base = FakeLLM() if kind == "fake" else make_llm(kind)
    return WaitingLLM(base, *wait) if wait else base


def bench_pipeline(registry: PackRegistry, text: str, n: int, kind: str, wait, publish_too: bool = True,
                   **opts) -> dict:
    steps: dict[str, list[float]] = defaultdict(list)
    totals: dict[str, list[float]] = defaultdict(list)
    status = ""
    for i in range(n + 1):  # i == 0: warm-up, not recorded
        store = Store()
        t0 = time.perf_counter()
        run = Pipeline(make_model(kind, wait), registry, store, Options(**opts)).run(text, "lk-landslide")
        if publish_too and run.status == "ready":
            run = publish.review(store, registry, Settings(), run.id, "Bench Reviewer", "approve")
        wall = (time.perf_counter() - t0) * 1000
        status = run.status
        if i == 0:
            continue
        totals["end_to_end_ms"].append(wall)
        for k, v in run.timings.items():
            totals[k].append(v)
        seen: dict[str, float] = defaultdict(float)
        for e in run.trace:
            if e.ms is not None:
                seen[e.step.split(" #")[0]] += e.ms  # re-localize #2 counts with localize rounds
        for k, v in seen.items():
            steps[k].append(v)
    return {"status": status, "steps": {k: summary(v) for k, v in steps.items()},
            "totals": {k: summary(v) for k, v in totals.items()}}


def bench_public(registry: PackRegistry, text: str, n: int, subscribers: int) -> dict:
    """The publish side people see: the public map and the warning emails."""
    store = Store()
    run = Pipeline(FakeLLM(), registry, store).run(text, "lk-landslide")
    run = publish.review(store, registry, Settings(), run.id, "Bench Reviewer", "approve")
    pack = registry.get("lk-landslide")
    at = datetime.now(timezone.utc)
    out: dict[str, dict] = {}

    def timed(name: str, fn, reps: int = n) -> None:
        fn()
        xs = []
        for _ in range(reps):
            t0 = time.perf_counter()
            fn()
            xs.append((time.perf_counter() - t0) * 1000)
        out[name] = summary(xs)

    sit = public.situation(registry, store, pack.region.id, at, True)
    timed("public_situation", lambda: public.situation(registry, store, pack.region.id, at, True))
    timed("public_map_svg", lambda: geo.render_region_svg(pack, sit["area_levels"], "si", "EXERCISE"))
    timed("atom_feed", lambda: public.atom_feed(registry, store, "http://localhost", at, True))

    subs = SubscriptionStore()
    ids = [g.id for g in pack.region.gazetteer]
    for i in range(subscribers):
        s = Subscriber(id=f"sub_{i:06d}", email=f"person{i}@example.org", region=pack.region.id,
                       areas=[ids[i % len(ids)]], lang=pack.langs[i % len(pack.langs)], confirmed=True,
                       include_exercises=True)
        subs.subs[s.id] = s
    mailer = FileMailer(keep=10)
    counts = {}

    def send() -> None:
        counts.update(notify.dispatch(mailer, subs, store, pack, run, "http://localhost"))

    timed("email", send, reps=max(3, n // 10))
    out[f"email: {subscribers} subscribers, {counts.get('matched', 0)} match"] = out.pop("email")
    return out


def bench_cold_start() -> dict:
    """A fresh process: import + build the app (packs, XSD, boundaries), then the first draft and publish."""
    code = (
        "import time; t0=time.perf_counter()\n"
        "from lastmile.api import create_app\nfrom lastmile.config import Settings\n"
        "t1=time.perf_counter(); app=create_app(Settings()); t2=time.perf_counter()\n"
        "from fastapi.testclient import TestClient\nfrom lastmile.eval import load_corpus\n"
        "c=TestClient(app); text={x['id']:x for x in load_corpus()}['lk-clean-multilevel']['text']\n"
        "t3=time.perf_counter(); r=c.post('/api/runs',json={'pack_id':'lk-landslide','source_text':text}).json()\n"
        "c.post(f\"/api/runs/{r['id']}/review\",json={'reviewer':'Bench Reviewer','decision':'approve'}); "
        "t4=time.perf_counter()\n"
        "print(round((t1-t0)*1000,1), round((t2-t1)*1000,1), round((t4-t3)*1000,1))\n"
    )
    env = {**os.environ, "LASTMILE_LLM": "fake", "LASTMILE_MAIL": "off"}
    env.pop("LASTMILE_DATA_DIR", None)
    res = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    if res.returncode:
        return {"error": res.stderr.strip()[-300:]}
    imp, build, first = (float(x) for x in res.stdout.split())
    return {"import_ms": imp, "create_app_ms": build, "first_draft_and_publish_ms": first}


def bench_http(base: str, text: str, n: int, api_key: str = "") -> dict:
    import httpx

    h = {"X-API-Key": api_key} if api_key else {}
    out: dict[str, list[float]] = defaultdict(list)
    with httpx.Client(base_url=base.rstrip("/"), headers=h, timeout=120) as c:
        for i in range(n + 1):
            t0 = time.perf_counter()
            c.get("/api/health").raise_for_status()
            t1 = time.perf_counter()
            r = c.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": text})
            r.raise_for_status()
            t2 = time.perf_counter()
            run = r.json()
            if run["status"] == "ready":
                c.post(f"/api/runs/{run['id']}/review",
                       json={"reviewer": f"Bench Reviewer {i}", "decision": "approve"}).raise_for_status()
            t3 = time.perf_counter()
            c.get("/api/public/map.svg", params={"region": "lk", "lang": "si"})
            t4 = time.perf_counter()
            if i == 0:
                continue
            out["GET /api/health"].append((t1 - t0) * 1000)
            out["POST /api/runs (draft)"].append((t2 - t1) * 1000)
            out["  of which server-side draft"].append(run.get("timings", {}).get("draft_ms", 0.0))
            out["POST review (approve+publish)"].append((t3 - t2) * 1000)
            out["GET public map.svg"].append((t4 - t3) * 1000)
    return {k: summary(v) for k, v in out.items()}


def table(title: str, rows: dict[str, dict]) -> str:
    lines = [f"\n{title}", f"  {'':38} {'p50 ms':>9} {'p95 ms':>9} {'max ms':>9}"]
    for k, s in rows.items():
        lines.append(f"  {k:38} {s['p50']:9.2f} {s['p95']:9.2f} {s['max']:9.2f}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=50, help="timed repetitions per scenario (default 50)")
    ap.add_argument("--case", default="lk-clean-multilevel", help="corpus case to draft")
    ap.add_argument("--llm", default="fake", help="fake (default) or gemini")
    ap.add_argument("--model-wait", default="", metavar="FAST,VERIFIER",
                    help="pretend model wait per call in ms, e.g. 1200,2000 (fake model only)")
    ap.add_argument("--subscribers", type=int, default=1000, help="subscribers for the email dispatch timing")
    ap.add_argument("--http", default="", metavar="URL", help="also time HTTP round trips against this server")
    ap.add_argument("--api-key", default=os.environ.get("LASTMILE_API_KEY", ""))
    ap.add_argument("--no-cold", action="store_true", help="skip the cold-start subprocess")
    ap.add_argument("--json", default="", metavar="FILE", help="write the results as JSON")
    a = ap.parse_args()

    corpus = {c["id"]: c for c in load_corpus()}
    text = corpus[a.case]["text"]
    wait = tuple(float(x) for x in a.model_wait.split(",")) if a.model_wait else None
    if wait and len(wait) != 2:
        ap.error("--model-wait takes two numbers: FAST,VERIFIER")
    registry = PackRegistry()
    registry.all()

    res: dict = {
        "machine": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                    "cpu": _cpu(), "cores": os.cpu_count(), "os": platform.platform(terse=True)},
        "settings": {"n": a.n, "case": a.case, "llm": a.llm, "model_wait_ms": wait},
    }
    print(f"LastMile Alert timing — Python {res['machine']['python']} on {res['machine']['cpu']} "
          f"({res['machine']['cores']} cores); case {a.case}; model {a.llm}"
          + (f" + pretend waits {wait[0]:.0f}/{wait[1]:.0f} ms per call" if wait else "") + f"; n={a.n}")

    if not a.no_cold:
        res["cold_start"] = bench_cold_start()
        print("\nCold start (new process):", ", ".join(f"{k} {v}" for k, v in res["cold_start"].items()))

    n_model = a.n if not (wait or a.llm != "fake") else max(3, min(a.n, 10))
    for name, opts in (("model drafting (AI Localizers + Verifier)", {}),
                       ("template-first (AI Intake + Verifier only)", {"drafting": "templates"})):
        r = bench_pipeline(registry, text, n_model, a.llm, wait, **opts)
        res[name] = r
        print(table(f"{name}: per stage (status {r['status']})", r["steps"]))
        print(table("  totals", r["totals"]))
    inj = corpus.get("lk-injection")
    if inj:
        r = bench_pipeline(registry, inj["text"], a.n, "fake", None)
        res["blocked at the injection scan"] = r
        print(table(f"blocked bulletin (injection, status {r['status']}): totals", r["totals"]))

    res["public"] = bench_public(registry, text, a.n, a.subscribers)
    print(table("Public side", res["public"]))

    if a.http:
        res["http"] = bench_http(a.http, text, max(3, min(a.n, 20)), a.api_key)
        print(table(f"HTTP against {a.http}", res["http"]))

    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


def _cpu() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


if __name__ == "__main__":
    raise SystemExit(main())
