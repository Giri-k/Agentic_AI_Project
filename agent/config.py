"""Paths, model names and per-ticket limits. Limits live here, not in policy text (POL-SUP-01)."""

import os
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "synthetic"
POLICY_DIR = REPO / "policies"
RUNTIME = REPO / "runtime"  # working store, outbox, checkpoints, traces

load_dotenv(REPO / ".env")  # PORTKEY_API_KEY lives only in the git-ignored .env
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"
CHAT_MODEL = os.getenv("OPENAI_MODEL", "@30875-fall26-openai/gpt-5-nano")
EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "@30875-fall26-openai/text-embedding-3-small")

# Per ticket, counted across resumes.
MAX_MODEL_CALLS = 4
MAX_TOOL_CALLS = 6
MAX_SCHEMA_REPAIRS = 1
MAX_REVISIONS = 1
MAX_RETRIES = 1          # one retry for a temporary tool error
IDENTICAL_CALL_STOP = 3  # the third identical call stops the run

KB_MAX_K = 3
KB_MAX_CHARS = 4000
# Best score below these marks a search as weak evidence. Placeholders until tuned on the dev split.
WEAK_COSINE = 0.30
WEAK_BM25 = 2.0
