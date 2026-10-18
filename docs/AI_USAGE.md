# Where AI contributes

The deployed design uses **Gemini on Vertex AI**, with a **Google ADK coordinator** for conversational tools, served by **Cloud Run**. Private **Cloud Storage** persists cloud state. Firebase and Firestore are not currently used. Model IDs/configuration must be recorded with the final release; the stored intake benchmark identifies its tested model separately.

[![AI architecture showing the structured public reader, scoped ADK model-tool loop and human-approved execution boundary.](assets/ai-architecture.svg)](assets/ai-architecture.svg)

| Task | Model contribution | What application code / people retain |
|---|---|---|
| Public request intake | Interpret free-form multilingual messages and optional photos into typed need, urgency, count quotation, summary, doubt and questions. | Validate/limit inputs, check count evidence, apply triage/routing rules, persist request and restrict photo access. A model interpretation is not verified evidence. |
| Incident assistance | Read scoped dashboard/evidence/history through tools and prepare linked actions from ordinary language. | Server-side actor/incident scope; tools cannot grant permissions or self-approve. |
| Action plans | Propose multiple linked steps for a person to inspect/edit. | Bind reviewed content to actor/incident/version, validate commands and recheck approval; reject stale/altered proposals. |
| Communications | Draft letters, factual notes and English/Sinhala/Tamil public messages from supplied facts/templates. | Quantity/fact guards, explicit human review and application-specific publication rules. Drafting is not real SMS/email delivery. |

Structured language interpretation helps with expressions that keyword matching misses; conversational tools connect those interpretations to existing records. The public text benchmark supplies limited comparative evidence for intake. It does not measure every row of this table. [Saved evidence](EVIDENCE.md).

## What makes a proposed action executable

[![A reviewed plan must pass current server checks before commands apply; stale, expired or changed plans require refresh and renewed review.](assets/approval-and-recovery.svg)](assets/approval-and-recovery.svg)

The model-tool loop is bounded to 12 model calls per turn. Read tools remain incident/actor scoped. Action tools prepare typed steps rather than approve them. A linked plan carries content/version/expiry checks; execution rechecks the approving actor and domain rules. Drafting and translation calls are separate from the coordinator's decision loop, so that loop's 12-call budget should not be represented as a global bound on every possible model request in the application.

## Two different execution modes

- **Gemini:** authorised server identity calls Vertex AI. Model calls can incur cost; input/model/latency evidence must identify the actual configuration.
- **Offline:** a labelled deterministic driver exercises the ADK function loop and rules. It supports reproducible demonstrations/tests, but is not Gemini inference or a claim of equivalent language understanding.

If the public reader fails or reaches its model limit, the application can use its rules fallback. Fallback behaviour requires its own safety tests. No guarantee of zero hallucinations, perfect triage, safe emergency advice or certified translations is made.

## Source entry points

`lastmile/public_requests.py` — structured intake and code triage.

`lastmile/coordinator.py` and `lastmile/team_agent.py` — ADK execution and incident-scoped tools.

`lastmile/team_plans.py` — linked plans and approval checks.

`lastmile/team_drafting.py`, `lastmile/team_messages.py`, `lastmile/llm.py` — drafting, translation and model adapter.

## Honest boundaries

Uploaded reports, photos and retrieved text are untrusted inputs. Prompt instructions alone do not prove safety; domain checks and independent tests matter. Photo interpretation and native-language quality have not received a comparable benchmark/practitioner review. A proposed route is not a completed agency handoff, and staff-recorded completion is not resident confirmation.

Claude and Codex assisted development/documentation under the team's direction; this disclosure is separate from the Gemini features running inside the product. Copyright/provenance and third-party licensing must be checked before public release.

[Architecture](ARCHITECTURE.md) · [Evidence](EVIDENCE.md) · [README](../README.md)
