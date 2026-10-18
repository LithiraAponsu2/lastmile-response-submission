# Submission materials — team MOB

**LastMile Response** · Sustainability & Social Impact · Lithira Aponsu and Pulsara Aponsu.

| Deliverable | Link / status |
|---|---|
| Working prototype | [Cloud Run workspace](https://lastmile-alert-74y2iwqhzq-el.a.run.app/) · [Resident page](https://lastmile-alert-74y2iwqhzq-el.a.run.app/public) |
| Relevant source | [Submission repository](https://github.com/LithiraAponsu2/lastmile-response-submission); currently a private preparation repository, so public-source submission requirements are not yet complete. |
| Architecture | [System, AI, workflow and approval diagrams](ARCHITECTURE.md) |
| Video | Actual accessible 3–4 minute functionality demonstration URL pending. The prototype URL is not a recording. |
| Pitch deck | Final prescribed-template PDF pending; editable PPTX deliberately withheld/ignored. |
| Evidence | [Saved AI benchmark and candidate validation](EVIDENCE.md) |

## Brief description

LastMile Response connects public requests for disaster assistance with a shared workspace for participating organisations. Residents provide a description, location and optional photos and can track staff updates. Gemini on Vertex AI interprets requests, supports evidence-grounded planning through a Google ADK coordinator, and drafts letters and multilingual messages. Code enforces routing, scope, quantities and human approval of operational plans. Cloud Run hosts the application; private Cloud Storage supports persisted cloud records. Firebase and Firestore are not currently used.

The prototype uses fictional operations. It does not establish real agency adoption, dispatch, delivery, payment or resident-confirmed resolution. See the [judge walkthrough](JUDGE_GUIDE.md), [AI limits](AI_USAGE.md) and [data notices](../THIRD_PARTY_NOTICES.md).

Public release still requires rights/provenance clearance and final video/PDF links. Accepted competition terms remain applicable; no repository notice overrides them.
