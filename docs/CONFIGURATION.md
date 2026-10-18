# Configuration

The application reads environment variables; it does not automatically load a `.env` file. Pass `.env.example` explicitly to Docker or set environment variables in your shell. Do not commit real environment files.

## Offline fictional exercise

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-e2e.txt
$env:LASTMILE_LLM = "fake"
$env:LASTMILE_TEAM_DEMO = "true"
$env:LASTMILE_MAIL = "off"
$env:LASTMILE_DATA_DIR = "var/local-exercise"
.venv\Scripts\python.exe -m uvicorn lastmile.api:app_factory --factory --host 127.0.0.1 --port 8080
```

Open `/` for the staff exercise and `/public` for resident requests/status. The role picker is deliberately fictional; selecting a role is not authentication as a real agency. Offline ADK uses a deterministic tool driver, not natural-language Gemini reasoning. Use the [judge guide](JUDGE_GUIDE.md).

## Invited-account exercise

Disable `LASTMILE_TEAM_DEMO` and use a separate empty data directory. First-administrator setup is allowed directly on loopback when no setup key is configured. Remote first setup requires your own private `LASTMILE_TEAM_SETUP_KEY` (falling back to `LASTMILE_API_KEY` where configured). Create the administrator through the setup screen, then issue scoped one-use invitations from Members. Do not share the setup key with ordinary invited users. Password/session/invitation state is private and must never enter Git.

## Main settings

| Variable | Purpose |
|---|---|
| `LASTMILE_LLM` | `fake` (offline) or explicit `gemini`. |
| `LASTMILE_TEAM_DEMO` | Optional anonymous fictional role simulation; normally off. |
| `LASTMILE_DATA_DIR` | Local private persistent state; app-factory default is `var`. |
| `LASTMILE_STORAGE_BUCKET` | Your own private cloud state bucket; omit for local storage. |
| `LASTMILE_TEAM_SETUP_KEY`, `LASTMILE_API_KEY` | Private setup/legacy API gate settings; never public browser credentials. |
| `LASTMILE_ALLOW_ACTUAL` | Keep `0`; real operational authority is not supplied by a flag. |
| `LASTMILE_MAIL` | `off` for isolated exercises; legacy mail configuration does not implement hub dispatch. |
| `LASTMILE_DEV_MAILBOX` | Keep false on hosted instances. |
| `LASTMILE_DRAFTING` | Warning drafting mode, `templates` or `model`. |
| `LASTMILE_MODEL_FAST`, `LASTMILE_MODEL_VERIFIER` | Available model IDs chosen for your own project. |
| `LASTMILE_RATE_LIMIT_PER_MIN`, `LASTMILE_PUBLIC_RATE_LIMIT_PER_MIN` | Request controls; these are not billing guarantees. |

## Optional Gemini through the Gemini Enterprise Agent Platform (Vertex AI API)

Set `LASTMILE_LLM=gemini`, `GOOGLE_GENAI_USE_VERTEXAI=TRUE`, your `GOOGLE_CLOUD_PROJECT` and a supported `GOOGLE_CLOUD_LOCATION`. Use authorised Application Default Credentials locally or the attached runtime identity on Cloud Run. Verify model availability/permissions and expected costs first. No private project/bucket IDs or credentials are supplied by this source release.

The coordinator and reader use the configured model adapter; drafting/translation can use a separate model. See [AI_USAGE.md](AI_USAGE.md). Configure one active writer process/revision/instance until the legacy/cross-incident persistence constraints are replaced and evaluated. Local state uses SQLite/files; cloud records use the private object adapter. See [DEPLOYMENT.md](DEPLOYMENT.md).

## UI development

Node 22 is used for the UI bundle. Run `npm ci --ignore-scripts`, then `npm run build:ui` after changing authored components and `npm run check:ui` to verify committed bundles/icons/notices. Python serves the committed assets without a Node runtime or UI CDN.
