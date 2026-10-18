"""Provider cutoffs must not be presented as completed replies."""
import asyncio

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from lastmile.coordinator import run_coordinator


def test_output_limit_is_visible_and_does_not_succeed(monkeypatch):
    monkeypatch.setenv("LASTMILE_THINKING_FAST", "0")

    class LimitedModel(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            assert llm_request.config.thinking_config.thinking_budget == 0
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="The pending assignment requires")]),
                finish_reason=types.FinishReason.MAX_TOKENS,
                usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=20, candidates_token_count=5, thoughts_token_count=10))

    class Tools:
        tool_names = ()
        agent_instruction = "Answer briefly."
        def build_context(self, text):
            return {"message": text}
        def progress(self, **step):
            pass
        def final_answer(self, text):
            return text

    result = asyncio.run(run_coordinator(Tools(), "Summarise", "fake", model_override=LimitedModel(model="output-limit-test")))
    assert result["failed"] and result["finish_reason"] == "MAX_TOKENS"
    assert "partial reply is incomplete" in result["text"]
    assert result["usage"]["thinking_tokens"] == 10 and result["usage"]["cached_tokens"] == 0
