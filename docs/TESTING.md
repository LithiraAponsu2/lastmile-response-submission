# Reproducing checks

Use Python 3.12 or the container's Python 3.14; install `requirements-e2e.txt` for the full local suite. Set `PYTHONUTF8=1` on Windows. Tests should use fake/mocked models and isolated fictional state; do not supply cloud credentials or real deployment targets.

```sh
python -m pip install -r requirements-e2e.txt
python -m pytest tests --ignore=tests/test_adk.py --ignore-glob='*browser.py'
python -m pytest tests/test_adk.py
python -m playwright install chromium
```

Set `LASTMILE_E2E_CHROMIUM` to the installed Chromium/Chrome executable, then run:

```sh
python -m pytest tests -k browser
npm ci --ignore-scripts
npm run check:ui
python scripts/live_smoke.py --llm fake
python -m lastmile.eval --out var/eval-report.md
```

Browser tests start local subprocess servers and use private per-test state under `var/`. Never commit that state or screenshots containing real records. `scripts/audit_team_accessibility.py` requires an explicitly supplied official local axe script, output directory and optionally browser path; its isolated test context does not weaken the deployed application CSP.

The container CI job builds the supplied Dockerfile, waits for `/api/health`, checks packs and executes `scripts/smoke_http.py` against the local fake-model container. It has no production cloud identity or deployment step.

For public-intake comparison, `scripts/benchmark_public.py` defaults to rules/offline. Use an output under `var/` for new runs so committed evidence is not overwritten. `--gemini` is a separate billable evaluation requiring deliberate configuration; a fresh run must disclose model, date, inputs and limitations. See [AI_EVALUATION.md](AI_EVALUATION.md), [AI_EVALUATION_HOLDOUT.md](AI_EVALUATION_HOLDOUT.md) and [EVIDENCE.md](EVIDENCE.md).

Report exact candidate results and skips, not historical development totals. The saved synthetic text benchmark does not validate photos, language quality, coordinator planning or field effectiveness.
