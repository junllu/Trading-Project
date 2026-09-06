"""Provider-agnostic analyst — Claude API, a LOCAL model (Ollama), or heuristic.

The "analyst" is one agent in the conviction ensemble. It can run on:

  - anthropic : Claude via the API (best reasoning, costs tokens)
  - ollama    : a LOCAL model you run yourself (llama3.1, qwen2.5, etc.) at
                http://localhost:11434 — free, private, offline. This is how you
                run agents on local models.
  - heuristic : no LLM at all (technical-driven fallback)

Every provider returns the same AnalystResult, so the rest of the system is
unchanged. Any failure (server down, model missing) degrades to the heuristic —
nothing breaks. Run several instances on different local models to get an
ensemble of agents (see docs/AGENTS.md).
"""
from __future__ import annotations

import json
import os

from .claude_analyst import SYSTEM_PROMPT, AnalystResult, ClaudeAnalyst


class OllamaAnalyst(ClaudeAnalyst):
    """Analyst backed by a local model served by Ollama."""

    def __init__(self, model: str = "llama3.1", host: str | None = None):
        super().__init__(api_key="", model=model)          # not anthropic
        self.host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")

    @property
    def live(self) -> bool:
        return True                                        # local; falls back if unreachable

    def analyze(self, symbol_data: list[dict]) -> AnalystResult:
        try:
            return self._analyze_ollama(symbol_data)
        except Exception:
            return self._analyze_heuristic(symbol_data)

    def _analyze_ollama(self, symbol_data: list[dict]) -> AnalystResult:
        import requests
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"symbols": symbol_data}, default=str)},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }
        r = requests.post(f"{self.host}/api/chat", json=payload, timeout=120)
        r.raise_for_status()
        text = r.json()["message"]["content"]
        return self._parse(text, source=f"ollama:{self.model}")


def build_analyst(provider: str | None = None, model: str | None = None) -> ClaudeAnalyst:
    """Factory. provider: anthropic | ollama | heuristic (default)."""
    p = (provider or os.getenv("ANALYST_PROVIDER", "heuristic")).lower()
    if p in ("anthropic", "claude"):
        return ClaudeAnalyst(model=model or os.getenv("PORTAL_LLM_MODEL", "claude-opus-5"))
    if p in ("ollama", "local"):
        return OllamaAnalyst(model=model or os.getenv("OLLAMA_MODEL", "llama3.1"))
    return ClaudeAnalyst(api_key="")                       # heuristic (no LLM)
