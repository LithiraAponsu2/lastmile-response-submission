# Submission materials — team MOB

**LastMile Response** · Sustainability & Social Impact · Lithira Aponsu and Pulsara Aponsu.

| Deliverable | Link / status |
|---|---|
| Working prototype | [Cloud Run workspace](https://lastmile-alert-74y2iwqhzq-el.a.run.app/) · [Resident page](https://lastmile-alert-74y2iwqhzq-el.a.run.app/public) |
| Relevant source | [Public curated submission repository](https://github.com/LithiraAponsu2/lastmile-response-submission): application source, runtime assets, tests and evaluation documentation. Research, private records, original development history, PPTX and video files are excluded. |
| Architecture | [System, AI, workflow and approval diagrams](ARCHITECTURE.md) |
| Video | [LastMile Response — Team MOB](https://www.youtube.com/watch?v=t2vgS3u6gh8) · **3:23**, within the supplied 3–4 minute requirement. |
| Pitch deck | [Prescribed-template presentation (PDF)](LastMile-Response-presentation.pdf), 20 slides. The editable PPTX is not published. |
| Evidence | [Saved AI benchmark and candidate validation](EVIDENCE.md) |

## Brief description

LastMile Response connects public requests for disaster assistance with a shared workspace for participating organisations. Residents provide a description, location and optional photos and can track staff updates. Gemini on Google's Gemini Enterprise Agent Platform (formerly Vertex AI) interprets requests, supports evidence-grounded planning through a Google ADK coordinator, and drafts letters and multilingual messages. Code enforces routing, scope, quantities and human approval of operational plans. Cloud Run hosts the application; private Cloud Storage supports persisted cloud records. Firebase and Firestore are not currently used.

The prototype uses fictional operations. It does not establish real agency adoption, dispatch, delivery, payment or resident-confirmed resolution. See the [judge walkthrough](JUDGE_GUIDE.md), [AI limits](AI_USAGE.md) and [data notices](../THIRD_PARTY_NOTICES.md).

YouTube's anonymous page and public metadata were checked on 18 October 2026: the title matches the submission, duration is 203 seconds and playability is reported as OK. The complete recording's functionality/content was not reviewed in this check. Keep the recording accessible throughout evaluation.

Publication does not establish agency endorsement or resolve the documented upstream rights/provenance questions. Accepted competition terms remain applicable; no repository notice overrides them.
