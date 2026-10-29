"""Paths, model names and per-ticket limits. Limits live here, not in policy text (POL-SUP-01)."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "synthetic"
POLICY_DIR = REPO / "policies"
RUNTIME = REPO / "runtime"  # working store, outbox, checkpoints, traces

CHAT_MODEL = "gpt-5-nano"
EMBED_MODEL = "text-embedding-3-small"

# Per ticket, counted across resumes.
MAX_MODEL_CALLS = 4
MAX_TOOL_CALLS = 6
MAX_SCHEMA_REPAIRS = 1
MAX_REVISIONS = 1
MAX_RETRIES = 1          # one retry for a temporary tool error
IDENTICAL_CALL_STOP = 3  # the third identical call stops the run

KB_MAX_K = 3
KB_MAX_CHARS = 4000
