"""Speed: every run measures itself in milliseconds, the Verifier runs in parallel, and the speed-ups keep the
results identical."""

from __future__ import annotations

import statistics
import threading
import time
import unicodedata

import pytest
from fastapi.testclient import TestClient

from lastmile import checks, geo, publish
from lastmile.api import create_app
from lastmile.config import Settings
from lastmile.llm import FakeLLM, LLMError
from lastmile.pipeline import Options, Pipeline
from lastmile.store import Store


class SlowLLM(FakeLLM):
    """The fake model with Gemini-like waits, so parallelism is measurable."""

    def __init__(self, wait_s: float = 0.15, fail_verify_for: str | None = None) -> None:
        super().__init__()
        self.wait_s, self.fail_verify_for = wait_s, fail_verify_for
        self.verifying_now = 0
        self.max_verifying = 0
        self._busy = threading.Lock()

    def verify(self, *a, **k):
        lang = a[3]
        with self._busy:
            self.verifying_now += 1
            self.max_verifying = max(self.max_verifying, self.verifying_now)
        try:
            time.sleep(self.wait_s)
            if lang == self.fail_verify_for:
                raise LLMError("503 from the verifier model")
            return super().verify(*a, **k)
        finally:
            with self._busy:
                self.verifying_now -= 1


def _run(registry, text, llm=None, **opts):
    store = Store()
    run = Pipeline(llm or FakeLLM(), registry, store, Options(**opts)).run(text, "lk-landslide")
    return store, run


def test_every_run_reports_its_milliseconds(registry, corpus):
    _, run = _run(registry, corpus["lk-clean-multilevel"]["text"])
    t = run.timings
    assert t["draft_ms"] > 0 and t["model_ms"] >= 0 and t["code_ms"] >= 0
    assert t["model_ms"] + t["code_ms"] == pytest.approx(t["draft_ms"], abs=0.05)
    timed = {e.step for e in run.trace if e.ms is not None}
    assert {"injection_scan", "intake", "intake_guards", "rules", "supersede_check", "localize", "verify"} <= timed
    assert all(e.ms >= 0 for e in run.trace if e.ms is not None)


def test_model_time_is_what_the_presenter_says_it_is(registry, corpus):
    """With a model that takes 150 ms per call, model time is ~3 waits (intake, parallel localize, parallel verify),
    and our own code is a small fraction."""
    _, run = _run(registry, corpus["lk-clean-multilevel"]["text"], SlowLLM(0.15))
    assert run.status == "ready"
    assert run.timings["model_ms"] >= 150  # at least the verify wait (intake/localize are not slowed here)
    assert run.timings["code_ms"] < run.timings["draft_ms"]


def test_verifier_runs_in_parallel_and_waits_for_the_slowest_not_the_sum(registry, corpus):
    llm = SlowLLM(0.2)
    _, run = _run(registry, corpus["lk-clean-multilevel"]["text"], llm)
    assert run.status == "ready" and llm.max_verifying == 3  # en, si, ta at once
    verify = next(e for e in run.trace if e.step == "verify")
    assert verify.ms < 0.2 * 3 * 1000 * 0.8  # well under three sequential waits


def test_one_verifier_failure_flags_only_that_language(registry, corpus):
    _, run = _run(registry, corpus["lk-clean-multilevel"]["text"], SlowLLM(0.01, fail_verify_for="ta"))
    flagged = [i.lang for i in run.issues if i.code == "verifier_unavailable"]
    assert flagged == ["ta"] and run.status == "ready"
    assert set(run.back_translations) == {"en", "si"}


def test_approval_and_publish_are_timed(registry, corpus):
    store, run = _run(registry, corpus["lk-clean-multilevel"]["text"])
    done = publish.review(store, registry, Settings(), run.id, "B. Silva", "approve")
    assert done.status == "published"
    assert done.timings["approve_checks_ms"] > 0 and done.timings["publish_ms"] > 0
    step = done.trace[-1]
    assert step.step == "publish" and step.ms == done.timings["publish_ms"] and "XSD" in step.summary


def test_api_returns_timings(corpus):
    c = TestClient(create_app(Settings(rate_limit_per_min=1000), store=Store()))
    r = c.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": corpus["lk-clean-multilevel"]["text"]})
    body = r.json()
    assert r.status_code == 200 and body["timings"]["draft_ms"] > 0
    assert any(e["ms"] is not None for e in body["trace"])


def test_fake_model_draft_and_publish_stay_fast(registry, corpus):
    """Regression guard with a wide margin (about 13 ms on a 2.1 GHz Xeon; budget 1 s for slow CI runners)."""
    text = corpus["lk-clean-multilevel"]["text"]
    _run(registry, text)  # warm-up
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        store, run = _run(registry, text)
        publish.review(store, registry, Settings(), run.id, "B. Silva", "approve")
        times.append(time.perf_counter() - t0)
    assert statistics.median(times) < 1.0


@pytest.mark.parametrize("s", ["", "Rainfall 150 mm", "වර්ෂාපතනය ១៥០ ٣٤ ௧௫௦ १५० mm", "x๑๒", "²³ ½ ①"])
def test_fast_digit_normalisation_matches_the_char_by_char_definition(s):
    slow = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in s)
    assert checks.norm_digits(s) == slow


def test_cached_name_scoring_is_still_exact(registry):
    pack = registry.get("lk-landslide")
    first = geo.resolve(pack, "Badula")
    geo._score.cache_clear()
    geo.norm_name.cache_clear()
    again = geo.resolve(pack, "Badula")
    assert (first.status, first.place_id, first.score) == (again.status, again.place_id, again.score)


def test_gemini_client_is_shared_per_configuration(monkeypatch):
    """Runs reuse one SDK client (and its open connections); a different configuration gets its own."""
    pytest.importorskip("google.genai")
    from lastmile.llm import GeminiLLM, shared_client

    monkeypatch.setenv("GOOGLE_API_KEY", "offline-mock")
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "http://127.0.0.1:9")
    a, b = GeminiLLM(), GeminiLLM()
    assert a.client is b.client is shared_client(60.0)
    assert a.back_translations is not b.back_translations  # per-run diagnostics stay per run
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "http://127.0.0.1:10")
    assert GeminiLLM().client is not a.client


# ---------------------------------------------------------------- fuzzy-matching pruning is exact
def _reference_resolve(pack, as_written, parent_hint=None, kind=None):
    """geo.resolve as it was before the pruning: score every entry, sort, decide (kept here as the oracle)."""
    def entry_score(g, q):
        return max((geo._score(q, n) for n in (geo.norm_name(x) for x in list(g.names.values()) + list(g.aliases))
                    if n), default=0.0)

    def hint_of(h):
        q, best, best_s = geo.norm_name(h), None, 0.0
        for g in pack.region.gazetteer:
            s = entry_score(g, q)
            if s > best_s:
                best, best_s = g.id, s
        return best if best_s >= geo.FUZZY_MIN else None

    q = geo.norm_name(as_written)
    if not q:
        return ("unresolved", None, 0.0, [])
    scored = sorted(((g.id, entry_score(g, q)) for g in pack.region.gazetteer if kind in (None, g.kind)),
                    key=lambda t: -t[1])
    if not scored:
        return ("unresolved", None, 0.0, [])
    top_id, top = scored[0]
    if top < geo.FUZZY_MIN:
        return ("unresolved", None, 0.0, scored[:3])
    hint_id = hint_of(parent_hint) if parent_hint else None
    ties = [pid for pid, s in scored if s >= top - 1e-9 and s >= geo.FUZZY_MIN]
    if len(ties) > 1:
        if hint_id:
            filtered = [p for p in ties if hint_id == p or hint_id in pack.ancestors(p)]
            if len(filtered) == 1:
                top_id, ties = filtered[0], filtered
        if len(ties) > 1:
            return ("ambiguous", None, 0.0, [(p, top) for p in ties])
    if len(ties) == 1 and top < 0.999:
        second = next((s for pid, s in scored if pid != top_id), 0.0)
        if top - second < geo.FUZZY_MARGIN:
            return ("ambiguous", None, 0.0, scored[:3])
    if hint_id and top_id != hint_id and hint_id not in pack.ancestors(top_id):
        return ("conflict", top_id, top, scored[:3])
    return ("exact" if top >= 0.999 else "fuzzy", top_id, top, scored[:3])


def _queries(pack):
    import random

    rnd = random.Random(7)
    out = ["", "x", "Ghostville", "Colombo", "Badula", "Badullaa", "Bandarawella", "Hali Ela", "Nuwara",
           "Passara (Badulla)", "district", "DS division"]
    for g in pack.region.gazetteer:
        for name in list(g.names.values()) + list(g.aliases):
            out.append(name)
            if len(name) > 3:
                i = rnd.randrange(len(name) - 1)
                out += [name[:i] + name[i + 1:], name[:i] + name[i + 1] + name[i] + name[i + 2:],
                        name[:i] + name[i] + name[i:], name[: len(name) // 2], name + "a", name.upper()]
    return out


@pytest.mark.parametrize("pack_id", ["lk-landslide", "np-flood"])
def test_pruned_fuzzy_matching_gives_exactly_the_brute_force_answer(registry, pack_id):
    pack = registry.get(pack_id)
    kinds = [None] + sorted({g.kind for g in pack.region.gazetteer})
    hints = [None] + [g.names.get("en", g.id) for g in pack.region.gazetteer[:6]] + ["Badula", "Nowhere"]
    n = 0
    for q in _queries(pack):
        for kind in kinds:
            for hint in hints[:: 1 if kind is None else 4]:
                got = geo.resolve(pack, q, parent_hint=hint, kind=kind)
                want = _reference_resolve(pack, q, parent_hint=hint, kind=kind)
                assert got.status == want[0], (q, hint, kind)
                if got.status != "ambiguous" or want[3]:
                    assert [c for c in got.candidates] == want[3], (q, hint, kind)
                if got.status in ("exact", "fuzzy", "conflict"):
                    assert (got.place_id, got.score) == (want[1], want[2]), (q, hint, kind)
                n += 1
    assert n > 500


def test_pruning_handles_many_identical_names(registry):
    """More tied entries than the top-3 cut: every tie must still be found (ambiguous lists them all)."""
    pack = registry.get("lk-landslide")
    base = pack.region.gazetteer[0]
    clones = [base.model_copy(update={"id": f"{base.id}-{i}"}) for i in range(5)]
    twin = pack.model_copy(update={"region": pack.region.model_copy(
        update={"gazetteer": list(pack.region.gazetteer) + clones})})
    for q in (base.names["en"], base.names["en"][:-1] + "x"):
        got = geo._ranked(list(twin.region.gazetteer), geo.norm_name(q))
        top = got[0][1]
        brute = sorted(((g.id, geo._entry_score(g, geo.norm_name(q))) for g in twin.region.gazetteer),
                       key=lambda t: -t[1])
        assert [t for t in brute if t[1] >= top - 1e-9] == [t for t in got if t[1] >= top - 1e-9]
        assert got[:3] == brute[:3]


def test_bench_script_runs_and_writes_json(tmp_path):
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "bench.py"
    spec = importlib.util.spec_from_file_location("lastmile_bench", path)
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    out = tmp_path / "bench.json"
    argv = sys.argv
    sys.argv = ["bench.py", "-n", "2", "--no-cold", "--subscribers", "20", "--json", str(out)]
    try:
        assert bench.main() == 0
    finally:
        sys.argv = argv
    import json

    res = json.loads(out.read_text())
    model = res["model drafting (AI Localizers + Verifier)"]
    assert model["status"] == "published" and model["totals"]["draft_ms"]["p50"] > 0
    assert {"intake", "localize", "verify", "publish"} <= set(model["steps"])
    assert res["blocked at the injection scan"]["status"] == "blocked"
