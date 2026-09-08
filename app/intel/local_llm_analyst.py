"""Local LLM analyst — a free, offline stand-in for ClaudeAnalyst.

Talks to a local Ollama server (default http://localhost:11434) instead of the
Anthropic API. Reuses ClaudeAnalyst's rating contract (SYSTEM_PROMPT, JSON
response shape, [-1,1] ratings) so it plugs into the conviction engine
identically — and reuses its heuristic fallback, so a backtest loop never
breaks just because the local model isn't running.

Setup once: install Ollama, then `ollama pull qwen2.5:7b-instruct` (or set
PORTAL_LOCAL_LLM_MODEL to whatever you pulled).
"""
from __future__ import annotations

import json
import os
from typing import Any

from .claude_analyst import SYSTEM_PROMPT, AnalystResult, ClaudeAnalyst

DEFAULT_BASE_URL = os.getenv("PORTAL_OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("PORTAL_LOCAL_LLM_MODEL", "qwen2.5:7b-instruct")


class LocalLLMAnalyst(ClaudeAnalyst):
    """Drop-in analyst that calls a local Ollama model instead of the Anthropic API."""

    MAX_CONSECUTIVE_FAILURES = 3   # trip the breaker after this many failed inferences

    def __init__(self, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
                 timeout: float = 60.0):
        super().__init__(api_key="unused", model=model)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._reachable: bool | None = None
        self._consecutive_failures = 0
        self._tripped = False   # once inference itself fails repeatedly, stop retrying

    @property
    def live(self) -> bool:
        if self._tripped:
            return False
        if self._reachable is None:
            self._reachable = self._ping()
        return self._reachable

    def analyze(self, symbol_data: list) -> AnalystResult:
        result = super().analyze(symbol_data)
        # super().analyze() swallows _analyze_live exceptions and falls back to
        # heuristic on failure — detect that here via result.source to drive the breaker.
        if self.live and result.source == "heuristic":
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                self._tripped = True
        else:
            self._consecutive_failures = 0
        return result

    def _ping(self) -> bool:
        try:
            import requests
            r = requests.get(f"{self.base_url}/api/tags", timeout=2)
            r.raise_for_status()
            names = {m.get("name", "") for m in r.json().get("models", [])}
            return any(n == self.model or n.split(":")[0] == self.model.split(":")[0] for n in names)
        except Exception:
            return False

    def _analyze_live(self, symbol_data: list[dict[str, Any]]) -> AnalystResult:
        import requests
        user = json.dumps({"symbols": symbol_data}, default=str)
        resp = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        text = resp.json().get("message", {}).get("content", "")
        return self._parse(text, source=f"local:{self.model}")
