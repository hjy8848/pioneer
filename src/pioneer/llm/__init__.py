"""Single LLM entry point for all synthesis roles (PLAN common rule 5).

TODO(PLAN Step 2): litellm-based client; model/temperature/seed from config;
every response cached on disk under data/cache/llm keyed by a hash of
(model, messages, params); a MockLLM for tests (no network in pytest).
"""
