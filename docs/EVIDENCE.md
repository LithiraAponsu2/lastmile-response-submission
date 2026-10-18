# Evidence available to judges

This page separates saved model results, implementation evidence and planned validation. No new model calls or application test runs were performed while preparing this README package.

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

The development deployment ledger records a 620-pass API/browser run and accessibility checks for its notifications release. Those historical counts must not be presented as tests freshly run on the trimmed public candidate. Before publication, record the candidate commit, commands, dependency versions, result totals and any remaining failures here.

The candidate must verify request intake, clarifications reaching staff, distinct-site completion remaining separate, filing permissions, district selection, notifications, role-scoped plans, UI bundle consistency, Docker startup and the saved score endpoint. All local tests should use fictional isolated state and fake models/mail off unless a deliberate billable evaluation is documented.

## Impact still to establish

Fewer missed requests, quicker coordination and better resource visibility are intended benefits. No practitioner pilot, measured reduction in response time, agency adoption, native-speaker certification or verified emergency outcome is claimed. A resident sees staff-recorded status; independent resident confirmation/reopening is not established by the current evidence.

[Judge walkthrough](JUDGE_GUIDE.md) · [AI usage](AI_USAGE.md) · [README](../README.md)
