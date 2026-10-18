# Judge walkthrough

Use fictional information only. The hosted role picker simulates organisations and permissions; it is not a government login or evidence of agency adoption. Submitting data to the hosted prototype changes its demo records and may trigger Gemini calls. For repeatable inspection without cloud model costs, use the local exercise instead.

1. Open the resident page and inspect English, Sinhala and Tamil views, area lookup and the request/tracking interface.
2. In an isolated exercise, submit a fictional need with a location. Inspect the request's interpretation, status and tracking code.
3. In the staff workspace, open **Public requests** in the sidebar to see requests across accessible incidents, then open the incident's **Public requests** tab. Inspect waiting, verification, filed and closed states. If clarification is requested, answer from the resident tracker and confirm the staff card/attention items receive it. The notification should open the relevant request.
4. Submit a separate need at another fictional site in the same division. Inspect any possible-duplicate prompt; retain separate requests unless a coordinator confirms the same issue. Do not equate matching categories with the same beneficiaries.
5. Ask the assistant to prepare an action using existing incident records. Inspect its evidence and linked plan, edit if needed, then approve as an authorised simulated role. Inspect the saved records rather than treating generated prose as proof of a write.
6. Inspect assignment progress, notifications, recorded outcome and the resident's staff-recorded status. Outcome recording does not demonstrate physical delivery or resident acceptance.

## Two more AI workflows

With Gemini explicitly enabled in your own authorised environment, ask for a linked letter, report-sharing action and agency request using facts already recorded in an incident. Inspect the proposed steps and shared references before approving; check each resulting record afterwards. The offline exercise tests the plumbing with a deterministic driver, so it does not demonstrate Gemini's language understanding.

For communications, use an official bulletin as the source, review the extracted warning rows, then request public messages grounded in those recorded facts. Inspect English/Sinhala/Tamil drafts for retained place names, quantities and advice before approval. No SMS/email gateway is connected, and generated language still needs native-speaker review.

## Local setup

From the complete public snapshot:

```sh
docker build -t lastmile-response .
docker run --rm -p 8080:8080 -e LASTMILE_LLM=fake -e LASTMILE_TEAM_DEMO=true -e LASTMILE_MAIL=off lastmile-response
```

Open `http://localhost:8080` and `http://localhost:8080/public`. This is an ephemeral fictional exercise with the deterministic model. The repository includes the application source and runtime inputs; consult [EVIDENCE.md](EVIDENCE.md) for completed local checks and the status of container/clean-runner verification.

[Architecture](ARCHITECTURE.md) · [AI usage](AI_USAGE.md) · [Evidence](EVIDENCE.md) · [README](../README.md)
