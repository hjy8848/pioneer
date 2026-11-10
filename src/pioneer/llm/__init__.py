"""[INDEPENDENT] Shared LLM entry point for synthesis roles."""
from .client import LiteLLMClient, MockLLM

__all__ = ["LiteLLMClient", "MockLLM"]
