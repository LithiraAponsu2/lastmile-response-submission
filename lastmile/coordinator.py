"""One bounded ADK coordinator; tools read or propose, never approve."""
from __future__ import annotations

import asyncio
import json
import os
import time

from .workspace import WorkspaceError
from .llm import DEFAULT_MODEL_FAST, thinking_budget


TOOL_LABELS = {
    "read_dashboard": "Reading your organisation's incident picture", "search_evidence": "Finding source passages and provenance",
    "read_incident_history": "Reading the incident history",
    "prepare_action": "Checking permissions, evidence and proposed change",
    "find_places": "Finding the places and organisations named",
    "draft_public_message": "Drafting the public message from its template",
    "create_request": "Preparing the request to another organisation",
    "record_area_status": "Preparing the service status",
}


async def run_coordinator(tools, text, mode, model_override=None):
    """ADK sessions are per-turn execution buffers; durable domain history is rehydrated.

    No process-local session is the source of truth. Only final text and bounded tool
    metadata are retained; private reasoning/provider credentials are not stored.

    `tools` (team_agent.TeamTools) supplies tool_names, agent_instruction, build_context,
    offline_call, final_answer and progress, plus the trace/proposal/citation lists.
    """
    from google.adk.agents import LlmAgent
    from google.adk.agents.run_config import RunConfig
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    class OfflineToolModel(BaseLlm):
        """Labelled deterministic exercise driver through the SAME ADK function loop.

        tools.offline_call lists the calls a model would make; one is played per model step,
        and a failed call ends the turn with its error."""
        async def generate_content_async(self, llm_request, stream=False):
            done = [p.function_response.response for c in llm_request.contents for p in c.parts or [] if p.function_response]
            said = [p.text for c in llm_request.contents if c.role == "user" for p in c.parts or [] if p.text]
            calls = tools.offline_call(json.loads(said[-1])["message"])
            if len(done) < len(calls) and not (done and done[-1].get("failed")):
                name, args = calls[len(done)]
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]))
                return
            payload = done[-1] if done else {}
            answer = payload.get("answer") if payload.get("failed") else tools.offline_answer(payload)
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=str(answer))]))

    if mode == "gemini" and model_override is None and os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").casefold() != "true":
        raise RuntimeError("The coordinator requires configured Vertex AI identity.")
    model_name = os.environ.get("LASTMILE_MODEL_FAST", DEFAULT_MODEL_FAST)
    model = model_override or (OfflineToolModel(model="offline-exercise-tools") if mode == "fake" else model_name)
    functions = [getattr(tools, n) for n in tools.tool_names]

    async def before_tool(tool, args, tool_context):
        # Convert expected tool-domain errors into focused responses, not loop failures.
        if len(tools.trace) >= 16:
            return {"answer": "Tool limit reached. Ask a smaller follow-up question."}
        tools.progress(kind="tool", label=TOOL_LABELS.get(tool.name, tool.name.replace("_", " ").capitalize()), status="running", step_id=tool_context.function_call_id)
        return None

    async def tool_error(tool, args, tool_context, error):
        tools.progress(kind="tool", label=TOOL_LABELS.get(tool.name, tool.name.replace("_", " ").capitalize()), status="failed", step_id=tool_context.function_call_id)
        # Domain refusals are the app's own wording, safe to keep; other errors keep only their class.
        tools.tool_results.append({"tool": tool.name, "status": "failed", "argument_fields": sorted(args), "error": "domain_validation" if isinstance(error, WorkspaceError) else "tool_unavailable",
                                   "message": str(error)[:300] if isinstance(error, WorkspaceError) else type(error).__name__})
        return {"answer": str(error) if isinstance(error, WorkspaceError) else "That tool could not complete the request. Check the record and try a focused question.", "failed": True}

    async def after_tool(tool, args, tool_context, tool_response):
        tools.progress(kind="tool", label=TOOL_LABELS.get(tool.name, tool.name.replace("_", " ").capitalize()), status="failed" if tool_response.get("failed") else "completed", step_id=tool_context.function_call_id)
        tools.tool_results.append({"tool": tool.name, "status": "failed" if tool_response.get("failed") else "returned",
            "argument_fields": sorted(args), "record_references": {k: v for k, v in args.items() if k.endswith("_id")},
            "step_count": len(tools.steps), "citation_ids": [c["source_id"] for c in tools.citations]})
        return None

    model_step = 0

    async def before_model(callback_context, llm_request):
        nonlocal model_step
        model_step += 1
        tools.progress(kind="model", label="Choosing the next step" if model_step == 1 else "Reviewing tool results", status="running", step_id=f"model-{model_step}")

    async def after_model(callback_context, llm_response):
        tools.progress(kind="model", label="Choosing the next step" if model_step == 1 else "Reviewing tool results", status="completed", step_id=f"model-{model_step}")

    thinking = thinking_budget("LASTMILE_THINKING_FAST")
    agent = LlmAgent(name="incident_coordinator", model=model, instruction=tools.agent_instruction, tools=functions,
                     before_tool_callback=before_tool, after_tool_callback=after_tool, on_tool_error_callback=tool_error,
                     before_model_callback=before_model, after_model_callback=after_model,
                     generate_content_config=types.GenerateContentConfig(temperature=0, max_output_tokens=4096,
                         thinking_config=types.ThinkingConfig(thinking_budget=thinking) if thinking is not None else None))
    runner = InMemoryRunner(agent=agent, app_name="lastmile_incident")
    session = await runner.session_service.create_session(app_name="lastmile_incident", user_id="scoped_operator")
    context = tools.build_context(text)
    started = time.monotonic()
    answer, usage = "", {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cached_tokens": 0}
    finish_reason, incomplete = None, False
    try:
        async with asyncio.timeout(75):
            async for event in runner.run_async(user_id="scoped_operator", session_id=session.id,
                    new_message=types.Content(role="user", parts=[types.Part(text=json.dumps(context))]), run_config=RunConfig(max_llm_calls=12)):
                if event.usage_metadata:
                    usage["input_tokens"] += event.usage_metadata.prompt_token_count or 0
                    usage["output_tokens"] += event.usage_metadata.candidates_token_count or 0
                    usage["thinking_tokens"] += event.usage_metadata.thoughts_token_count or 0
                    # Gemini reuses the unchanged start of a prompt (instruction, tool declarations): implicit caching.
                    usage["cached_tokens"] += event.usage_metadata.cached_content_token_count or 0
                if event.is_final_response() and event.content:
                    answer = "\n".join(p.text for p in event.content.parts or [] if p.text and not p.thought)
                    finish_reason = getattr(event.finish_reason, "value", event.finish_reason)
                    incomplete = bool(event.error_code or event.interrupted or finish_reason not in (None, "STOP"))
    finally:
        await runner.close()
    usage["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    answer = tools.final_answer(answer)
    if incomplete:
        answer = "The model reply was interrupted or reached its output limit. This partial reply is incomplete; inspect saved records and any pending proposal before retrying.\n\n" + answer
    return {"text": answer or "The turn ended without a final answer. Inspect any pending proposal before retrying.",
            "failed": incomplete or not bool(answer), "finish_reason": finish_reason, "usage": usage, "model": "offline-exercise-tools" if mode == "fake" else model_name,
            "runtime": "adk-offline" if mode == "fake" else "adk-vertex"}
