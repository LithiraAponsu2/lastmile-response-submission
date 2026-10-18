"""Benchmark the public-request reader: the offline rules against Gemini, on labelled unseen cases.

Each case goes through the same code path as the app: read → code checks (counts must be quoted) →
decision (close junk only without danger words; doubt → verify; no incident → hold). Scored: the
decision, real requests wrongly closed (the headline; target 0), junk caught, need, people count,
invented counts, urgency and the owning body. Every failure is listed.

    python scripts/benchmark_public.py                 # rules only, offline
    python scripts/benchmark_public.py --gemini        # also Gemini (billable; signed-in gcloud identity in memory)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lastmile import public_requests as public  # noqa: E402

DATA = ROOT / "data" / "benchmark" / "public_requests.json"


def run(cases, districts, read):
    rows = []
    for case in cases:
        started = time.monotonic()
        try:
            reading = read(case["text"])
            error = None
        except Exception as exc:  # a failed model call is scored as the app would behave: rules fall back
            reading, error = public.rules_read(case["text"]), type(exc).__name__
        reading, notes = public.checked(reading, case["text"])
        district = public.district_of(case["area"])
        decision, reason = public.decide(reading, case["text"], case["area"], {"id": "x"} if district in districts else None)
        rows.append({"id": case["id"], "lang": case["lang"], "decision": decision, "need": reading.need, "people": reading.people,
                     "urgency": reading.urgency, "reason": reason, "notes": notes, "error": error, "seconds": round(time.monotonic() - started, 2)})
    return rows


def score(cases, rows):
    by = {r["id"]: r for r in rows}
    out = {"cases": len(cases), "failures": []}
    def tally(name, ok, total):
        out[name] = {"correct": ok, "of": total, "rate": round(ok / total, 3) if total else None}
    decision_ok = wrongly_closed = junk = junk_caught = need_ok = need_n = people_ok = people_n = invented = urgent_n = urgent_ok = owner_ok = 0
    for c in cases:
        r = by[c["id"]]
        good = r["decision"] != "closed" if c["decision"] == "not_closed" else r["decision"] == c["decision"]
        decision_ok += good
        if not good:
            out["failures"].append({"id": c["id"], "what": "decision", "expected": c["decision"], "got": r["decision"], "text": c["text"][:90]})
        if c["decision"] != "closed" and r["decision"] == "closed":
            wrongly_closed += 1
        if c["decision"] == "closed":
            junk += 1
            junk_caught += r["decision"] == "closed"
        if c["need"]:
            need_n += 1
            accepted = c["need"] if isinstance(c["need"], list) else [c["need"]]
            ok = r["need"] in accepted
            need_ok += ok
            owner_ok += public.OWNER.get(r["need"]) in {public.OWNER[n] for n in accepted}
            if not ok:
                out["failures"].append({"id": c["id"], "what": "need", "expected": accepted, "got": r["need"], "text": c["text"][:90]})
        if c["decision"] not in {"closed", "verify"}:
            people_n += 1
            ok = r["people"] == c["people"]
            people_ok += ok
            invented += c["people"] is None and r["people"] is not None
            if not ok:
                out["failures"].append({"id": c["id"], "what": "people", "expected": c["people"], "got": r["people"], "text": c["text"][:90]})
        if c["urgent"]:
            urgent_n += 1
            urgent_ok += r["urgency"] == "high"
            if r["urgency"] != "high":
                out["failures"].append({"id": c["id"], "what": "urgency", "expected": "high", "got": r["urgency"], "text": c["text"][:90]})
    tally("decision", decision_ok, len(cases))
    tally("junk_caught", junk_caught, junk)
    tally("need", need_ok, need_n)
    tally("owner", owner_ok, need_n)
    tally("people", people_ok, people_n)
    tally("urgent_flagged", urgent_ok, urgent_n)
    out["real_requests_wrongly_closed"] = wrongly_closed
    out["invented_counts"] = invented
    out["model_errors"] = sum(1 for r in rows if r["error"])
    out["median_seconds"] = sorted(r["seconds"] for r in rows)[len(rows) // 2]
    langs = sorted({c["lang"] for c in cases})
    out["decision_by_language"] = {l: sum(1 for c in cases if c["lang"] == l and ((by[c["id"]]["decision"] != "closed") if c["decision"] == "not_closed" else by[c["id"]]["decision"] == c["decision"])) for l in langs}
    out["cases_by_language"] = {l: sum(1 for c in cases if c["lang"] == l) for l in langs}
    return out


def gemini_read():
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        raise SystemExit("Set GOOGLE_CLOUD_PROJECT to your own authorised project before --gemini (billable).")
    gcloud = shutil.which("gcloud")
    if not gcloud:
        raise SystemExit("Install gcloud and sign in before requesting --gemini.")
    from google.oauth2.credentials import Credentials
    result = subprocess.run([gcloud, "auth", "print-access-token", "--quiet"], capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise SystemExit("Sign in with gcloud first.")
    credentials = Credentials(token=result.stdout.strip())
    del result
    os.environ.update(GOOGLE_GENAI_USE_VERTEXAI="TRUE", GOOGLE_CLOUD_PROJECT=project, GOOGLE_CLOUD_LOCATION="global",
                      LASTMILE_MODEL_FAST=os.environ.get("LASTMILE_MODEL_FAST", "gemini-3.5-flash-lite"), LASTMILE_THINKING_FAST="0")
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        os.environ.pop(name, None)
    patcher = patch("google.auth.default", return_value=(credentials, os.environ["GOOGLE_CLOUD_PROJECT"]))
    patcher.start()
    from lastmile.llm import make_llm
    llm = make_llm("gemini")
    reader = public.gemini_reader(llm)
    return (lambda text: reader(text, [])[0]), llm.model_fast


def report(results, path, title="How well the AI reads public requests"):
    lines = [f"# {title}", "",
             f"Run {results['run_at'][:16].replace('T', ' ')} UTC on {results['cases']} synthetic, labelled cases ([data](../data/benchmark/{results['data']})): "
             "English, Sinhala, Tamil and Singlish; real needs, junk, doubtful posts, danger words inside junk, injection attempts, and areas with no incident. "
             "The cases were written for this prototype and have not been reviewed by native speakers or responders. They test the reading and the decisions the app takes from it, not field performance.", "",
             "| Measure | " + " | ".join(results["readers"]) + " |", "| --- | " + " | ".join("---:" for _ in results["readers"]) + " |"]
    names = [("decision", "Right decision (file, hold, check, close)"), ("junk_caught", "Junk closed"), ("need", "Right need"), ("owner", "Right owning body"),
             ("people", "Right people count (stated or none)"), ("urgent_flagged", "Urgent cases flagged urgent")]
    for key, label in names:
        cells = []
        for reader in results["readers"]:
            m = results["scores"][reader][key]
            cells.append(f"{m['correct']}/{m['of']} ({m['rate']:.0%})" if m["of"] else "–")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    for key, label in (("real_requests_wrongly_closed", "**Real requests wrongly closed** (target 0)"), ("invented_counts", "Counts invented and kept"), ("model_errors", "Model call failures (fell back to rules)"), ("median_seconds", "Median seconds per request")):
        lines.append(f"| {label} | " + " | ".join(str(results["scores"][r][key]) for r in results["readers"]) + " |")
    lines += ["", "Code, not the model, decides what happens next: a count is kept only if its words are in the message; nothing with a danger word is closed; requests with no open incident wait for a person. Counts the reader invented were removed by that check before scoring, so \"Counts invented and kept\" measures what reached staff.", ""]
    for reader in results["readers"]:
        fails = results["scores"][reader]["failures"]
        lines += [f"## Failures: {reader} ({len(fails)})", ""]
        lines += [f"- `{f['id']}` {f['what']}: expected {f['expected']}, got {f['got']} · “{f['text']}”" for f in fails] or ["None."]
        lines.append("")
    lines += ["## Limits", "", "- Synthetic messages written by the builders, not real public requests; small sample; no photos in this run.",
              "- Sinhala and Tamil cases need native-speaker review; a few Sinhala/Tamil counts are written in words and are expected to be missed by the rules.",
              "- One run, temperature 0; model behaviour can change between versions. Re-run with `python scripts/benchmark_public.py --gemini`.", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gemini", action="store_true")
    parser.add_argument("--data", default=str(DATA))
    parser.add_argument("--title", default="How well the AI reads public requests")
    parser.add_argument("--out", default=str(ROOT / "data" / "benchmark" / "results.json"))
    parser.add_argument("--report", default=str(ROOT / "docs" / "AI_EVALUATION.md"))
    args = parser.parse_args()
    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    cases, districts = data["cases"], set(data["incident_districts"])
    readers = {"rules (offline)": lambda text: public.rules_read(text)}
    if args.gemini:
        read, model = gemini_read()
        readers[f"Gemini ({model})"] = read
    results = {"run_at": datetime.now(timezone.utc).isoformat(), "cases": len(cases), "readers": list(readers), "scores": {}, "rows": {}}
    for name, read in readers.items():
        rows = run(cases, districts, read)
        results["rows"][name] = rows
        results["scores"][name] = score(cases, rows)
        s = results["scores"][name]
        print(f"{name}: decision {s['decision']['rate']:.0%}, wrongly closed {s['real_requests_wrongly_closed']}, junk {s['junk_caught']['rate']:.0%}, need {s['need']['rate']:.0%}, people {s['people']['rate']:.0%}, urgent {s['urgent_flagged']['rate']:.0%}, errors {s['model_errors']}")
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    results["data"] = Path(args.data).name
    report(results, args.report, args.title)


if __name__ == "__main__":
    main()
