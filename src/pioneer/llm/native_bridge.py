"""[INDEPENDENT] Route native tau2 participants through our cached LLM boundary.

Temporary, process-local adapters leave upstream source untouched. This scoped
bridge is used by the sequential verifier, not concurrent native simulations.
"""
import json
from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def native_generation(client):
    from tau2.data_model.message import AssistantMessage, ToolCall
    from tau2.utils.llm_utils import to_litellm_messages

    def generate(model, messages, tools=None, **kwargs):
        if model != client.params["model"]:
            raise ValueError("all participants must use the shared configured model")
        response = client.chat(to_litellm_messages(messages),
                               [t.openai_schema for t in tools] if tools else None,
                               seed=kwargs.get("seed", client.params["seed"]))
        calls = [ToolCall(id=c["id"], name=c["function"]["name"],
                          arguments=json.loads(c["function"]["arguments"]))
                 for c in response.get("tool_calls", [])]
        record = client.records[-1] if client.records else {}
        return AssistantMessage(role="assistant", content=response.get("content"),
            tool_calls=calls or None, cost=record.get("cost_usd"), usage=record.get("usage"))

    with patch("tau2.agent.llm_agent.generate", generate), patch("tau2.user.user_simulator.generate", generate):
        yield
