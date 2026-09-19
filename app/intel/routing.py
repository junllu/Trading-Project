"""Model routing — which tier of intelligence is allowed to do which job.

THE PROBLEM THIS SOLVES

Three tiers are now available: Claude (expensive, strong judgement), a local
qwen2.5 on Ollama (free, weak judgement, unlimited throughput), and no model at
all (statistics). Left to ad-hoc choice at each call site, the routing decays in
one predictable direction — toward whatever is cheapest and closest to hand —
and the failure is silent, because a fluent wrong answer reads exactly like a
right one.

So the assignment is declared here, once, and enforced.

THE INVARIANT THAT MATTERS MOST

    A verdict on whether something WORKS may never be produced by a model.

Not by Claude, not by qwen. "Did this strategy variant beat holding?" and "is
this pattern real or selection noise?" are statistical questions with statistical
answers — deflated Sharpe, walk-forward folds, forward-record calibration. A
language model asked those questions will produce a confident, plausible,
unfalsifiable narrative, and this project has already measured what that costs:
3,208 candlestick looks whose best directional pattern could not beat the Sharpe
expected from selection alone, and a conviction engine that beat buy-and-hold in
7 of 34 out-of-sample folds while reading as insightful the whole way.

Those tasks are tier DETERMINISTIC and `route()` refuses to hand them to any
provider. It raises rather than returning a default, because a routing mistake
that silently downgrades a verdict to a chat completion is the exact failure
this module exists to make impossible.

THE OTHER TWO TIERS

    CLAUDE   judgement that is genuinely hard and genuinely rare: synthesising
             research, proposing method variants, triaging an anomaly nobody
             anticipated. Low frequency, high stakes, worth the cost.
    LOCAL    bounded, mechanical, high-frequency work where the input already
             contains the answer: summarising an agent's own JSON report,
             explaining a decision the agent already made and recorded,
             reformatting, classifying a log line. A 7b model is sufficient
             because it is not being asked to know anything the prompt does not
             already state.

The dividing line between CLAUDE and LOCAL is not difficulty of language. It is
whether the task requires knowledge or judgement NOT PRESENT IN THE INPUT. If the
answer is derivable from the text in front of it, the local model does it.

    python -m app.intel.routing
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

CLAUDE, LOCAL, DETERMINISTIC = "claude", "local", "deterministic"
TIERS = (CLAUDE, LOCAL, DETERMINISTIC)


class RoutingError(RuntimeError):
    """Raised when a task is routed to a tier it may not use."""


@dataclass(frozen=True)
class Task:
    name: str
    tier: str
    why: str
    decided_by: str = ""          # for DETERMINISTIC: what actually answers it


# The registry. Adding a task here is a deliberate act; `route()` refuses
# anything not listed, so a new call site cannot invent its own tier silently.
TASKS: tuple[Task, ...] = (
    # ---- DETERMINISTIC: no model may answer these ------------------------
    Task("strategy_verdict", DETERMINISTIC,
         "whether a method beats the benchmark is a statistical question; a model "
         "asked it returns a story that cannot be falsified",
         decided_by="app/backtest/trials.py deflated_sharpe + walk-forward folds"),
    Task("pattern_significance", DETERMINISTIC,
         "3,208 candlestick looks produced a best directional Sharpe below what "
         "selection alone yields; only deflation can tell those apart",
         decided_by="app/analytics/candles.py deflate()"),
    Task("forecast_calibration", DETERMINISTIC,
         "whether predictions earn trust is measured against resolved outcomes, "
         "never asserted",
         decided_by="app/analytics/confidence.py score_forward()"),
    Task("risk_approval", DETERMINISTIC,
         "caps and halts are arithmetic; a model in this path could be talked "
         "into an order the limits forbid",
         decided_by="app/engine/risk.py RiskManager.approve()"),
    Task("exit_rule_evaluation", DETERMINISTIC,
         "a stop is touched or it is not; policy lives in exit_plans.py and the "
         "comparison is arithmetic",
         decided_by="app/agent/exit_monitor.py evaluate_position()"),
    Task("position_sizing", DETERMINISTIC,
         "size follows from conviction, caps and held quantity — no narrative "
         "belongs between those inputs and the number",
         decided_by="app/engine/executor.py + risk.py"),
    Task("autonomy_promotion", DETERMINISTIC,
         "a routine earns autonomy on its pass rate, not on an argument that it "
         "deserves it",
         decided_by="app/agent/autonomy.py"),

    # ---- CLAUDE: hard judgement, low frequency ---------------------------
    Task("research_synthesis", CLAUDE,
         "reconciling filings, themes and sources into a thesis needs knowledge "
         "well beyond the prompt"),
    Task("method_variant_proposal", CLAUDE,
         "proposing what to try next is generative and hard; note that judging "
         "the result is strategy_verdict and stays deterministic"),
    Task("anomaly_triage", CLAUDE,
         "an unanticipated break has no template; this is exactly where a weak "
         "model produces a confident wrong diagnosis"),
    Task("plan_review", CLAUDE,
         "reviewing a trade plan against the campaign's constraints is the last "
         "judgement before real money"),
    Task("portfolio_narrative", CLAUDE,
         "explaining what the book is doing, and why, across 30 names and a "
         "macro regime"),

    # ---- LOCAL: bounded, mechanical, high frequency -----------------------
    Task("report_summarization", LOCAL,
         "the agent's JSON already contains the answer; this renders it as prose"),
    Task("decision_explanation", LOCAL,
         "'why did the exit monitor flag BE' is answered by that agent's own "
         "recorded verdict and evidence — reading, not deciding"),
    Task("agent_chat", LOCAL,
         "interrogating an existing report is retrieval over text already on "
         "disk; unlimited throughput matters more than depth"),
    Task("structured_extraction", LOCAL,
         "pulling fields out of text into a schema, where the text is the source "
         "of truth"),
    Task("log_triage", LOCAL,
         "classifying a log line as routine or notable is high volume and cheap "
         "to verify"),
    Task("watchlist_blurb", LOCAL,
         "one-line descriptions of names, from data already fetched"),
)

BY_NAME = {t.name: t for t in TASKS}


def route(task: str, *, allow_fallback: bool = True) -> str:
    """Return the provider a task must use.

    Raises rather than defaulting. An unknown task is a call site that never
    declared its tier, and guessing on its behalf is how the deterministic
    boundary gets crossed by accident.
    """
    t = BY_NAME.get(task)
    if t is None:
        raise RoutingError(
            f"unknown task {task!r} — declare it in app/intel/routing.py TASKS "
            f"with an explicit tier. Routing is not inferred.")
    if t.tier is DETERMINISTIC or t.tier == DETERMINISTIC:
        raise RoutingError(
            f"{task!r} is DETERMINISTIC and must not reach a language model. "
            f"It is answered by {t.decided_by}.")
    if t.tier == CLAUDE and not allow_fallback:
        return CLAUDE
    return t.tier


def provider_for(task: str) -> str:
    """Map a tier onto the analyst factory's provider names."""
    tier = route(task)
    return {CLAUDE: "anthropic", LOCAL: "ollama"}[tier]


def may_use_model(task: str) -> bool:
    t = BY_NAME.get(task)
    return bool(t and t.tier != DETERMINISTIC)


def validate() -> list[str]:
    """Invariants, enforced rather than described."""
    errs: list[str] = []
    seen: set[str] = set()
    for t in TASKS:
        if t.name in seen:
            errs.append(f"{t.name}: declared twice")
        seen.add(t.name)
        if t.tier not in TIERS:
            errs.append(f"{t.name}: unknown tier {t.tier!r}")
        if not t.why.strip():
            errs.append(f"{t.name}: no reason given for its tier")
        if t.tier == DETERMINISTIC and not t.decided_by.strip():
            errs.append(f"{t.name}: DETERMINISTIC but names nothing that decides it")
        if t.tier != DETERMINISTIC and t.decided_by:
            errs.append(f"{t.name}: only DETERMINISTIC tasks name a decider")
    # The verdict family must never drift off DETERMINISTIC.
    for name in ("strategy_verdict", "pattern_significance", "forecast_calibration"):
        t = BY_NAME.get(name)
        if t is None or t.tier != DETERMINISTIC:
            errs.append(f"{name}: must remain DETERMINISTIC — a model may not "
                        f"rule on whether something works")
    return errs


def report() -> dict[str, Any]:
    return {
        "agent": "model_routing",
        "tiers": {
            tier: [{"task": t.name, "why": t.why,
                    **({"decided_by": t.decided_by} if t.decided_by else {})}
                   for t in TASKS if t.tier == tier]
            for tier in TIERS
        },
        "invariant": ("a verdict on whether something WORKS is never produced by "
                      "a model — not by Claude, not by the local model"),
        "errors": validate(),
    }


def _main() -> None:                              # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Model routing — who does which job.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--task", help="show the routing for one task")
    args = ap.parse_args()

    if args.task:
        try:
            print(f"{args.task} -> {route(args.task)}")
        except RoutingError as exc:
            print(f"{args.task} -> REFUSED\n  {exc}")
        return

    r = report()
    if args.json:
        print(json.dumps(r, indent=2))
        return

    print("=" * 78)
    print("  MODEL ROUTING — which tier is allowed to do which job")
    print("=" * 78)
    titles = {
        DETERMINISTIC: "DETERMINISTIC — no model may answer these",
        CLAUDE: "CLAUDE — hard judgement, low frequency, worth the cost",
        LOCAL: "LOCAL (qwen2.5 / Ollama) — bounded, mechanical, high frequency",
    }
    for tier in (DETERMINISTIC, CLAUDE, LOCAL):
        items = [t for t in TASKS if t.tier == tier]
        print(f"\n  {titles[tier]}")
        print("  " + "-" * 74)
        for t in items:
            print(f"    {t.name}")
            print(f"        why  {t.why}")
            if t.decided_by:
                print(f"        by   {t.decided_by}")

    errs = validate()
    print(f"\n  {'-' * 74}")
    if errs:
        print("  INVARIANT VIOLATIONS:")
        for e in errs:
            print(f"    FAIL  {e}")
    else:
        print("  invariants hold: no verdict task is routable to a model")


if __name__ == "__main__":                        # pragma: no cover
    _main()
