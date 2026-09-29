"""Prompt registry: prompts are versioned Markdown files, not inline strings."""
import re
from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent
_NAME_RE = re.compile(r"^[a-z0-9_]+$")


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    if not _NAME_RE.match(name):
        raise ValueError(f"Invalid prompt name: {name!r}")
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
