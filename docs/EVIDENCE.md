# Evidence available to judges

This page separates saved model results from fresh candidate validation. The source candidate was tested locally on 18 October 2026 with fake/mocked models and isolated fictional state; no new billable model calls or cloud submissions were made.

## Application sync verification — 18 October 2026

The updated tree includes global/incident public-request boards, request-specific notifications and progress, corrected organisation/location labels, and the scroll-boundary regression. Checks ran against this candidate's code with Python 3.12.6, fake/mocked models, disabled mail and isolated fictional local state.

| Check | Fresh result for the synced application |
|---|---|
| Backend/API/domain suite, excluding browser files and the unchanged ADK file | **496 passed**, 525.03 seconds. |
| Full browser suite | **37 passed**, 586 non-browser tests deselected, 421.53 seconds. Includes public-request boards, clarification/duplicate handling, notifications and desktop/phone scroll boundaries. |
| Activity/Refresh regression after its test synchronization correction | **3 passed**, 24.47 seconds; these overlap the full browser suite and are not added to its total. |
| UI bundle/icon/notice consistency | `npm run check:ui` passed. |
| Export boundary and judge-document links | **305 allowlisted files**; relative judge-document links resolve. No research, private history, runtime records, credentials, dependencies, PPTX or video files are included. |
| Exact-tree secret scan | Gitleaks 8.30.1: three detections verified as source-file SHA-256 values in the manifest; zero unresolved findings. |

**533 non-overlapping backend/browser tests passed.** The unchanged 90-case ADK suite was not rerun locally for this sync; its earlier results remain below. Both full runs reported one Starlette/httpx deprecation warning. The initial sandbox attempts were blocked by Windows process/temporary-pipe permissions; the permitted isolated reruns supply the results above. This does not establish field effectiveness or fresh model/language/photo quality.

The activity test now waits for Refresh to replace the old composer and checks one visible rectangle atomically. The full browser run collected the previous test before that correction; the separate three-test rerun validates the final test version. [GitHub Actions](https://github.com/LithiraAponsu2/lastmile-response-submission/actions) performs the updated snapshot's clean-runner checks; a pending run is not counted as passed here.

## Initial candidate validation — 18 October 2026

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

The local Docker engine was unavailable. The initial snapshot's [GitHub CI](https://github.com/LithiraAponsu2/lastmile-response-submission/actions/runs/39125787704) subsequently **passed all five jobs**: UI, Python 3.12, Python 3.14, browser and container. This completed clean-runner result applies to that snapshot; the application sync below requires its own checks. CI has no production credentials or deployment permission.

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
