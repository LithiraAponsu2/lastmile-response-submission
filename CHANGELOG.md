# Public-candidate changes

## Submission update · 18 October 2026

- README rewritten for evaluators: the problem and product, screens from the recorded exercise, how each Google service is used, the AI chat-turn and public-request flows, guardrails, application architecture, benchmark, measured and estimated cost, and how to try it. Video link: https://www.youtube.com/watch?v=t2vgS3u6gh8.
- Adds the prescribed-template presentation as `docs/LastMile-Response-presentation.pdf`.
- Names the model platform as Google's Gemini Enterprise Agent Platform (formerly Vertex AI) across the docs and the system diagram.
- Removes the GitHub Actions workflow; the documented local checks remain (docs/TESTING.md). The release manifest is regenerated for the changed files.

## Application sync — 18 October 2026

- Updates global and incident public-request boards, request-specific attention/notification links and staff-visible progress.
- Includes the dashboard scroll-boundary correction and its desktop/phone regression, readable organisation names and the Galle Four Gravets display-name correction while retaining saved location IDs.
- Updates the judge walkthrough and README; retains submission-specific offline defaults, rights notices and source-only CI. Research, runtime records, presentation files and video files remain excluded.
- Makes the activity/composer browser regression wait for Refresh to replace the form before measuring it, avoiding a stale-element timing failure. Development CI now selects all browser tests, matching this candidate's coverage rule.
- See [EVIDENCE.md](docs/EVIDENCE.md) for the validation of this update; earlier results remain identified with their original snapshot.

## Submission snapshot — 18 October 2026

- Includes the resident request/tracking and organisation coordination implementation, AI tools, notifications, frontend source, tests, runtime reference assets and disclosed saved benchmark evidence.
- Replaces development/research documentation with a judge-focused README, system/AI/workflow/approval diagrams and concise setup/evidence documentation.
- Excludes competitor research, original Git history, local runtime state, production deployment automation, unused screenshots and editable PPTX files.
- Adds restricted first-party evaluation terms while retaining upstream notices and previously granted rights; public-release rights/provenance checks remain explicit.
- Changes the standalone model smoke utility to offline mode by default; Gemini requires explicit opt-in.
- Removes the benchmark utility's implicit private-project fallback; billable mode requires an explicit own-project setting before authentication/model access.

Fresh local candidate checks: 495 backend, 90 ADK and 36 browser tests passed (621 total), plus the pinned UI bundle check. Clean-runner/container CI status and test limitations are recorded separately in [docs/EVIDENCE.md](docs/EVIDENCE.md).

This is an export boundary, not a claim that development began on this snapshot date. The original development history is preserved separately. Validation of this candidate is recorded in [docs/EVIDENCE.md](docs/EVIDENCE.md).
