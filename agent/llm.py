"""Thin model wrapper so agents and tests share one interface.

The baseline (and later the agent) only ever calls `llm.parse(messages, schema)`.
Tests pass a fake; production uses OpenAILLM. Point it at Portkey (or any
OpenAI-compatible gateway) with OPENAI_BASE_URL in .env; never log API keys.
"""

from typing import Protocol

from pydantic import BaseModel

from .config import CHAT_MODEL


class LLM(Protocol):
    """Anything that can turn chat messages into a validated Pydantic object."""

    def parse(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel: ...


class OpenAILLM:
    """OpenAI structured-output client. Reads OPENAI_API_KEY and optional OPENAI_BASE_URL from the environment."""

    def __init__(self, model=CHAT_MODEL, client=None):
        from dotenv import load_dotenv

        load_dotenv()
        if client is None:
            from openai import OpenAI

            client = OpenAI()  # picks up OPENAI_API_KEY / OPENAI_BASE_URL itself
        self.client, self.model, self.last_usage = client, model, None

    def parse(self, messages, schema):
        """One model call. Raises ValueError on a refusal or an empty parse, so callers can fail safe."""
        resp = self.client.chat.completions.parse(model=self.model, messages=messages, response_format=schema)
        self.last_usage = getattr(resp, "usage", None)
        message = resp.choices[0].message
        if getattr(message, "refusal", None):
            raise ValueError(f"model refused: {message.refusal}")
        if message.parsed is None:
            raise ValueError("model returned no parsed output")
        return message.parsed
