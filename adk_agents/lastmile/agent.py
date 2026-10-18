"""Entry point for `adk web adk_agents` / `adk run adk_agents/lastmile` / `adk api_server adk_agents`.

Env: LASTMILE_PACK (default lk-landslide), LASTMILE_LLM=fake|gemini, LASTMILE_MODEL_FAST / LASTMILE_MODEL_VERIFIER.
Put the bulletin text into session state key `source_text` (the web UI "State" tab, or the API's `state` field).
"""

from lastmile.adk_app import build_root_agent

root_agent = build_root_agent()
