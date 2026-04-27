"""LLM call surface — LiteLLM wrapper that records every call to llm_call."""

from meridian.llm.client import LlmCall, call_llm

__all__ = ["LlmCall", "call_llm"]
