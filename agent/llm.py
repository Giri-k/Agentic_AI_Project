import os
from functools import lru_cache
from typing import Protocol

from openai import OpenAI
from pydantic import BaseModel

from .config import CHAT_MODEL, EMBED_MODEL, PORTKEY_BASE_URL


@lru_cache(maxsize=1)
def client():
    """One shared OpenAI client pointed at Portkey."""
    key = os.getenv("PORTKEY_API_KEY")
    if not key:
        raise RuntimeError("PORTKEY_API_KEY is not set; add it to .env in the repo root")
    return OpenAI(api_key=key, base_url=PORTKEY_BASE_URL)


_shared_client = client  # OpenAILLM's `client` argument shadows the function name


def embed(texts):
    """Embed a list of texts in one request; returns one 1,536-number vector per text, in order."""
    response = client().embeddings.create(model=EMBED_MODEL, input=list(texts))
    return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]


class LLM(Protocol):
    """Anything that can turn chat messages into a validated Pydantic object."""

    def parse(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel: ...


class OpenAILLM:
    """Structured-output chat client. Uses the shared Portkey client unless a client is passed in."""

    def __init__(self, model=CHAT_MODEL, client=None):
        self.client = client if client is not None else _shared_client()
        self.model, self.last_usage = model, None

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
