# Evidence available to judges

This page separates saved model results from fresh candidate validation. The source candidate was tested locally on 18 October 2026 with fake/mocked models and isolated fictional state; no new billable model calls or cloud submissions were made.

## Candidate validation — 18 October 2026

The initial source candidate is commit `c54d20d`. The checks below exercised its application/test content; the subsequent evidence/manifest update changes documentation/metadata only.

| Check | Fresh local result |
|---|---|
| Backend/API/domain suite, excluding browser files and separate ADK file | **495 passed**, Python 3.12.6; 554.69 seconds. |
| ADK equivalence suite | **90 passed**; 438.84 seconds. Uses deterministic/stubbed models, including local mock HTTP paths. |
| Complete selected browser suite | **36 passed**, 585 non-browser cases deselected; Chrome/Playwright, 442.59 seconds. |
| Total non-overlapping selected tests | **621 passed**. |
| Pinned UI dependency installation and committed bundle/icon/notice consistency | `npm ci --ignore-scripts` and `npm run check:ui` passed. |
| Installed Python environment | `pip check` passed; local interpreter imported `lastmile` from this candidate, not the development checkout. |
| Candidate document links and source isolation | Relative Markdown links resolved; only the 304 manifest-selected paths were committed/uploaded. No private history, research, state, dependencies or PPTX. |
| Secret review | Gitleaks 8.30.1 scanned files/Git history. Three generic detections were verified source-file SHA-256 values in the release manifest; zero unresolved findings. This is not a guarantee that every possible secret format is detectable. |
| Optional billable benchmark safety | Missing explicit `GOOGLE_CLOUD_PROJECT` is refused before credential/model access. |

The backend/browser runs reported a Starlette/httpx deprecation warning; the ADK run reported 205 SDK deprecation warnings. These were warnings, not test failures. No fresh native-speaker/practitioner/photo-quality study or live authenticated cloud resolution exercise was performed.

The local Docker engine was unavailable. [GitHub CI](https://github.com/LithiraAponsu2/lastmile-response-submission/actions/runs/39125787704) performs clean-runner UI, Python 3.12/3.14, browser and container checks; at the time this evidence was written the UI job had passed and the remaining jobs were queued. Do not describe queued jobs as passed. CI has no production credentials or deployment permission.

The local test runs reused the development virtual environment's installed dependencies while executing the isolated candidate code. Clean-runner CI is the separate dependency/setup check; the local run is not described as a fresh dependency installation from scratch.

## Saved held-out text benchmark

24 builder-authored synthetic messages, run once on 18 October 2026, then re-scored after the closing-rule correction. Stored model: `gemini-3.5-flash-lite`. Saved inputs/results remain in the approved source snapshot; do not substitute a previous percentage for the corrected result.

| Measure | Rules | Gemini |
|---|---:|---:|
| Correct triage decision | 19/24 | 22/24 |
| Correct need classification | 15/19 | 19/19 |
| Real requests incorrectly closed in this set | 0 | 0 |

Sources to retain in the public snapshot: `data/benchmark/public_requests_holdout.json`, `data/benchmark/results-holdout.json`, `scripts/benchmark_public.py`, and the rewritten `docs/AI_EVALUATION_HOLDOUT.md`. The application exposes saved score summaries at `/api/public/ai-quality`.

This is a small synthetic text test, not an independent study. The routing-body metric derives from need classification and the owner table; it does not measure completed handoffs. Photos, translation quality, coordinator plans and real-world response outcomes are outside this benchmark. No false closures observed in these cases is not proof of a zero false-closure rate generally.

## Implementation and workflow checks

The development deployment ledger records an earlier 620-pass API/browser run and accessibility checks for its notifications release. Those historical counts are distinct from the fresh 621-pass candidate checks above. A new candidate-wide axe accessibility audit was not run here.

The candidate must verify request intake, clarifications reaching staff, distinct-site completion remaining separate, filing permissions, district selection, notifications, role-scoped plans, UI bundle consistency, Docker startup and the saved score endpoint. All local tests should use fictional isolated state and fake models/mail off unless a deliberate billable evaluation is documented.

## Impact still to establish

Fewer missed requests, quicker coordination and better resource visibility are intended benefits. No practitioner pilot, measured reduction in response time, agency adoption, native-speaker certification or verified emergency outcome is claimed. A resident sees staff-recorded status; independent resident confirmation/reopening is not established by the current evidence.

[Judge walkthrough](JUDGE_GUIDE.md) · [AI usage](AI_USAGE.md) · [README](../README.md)
