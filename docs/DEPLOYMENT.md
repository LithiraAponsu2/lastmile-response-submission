# Deploying your own exercise instance

The prototype runs a Python/FastAPI container on Google Cloud Run. Gemini runs on the Gemini Enterprise Agent Platform (formerly Vertex AI) through the attached runtime identity. An optional private Cloud Storage bucket persists cloud state. Firebase and Firestore are not currently implemented.

## Local verification first

```sh
docker build -t lastmile-response .
docker run --rm -p 8080:8080 --env-file .env.example lastmile-response
```

Open `http://localhost:8080` and `/public`. This starts a fictional exercise with the deterministic model and mail off. The container is ephemeral unless appropriate persistent state is configured.

## Own-project Cloud Run setup

1. Use a Google Cloud project/billing account you control. Enable the APIs needed for Cloud Run, Artifact Registry, the Agent Platform API (aiplatform.googleapis.com) and Cloud Storage. Verify current model/region availability in your account.
2. Build/push the supplied Docker image to your own Artifact Registry repository. Keep the Docker/upload contexts restricted by the supplied ignore files.
3. Create a runtime service account with only the model and private-bucket permissions actually needed. Use its attached identity; do not put service-account JSON, access tokens or API keys in the repository or frontend.
4. Deploy one container process with **one active writer revision and at most one instance**. Legacy caches and owner-linked cross-incident resources do not provide distributed multiwriter consistency. Do not route traffic to two writer revisions against the same state.
5. Supply environment settings from [CONFIGURATION.md](CONFIGURATION.md), using your own project, bucket and secrets. Keep `LASTMILE_ALLOW_ACTUAL=0`, developer mailbox off and real messaging disabled. Decide explicitly whether this is a disposable public fictional role demo or a private invited-account exercise.
6. In Gemini mode set `LASTMILE_LLM=gemini`, `GOOGLE_GENAI_USE_VERTEXAI=TRUE`, your `GOOGLE_CLOUD_PROJECT` and supported `GOOGLE_CLOUD_LOCATION`. Choose model IDs available to your project. Keep private setup/access secrets outside source.
7. Verify `/api/health`, workspace/public pages, saved-score endpoint, permissions, persistence across replacement and the full fictional request workflow. A successful health response alone is not an end-to-end validation.

The Docker command trusts forwarded headers for the intended Cloud Run proxy topology. Restrict ingress appropriately when adapting the image to a different hosting topology.

The public-source candidate has **no production deployment workflow, credentials or Workload Identity Federation binding**. Deployment of the hosted submission remains controlled separately; cloning this repository does not grant access to its cloud resources.

[Architecture](ARCHITECTURE.md) · [Configuration](CONFIGURATION.md) · [Testing](TESTING.md)
