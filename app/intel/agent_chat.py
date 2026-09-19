"""Talk to one agent about its own report, on local hardware.

WHAT THIS IS FOR

Every agent already produces a structured `report()`. Reading raw JSON to find
out why the exit monitor flagged BE is tedious, and asking Claude each time
spends a cloud call on a question whose answer is already sitting on disk. That
is exactly the profile routing.py assigns to the LOCAL tier: bounded, mechanical,
high frequency, and answerable from text already in the prompt.

THE CONSTRAINT THAT MAKES IT SAFE

The local model is a READER, not a judge. qwen2.5:7b has no edge on markets, and
a fluent wrong answer about whether a strategy works is worse than no answer —
this project has measured that twice over. So:

  * the model is given the agent's report and told to answer only from it;
  * questions of the form "is this good / will this work / should I trade this"
    are refused before inference, because routing.py classes those DETERMINISTIC
    and names the statistic that actually answers them;
  * no output from here places, sizes, or approves anything.

If Ollama is unreachable the answer is the raw report and an honest note, never
a guess.

    python -m app.intel.agent_chat exits "why was BE flagged?"
    python -m app.intel.agent_chat --list
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from .routing import DETERMINISTIC, BY_NAME, provider_for
from .local_llm_analyst import DEFAULT_BASE_URL, DEFAULT_MODEL

# Agents that can be interrogated, and where their report comes from. Each entry
# is (import path, callable name, one-line charter) — the charter is fed to the
# model so it answers in that agent's voice and scope rather than as a generic
# assistant.
AGENTS: dict[str, tuple[str, str, str]] = {
    "researcher": (
        "app.analytics.researcher", "report",
        "Outward coverage: names you do NOT own, in themes that are alive. "
        "Produces a shortlist and the MCP pulls needed to finish it. "
        "Assigns no ratings and stages no orders."),
    "chief": (
        "app.agent.chief", "report",
        "The sole coordinator. Routes and gates every checker's output into one "
        "decision queue with evidence and falsifiers. Generates no research and "
        "executes nothing."),
    "program": (
        "app.agent.program", "report",
        "Program manager. Reports pace to the $1M target and what is blocking "
        "delivery. FORBIDDEN from proposing anything that closes a pace gap by "
        "taking more risk."),
    "exits": (
        "app.agent.exit_monitor", "report",
        "Grades every held position against its own exit plan. Sets no levels "
        "of its own — exit_plans.py owns policy. Places orders in paper only."),
    "routing": (
        "app.intel.routing", "report",
        "Declares which tier of intelligence may do which job, and refuses to "
        "route a verdict question to any model."),
}

# Asked of a report, these are really asking the model to rule on quality. The
# refusal names what does decide them instead.
_VERDICT_WORDS = (
    "is it good", "is this good", "will it work", "will this work",
    "should i trade", "should i buy", "should i sell", "is it profitable",
    "does it beat", "is the strategy", "is this strategy", "worth trading",
    "better than", "outperform",
)


def _load_report(agent: str) -> dict[str, Any]:
    mod_path, fn_name, _ = AGENTS[agent]
    import importlib
    mod = importlib.import_module(mod_path)
    fn: Callable[[], dict] = getattr(mod, fn_name)
    return fn()


def _refuses(question: str) -> str | None:
    q = question.lower()
    for w in _VERDICT_WORDS:
        if w in q:
            t = BY_NAME.get("strategy_verdict")
            return (
                f"That is a verdict question, and no language model answers those "
                f"here — local or otherwise. Whether something works is decided by "
                f"{t.decided_by if t else 'the statistical harness'}.\n"
                f"Ask instead what the report SAYS: which rules fired, on what "
                f"evidence, and what it could not evaluate.")
    return None


SYSTEM = """You are the {agent} agent of a trading portal, answering questions about your own report.

Your charter: {charter}

Rules you follow exactly:
- Answer ONLY from the report JSON provided. It is your entire world.
- If the report does not contain the answer, say "the report does not say" and stop. Never fill a gap with market knowledge or a plausible guess.
- Quote the specific fields you relied on so the answer can be checked.
- Never state whether a strategy, pattern or trade is good, profitable or worth taking. That is decided by statistical tests elsewhere, not by you. If asked, say so.
- Be brief. Two or three sentences unless asked to expand."""


def ask(agent: str, question: str, *, model: str | None = None,
        base_url: str = DEFAULT_BASE_URL, timeout: float = 90.0) -> dict[str, Any]:
    """Answer a question about one agent's report using the local model."""
    if agent not in AGENTS:
        return {"error": f"unknown agent {agent!r}; known: {', '.join(sorted(AGENTS))}"}

    refusal = _refuses(question)
    if refusal:
        return {"agent": agent, "question": question, "answer": refusal,
                "source": "refused-by-routing", "tier": DETERMINISTIC}

    try:
        report = _load_report(agent)
    except Exception as exc:
        return {"agent": agent, "error": f"report failed: {type(exc).__name__}: {exc}"}

    blob = json.dumps(report, indent=2, default=str)
    if len(blob) > 60_000:                     # keep the small model inside its window
        blob = blob[:60_000] + "\n... [report truncated]"

    provider = provider_for("agent_chat")      # routing says LOCAL -> ollama
    charter = AGENTS[agent][2]
    try:
        import requests
        resp = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            json={
                "model": model or DEFAULT_MODEL,
                "stream": False,
                "options": {"temperature": 0.1},
                "messages": [
                    {"role": "system",
                     "content": SYSTEM.format(agent=agent, charter=charter)},
                    {"role": "user",
                     "content": f"REPORT:\n{blob}\n\nQUESTION: {question}"},
                ],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        answer = (resp.json().get("message") or {}).get("content", "").strip()
    except Exception as exc:
        return {"agent": agent, "question": question, "provider": provider,
                "error": f"local model unreachable: {type(exc).__name__}: {exc}",
                "answer": None,
                "note": "no guess is substituted — read the report directly",
                "report": report}

    return {"agent": agent, "question": question, "provider": provider,
            "model": model or DEFAULT_MODEL, "answer": answer, "tier": "local"}


def _main() -> None:                              # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Ask one agent about its own report.")
    ap.add_argument("agent", nargs="?", choices=sorted(AGENTS))
    ap.add_argument("question", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--model")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.list or not args.agent:
        print("agents you can talk to:\n")
        for name, (_, _, charter) in sorted(AGENTS.items()):
            print(f"  {name:12} {charter}")
        return

    q = " ".join(args.question) or "summarise your report in three sentences"
    r = ask(args.agent, q, model=args.model)
    if args.json:
        print(json.dumps(r, indent=2, default=str))
        return
    print(f"\n[{args.agent}] {q}\n")
    if r.get("answer"):
        print(r["answer"])
    else:
        print(f"  {r.get('error') or r.get('note')}")
    if r.get("model"):
        print(f"\n  -- {r['model']} (local, no cloud call)")


if __name__ == "__main__":                        # pragma: no cover
    _main()
