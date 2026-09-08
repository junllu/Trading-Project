"""The agent graph — who owns what, what flows between them, where it stops.

Not a list of bots. A graph with three properties that are enforced rather than
described, because every one of them is a mistake this project already made and
had to undo:

  MAKER / CHECKER SPLIT
      A maker produces research and never judges it. A checker judges and never
      produces. The reason is concrete: thesis_ledger's net-margin checkpoints
      all passed on ANET while the filing showed COGS growing 46.9% against
      revenue's 37.7%. The checkpoint was not wrong, it was reading its own
      preferred number. Only a SECOND maker with a different input catches that,
      and only if the checker cannot quietly become its own supplier.

  LEG INDEPENDENCE
      The core leg (theses to 2028) and the options leg (volatility around
      events) are separate books with separate edges. An earlier version fed the
      core's fundamental read into the options structure choice, which made the
      sleeve a derivative of the core research — one wrong fundamental call
      would then break both books at once, defeating the entire point of having
      two. They now meet at exactly one place: covered calls and cash-secured
      puts, the only structures whose assignment hands the core pool shares it
      did not choose.

  THE COORDINATOR NEVER GENERATES
      It routes, gates and escalates. The moment a coordinator starts producing
      research it becomes another maker grading its own work, and the split
      above is gone.

Cadence follows the DECISION, never the data. A thesis underwritten to 2028 is
re-graded when a filing lands, not every morning — a daily brief on a two-year
hold is 500 opportunities a year to break discipline. The options leg genuinely
is daily, because its edge decays in days. Public "AI hedge fund desk" write-ups
almost always put everything on one 6 AM cadence; that is the single biggest
thing to not copy.

Deliberately NOT included: a sentiment agent on the core leg (mention volume is
noise on a two-year hold), and any self-improving loop that rewrites its own
rules from last week's outcomes — that is in-sample fitting with no holdout and
it is precisely what produced this project's ~16pp overfit gap.

    python -m app.agent.roster              # the graph
    python -m app.agent.roster --validate   # enforce the invariants
    python -m app.agent.roster --run        # execute makers -> checkers
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from .autonomy import IRREVERSIBLE, LEVELS, OBSERVE, PREPARE

MAKER, CHECKER, COORDINATOR = "maker", "checker", "coordinator"
CORE, OPTIONS, SHARED = "core", "options", "shared"

# The single sanctioned crossing point between the two books.
COORDINATION_POINT = ("covered_call", "cash_secured_put")

# --- the two clocks --------------------------------------------------------
# Cadences that re-open a decision within a day. A checker on the CORE leg must
# never run on one of these: the holding period is years, and a daily verdict on
# a two-year thesis is ~250 fresh opportunities a year to talk yourself out of
# it. The OPTIONS leg must run on one, because its edge decays in days.
FAST_CADENCES = {"intraday", "daily"}
SLOW_CADENCES = {"on_filing", "weekly", "bi_weekly", "monthly", "quarterly", "on_sweep"}

# Evidence whose half-life is shorter than the core leg's holding period. On a
# two-year thesis a mention-volume spike is noise; on the options leg the same
# spike is what moves implied volatility, which is the sleeve's whole subject.
FAST_DECAY_SIGNALS = {"sentiment", "price"}


@dataclass
class Agent:
    name: str
    kind: str                        # maker | checker | coordinator
    leg: str                         # core | options | shared
    owns: str                        # ONE result, in a sentence
    consumes: list[str] = field(default_factory=list)
    produces: str = ""
    cadence: str = ""
    why_that_cadence: str = ""
    cadence_class: str = ""          # structured cadence — the two-clock rule reads this
    signal_kind: str = ""            # what KIND of evidence: fundamental|price|event|
                                     # macro|curated|sentiment|record|verdict|meta|route
    rewrites_own_rules: bool = False # must stay False. See INVARIANT 6.
    module: str = ""                 # importable module, if it runs
    entrypoint: str = ""             # no-arg callable in that module; "" = not wired
    level: int = PREPARE
    stages: list[str] = field(default_factory=list)   # irreversible acts it may STAGE
    note: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__) | {"level_name": LEVELS[self.level][0]}


ROSTER: list[Agent] = [
    # ---------------- MAKERS: produce evidence, never grade it -------------
    Agent(
        name="filings_analyst", cadence_class="on_filing", signal_kind="fundamental", kind=MAKER, leg=SHARED,
        owns="operational truth from primary filings — is revenue growth price or volume",
        consumes=["SEC XBRL via broker MCP"],
        produces="data/fundamentals/filings_*.json",
        cadence="on each new 10-Q/10-K", module="app.intel.filings", entrypoint="report",
        why_that_cadence=("A filing is the only moment new operational fact exists. "
                          "Running it daily re-reads the same numbers and invites "
                          "reacting to price while pretending to read fundamentals."),
        note=("The highest-value maker in the graph. Cost elasticity separated MU "
              "(0.03, price spike) from VRT (0.71, volume + leverage) and caught the "
              "gross-margin erosion at ANET, ALAB and NOW that net margin hid."),
    ),
    Agent(
        name="price_history", cadence_class="daily", signal_kind="price", kind=MAKER, leg=SHARED,
        owns="real daily closes and the volatility distribution derived from them",
        consumes=["yfinance / cached CSV"], produces="data/prices/*.csv",
        cadence="daily, after the close", module="app.data.market_data", entrypoint="report",
        why_that_cadence="One new bar per day exists. There is nothing else to fetch.",
        note=("Was silently synthesising near-straight lines, which pinned every "
              "volatility scalar at its ceiling and made all risk sizing inert. "
              "A maker that fabricates is worse than a missing one."),
    ),
    Agent(
        name="event_calendar", cadence_class="weekly", signal_kind="event", kind=MAKER, leg=OPTIONS,
        owns="scheduled catalysts — earnings dates and their confirmation status",
        consumes=["broker MCP earnings calendar"], produces="data/options/events_*.json",
        cadence="weekly", module="app.options.desk", entrypoint="latest_events",
        why_that_cadence="Dates move rarely; unconfirmed dates firm up over weeks.",
    ),
    Agent(
        name="macro_signals", cadence_class="monthly", signal_kind="macro", kind=MAKER, leg=CORE,
        owns="computed, publication-lagged macro state — cycle year and M2",
        consumes=["FRED"], produces="in-memory signals", cadence="monthly",
        module="app.macro.signals", entrypoint="all_signals",
        why_that_cadence=("M2 publishes monthly with a 2-month lag and transmits over "
                          "~6 months. A daily read would be the same number 30 times."),
    ),
    Agent(
        name="source_feeds", cadence_class="bi_weekly", signal_kind="curated", kind=MAKER, leg=SHARED,
        owns="dated third-party views, point-in-time filtered and age-decayed",
        consumes=["curated analysts"], produces="data/feeds/", cadence="bi-weekly",
        module="app.intel.feeds", entrypoint="report",
        why_that_cadence="Matches how often the tracked sources actually publish.",
        note="Returns None when a source is silent. Silence is not neutrality.",
    ),
    Agent(
        name="researcher", cadence_class="daily", signal_kind="curated", kind=MAKER, leg=SHARED,
        owns="outward coverage brief — shortlist of non-owned names + compact dossiers",
        consumes=["price_history", "source_feeds"],
        produces="data/research/brief_*.json",
        cadence="daily, after the close", module="app.analytics.researcher", entrypoint="report",
        why_that_cadence=("Coverage failures happen when nobody scans outside the book. "
                          "One brief per day is enough; intraday research re-trades noise."),
        note=("Maker only: assembles discovery + deep_dive facts. Never assigns buy/sell, "
              "never places orders. Tier-2 MCP pulls are queued for a human."),
    ),
    Agent(
        name="live_recorder", cadence_class="intraday", signal_kind="record", kind=MAKER, leg=SHARED, level=OBSERVE,
        owns="what the system WOULD have done, written before outcomes are known",
        consumes=["portal state"], produces="data/forward_record.jsonl",
        cadence="every 5 min during the regular session", module="app.agent.live_loop",
        entrypoint="report",
        why_that_cadence=("This is the only record that cannot be tuned after the fact, "
                          "so it is worth sampling densely. It changes nothing."),
    ),

    # ---------------- CHECKERS: judge, never generate ----------------------
    Agent(
        name="thesis_ledger", cadence_class="on_filing", signal_kind="verdict", kind=CHECKER, leg=CORE,
        owns="a verdict per core holding against checkpoints fixed in advance",
        consumes=["filings_analyst", "price_history", "macro_signals", "source_feeds"],
        produces="INTACT / WATCH / BROKEN per name",
        cadence="quarterly, on filings", module="app.intel.thesis", entrypoint="report",
        why_that_cadence=("The holding period is years. Re-grading weekly manufactures "
                          "activity and tempts trading on noise."),
        note="Consumes TWO independent makers so neither can quietly grade itself.",
    ),
    Agent(
        name="options_desk", cadence_class="daily", signal_kind="verdict", kind=CHECKER, leg=OPTIONS,
        owns="event-driven options candidates, priced on volatility rank alone",
        consumes=["event_calendar", "price_history"],
        produces="candidate structures with verdicts",
        cadence="daily", module="app.options.desk", entrypoint="build",
        why_that_cadence="Vol rank and days-to-event both move daily; the edge decays in days.",
        stages=["place_order"],
        note=("Consumes NO fundamental input by design. It reached a verdict on ADBE "
              "when the core layer had no filing for it — under the old coupling that "
              "was an automatic refusal."),
    ),
    Agent(
        name="calibration_scorer", cadence_class="monthly", signal_kind="meta", kind=CHECKER, leg=SHARED,
        owns="whether stated confidence matches realised frequency",
        consumes=["live_recorder"], produces="Brier skill and ECE",
        cadence="monthly", module="app.analytics.calibration", entrypoint="report",
        why_that_cadence="Needs outcomes to have resolved. Scoring early is peeking.",
        note="Cannot run until the conviction score is emitted as a probability.",
    ),
    Agent(
        name="trials_auditor", cadence_class="on_sweep", signal_kind="meta", kind=CHECKER, leg=SHARED,
        owns="the multiple-testing correction — what survives its own search",
        consumes=["every backtest and sweep"], produces="Deflated Sharpe Ratio",
        cadence="after every sweep", module="app.backtest.trials", entrypoint="registry_summary",
        why_that_cadence=("N rises the moment a variant is examined, so the correction "
                          "is stale the moment it is not re-run."),
        note=("The agent that makes iteration safe. Best observed Sharpe 1.359 against "
              "a selection bar of 1.311 — DSR 0.812, WEAK."),
    ),
    Agent(
        name="onboarding_gate", cadence_class="weekly", signal_kind="meta",
        kind=CHECKER, leg=SHARED,
        owns="whether a symbol has the data each leg needs before it can be used",
        consumes=["price_history", "filings_analyst", "source_feeds", "researcher"],
        produces="READY_BOTH / READY_OPTIONS / READY_CORE / DATA_ONLY per symbol",
        cadence="weekly, and on any new candidate",
        module="app.intel.onboard", entrypoint="report",
        why_that_cadence=("A symbol's data footprint changes when a filing lands or a "
                          "fetch runs, not intraday."),
        note=("Guards the failure that is silent by construction: a half-onboarded "
              "name does not error, it just scores off synthetic volatility or gets "
              "graded against a snapshot with no entry for it. The two legs need "
              "DIFFERENT data, so readiness is reported per leg — a name is routinely "
              "usable by the options desk and not by the core."),
    ),
    Agent(
        name="autonomy_auditor", cadence_class="weekly", signal_kind="meta", kind=CHECKER, leg=SHARED,
        owns="each routine's receipt, and whether it still deserves its permissions",
        consumes=["all routines"], produces="promote / hold / DEMOTE",
        cadence="weekly", module="app.agent.autonomy", entrypoint="audit",
        why_that_cadence="Always-on automation rots quietly; a week is short enough to catch it.",
        note="Demotion is automatic. Promotion requires a human.",
    ),

    Agent(
        name="program_manager", cadence_class="weekly", signal_kind="meta",
        kind=CHECKER, leg=SHARED,
        owns="whether the PROGRAM is on track — pace to plan, delivery, evidence debt, blockers",
        consumes=["live_recorder", "roster graph", "holdings.yaml"],
        produces="program status + proposals in the chief's schema",
        cadence="weekly", module="app.agent.program", entrypoint="report",
        why_that_cadence=("Execution state changes over days, not hours. A daily PM "
                          "report on a two-year campaign manufactures the same "
                          "false urgency the two-clock rule exists to prevent."),
        note=("Deliberately CANNOT close a pace gap by taking risk: proposals "
              "touching position caps, the drawdown halt, leverage or concentration "
              "are rejected by validate_proposals(), not merely discouraged. "
              "'Behind plan, so size up' reads like diligence right until it ends "
              "the campaign, and 'make no mistake' outranks the target. It is a "
              "CHECKER, not a second coordinator — the graph permits exactly one gate."),
    ),

    # ---------------- COORDINATOR: routes and gates, never researches -------
    Agent(
        name="chief", cadence_class="dual", signal_kind="route", kind=COORDINATOR, leg=SHARED,
        owns="one reviewed brief, with every irreversible step staged and named",
        consumes=["thesis_ledger", "options_desk", "trials_auditor",
                  "calibration_scorer", "autonomy_auditor", "onboarding_gate", "researcher"],
        produces="the decision queue",
        cadence="daily pre-open; full review quarterly on filings",
        module="app.agent.chief", entrypoint="report",
        why_that_cadence=("Two clocks on purpose: the options leg needs a daily look, "
                          "the core leg does not, and collapsing them into one cadence "
                          "is what makes a long-horizon book trade like a short one."),
        stages=list(IRREVERSIBLE),
        note=("Applies the ONE sanctioned crossing between legs: a live core thesis "
              "vetoes covered calls on its shares, and a put is only sold on a name "
              "the core pool would genuinely own. Generates no research of its own."),
    ),
]

BY_NAME = {a.name: a for a in ROSTER}


# --- invariants, enforced rather than described ----------------------------

def validate() -> list[str]:
    """Every rule here corresponds to a bug this project actually shipped."""
    errs: list[str] = []

    for a in ROSTER:
        for dep in a.consumes:
            if dep in BY_NAME and BY_NAME[dep].kind == CHECKER and a.kind == MAKER:
                errs.append(f"{a.name}: a maker consumes checker '{dep}' — circular judgement")

        if a.kind == MAKER and a.stages:
            errs.append(f"{a.name}: makers must not stage irreversible actions {a.stages}")

        if a.kind == COORDINATOR and a.leg != SHARED:
            errs.append(f"{a.name}: a coordinator bound to one leg cannot arbitrate between them")

        # Leg independence: a leg-bound checker may not consume the other leg.
        if a.kind == CHECKER and a.leg in (CORE, OPTIONS):
            for dep in a.consumes:
                d = BY_NAME.get(dep)
                if d and d.leg not in (a.leg, SHARED):
                    errs.append(f"{a.name} ({a.leg}) consumes {dep} ({d.leg}) — legs must "
                                f"stay independent except at {COORDINATION_POINT}")

        if a.consumes and a.kind == CHECKER and not any(
                BY_NAME.get(d, Agent(d, MAKER, SHARED, "")).kind == MAKER for d in a.consumes):
            errs.append(f"{a.name}: a checker with no maker input is grading its own opinion")

        # INVARIANT 4 — TWO CLOCKS. Cadence follows the DECISION, not the data.
        # Every public "AI hedge fund desk" writeup collapses everything onto one
        # 6 AM cadence; on a book held to 2028 that is ~250 chances a year to
        # break discipline, and it is the single biggest thing not to copy.
        if a.kind == CHECKER:
            if a.leg == CORE and a.cadence_class in FAST_CADENCES:
                errs.append(f"{a.name}: a CORE checker on a '{a.cadence_class}' clock — "
                            f"the holding period is years; re-grading that often "
                            f"manufactures activity on a thesis that has not changed")
            if a.leg == OPTIONS and a.cadence_class not in FAST_CADENCES:
                errs.append(f"{a.name}: an OPTIONS checker on a '{a.cadence_class}' clock — "
                            f"this edge decays in days and a slow clock misses it entirely")

        # INVARIANT 5 — NO FAST-DECAY EVIDENCE ON THE CORE LEG. Sentiment belongs
        # to the options sleeve, where mention volume drives implied vol. Fed to a
        # two-year thesis it is noise wearing the costume of information.
        if a.leg == CORE and a.signal_kind in FAST_DECAY_SIGNALS:
            errs.append(f"{a.name}: '{a.signal_kind}' evidence on the CORE leg — its "
                        f"half-life is far shorter than the holding period")

        # INVARIANT 6 — NO SELF-IMPROVING LOOP. "Review last week's flags, check
        # whether the stock moved 2%, rewrite your criteria" is in-sample fitting
        # with no holdout. It is the exact mechanism behind this project's ~16pp
        # overfit gap. trials_auditor is the sanctioned replacement: it counts N
        # and deflates, so iteration has a visible price instead of a free one.
        if a.rewrites_own_rules:
            errs.append(f"{a.name}: rewrites its own rules from past outcomes — "
                        f"in-sample fitting with no holdout. Use trials_auditor.")
        if a.name in a.consumes:
            errs.append(f"{a.name}: consumes its own output — a closed loop with no "
                        f"external check is how a backtest gets talked into an edge")

    # Exactly one coordinator, or nobody owns the gate.
    coords = [a for a in ROSTER if a.kind == COORDINATOR]
    if len(coords) != 1:
        errs.append(f"expected exactly 1 coordinator, found {len(coords)}")

    # Everything irreversible must be staged by the coordinator.
    staged = set(coords[0].stages) if coords else set()
    missing = set(IRREVERSIBLE) - staged
    if missing:
        errs.append(f"coordinator does not gate: {sorted(missing)}")
    return errs


def order() -> list[Agent]:
    """Makers, then checkers, then the coordinator. Dependencies respected."""
    rank = {MAKER: 0, CHECKER: 1, COORDINATOR: 2}
    return sorted(ROSTER, key=lambda a: (rank[a.kind], a.leg, a.name))


def run_pipeline(dry: bool = True) -> dict:
    """Execute the read-only research agents in dependency order.

    Only modules that produce or judge research run here. Nothing that stages an
    irreversible action is invoked — the coordinator's queue is for a human.
    """
    results = {}
    for a in order():
        if a.kind == COORDINATOR or not a.module:
            continue
        if dry:
            results[a.name] = "would run"
            continue
        if not a.entrypoint:
            # Importing is not running. Reporting "ok" here would show a green
            # pipeline for agents that did nothing, which is worse than a gap
            # you can see.
            results[a.name] = "NOT WIRED — CLI only, no no-arg callable"
            continue
        try:
            import importlib
            fn = getattr(importlib.import_module(a.module), a.entrypoint, None)
            if fn is None:
                results[a.name] = f"MISSING — {a.module}.{a.entrypoint} does not exist"
            else:
                results[a.name] = "ran" if fn() is not None else "ran (empty)"
        except Exception as exc:
            results[a.name] = f"FAILED {type(exc).__name__}: {exc}"
    return results


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()

    if args.run:
        print("running research agents in dependency order\n")
        res = run_pipeline(dry=False)
        for name, status in res.items():
            mark = "ok " if status.startswith("ran") else "!! "
            print(f"  [{mark}] {name:20} {status}")
        wired = sum(1 for s in res.values() if s.startswith("ran"))
        print(f"\n  {wired}/{len(res)} research agents ran.")
        if wired < len(res):
            print("  The rest are modules the graph names but cannot invoke — real")
            print("  work outstanding, not a green pipeline.")
        return

    errs = validate()
    if args.validate:
        print("=" * 74)
        print("  GRAPH INVARIANTS")
        print("=" * 74)
        if not errs:
            print("\n  all invariants hold:")
            print("    · no maker consumes a checker (no circular judgement)")
            print("    · no maker stages an irreversible action")
            print("    · every leg-bound checker consumes only its own leg or shared")
            print("    · every checker has at least one independent maker upstream")
            print(f"    · exactly one coordinator, gating all {len(IRREVERSIBLE)} "
                  f"irreversible actions")
            print("    · TWO CLOCKS — core checkers slow, options checkers fast")
            print("    · no fast-decay evidence (sentiment/price) on the core leg")
            print("    · no agent rewrites its own rules or consumes its own output")
        for e in errs:
            print(f"  FAIL  {e}")
        return

    print("=" * 78)
    print("  AGENT GRAPH — makers produce, checkers judge, the chief gates")
    print("=" * 78)
    last = None
    for a in order():
        if a.kind != last:
            titles = {MAKER: "MAKERS — produce evidence, never grade it",
                      CHECKER: "CHECKERS — judge evidence, never produce it",
                      COORDINATOR: "COORDINATOR — routes and gates, never researches"}
            print(f"\n  {titles[a.kind]}")
            print("  " + "-" * 74)
            last = a.kind
        leg = f"[{a.leg}]"
        print(f"\n  {a.name:20} {leg:<9} {LEVELS[a.level][0]}   ·   {a.cadence}")
        print(f"      owns      {a.owns}")
        if a.consumes:
            print(f"      consumes  {', '.join(a.consumes)}")
        if a.why_that_cadence:
            print(f"      cadence   {a.why_that_cadence}")
        if a.stages:
            shown = a.stages if len(a.stages) <= 3 else a.stages[:3] + ["..."]
            print(f"      STAGES    {', '.join(shown)}  (never executes)")
        if a.note:
            print(f"      note      {a.note}")

    print(f"\n  {'=' * 74}")
    print("  THE ONE CROSSING BETWEEN LEGS")
    print(f"    {' and '.join(COORDINATION_POINT)} — the only structures whose")
    print("    assignment hands the CORE pool shares it did not choose. Every other")
    print("    options structure risks only the sleeve's own premium and asks nobody.")
    print(f"\n  invariants: {'ALL HOLD' if not errs else f'{len(errs)} FAILING'}")


if __name__ == "__main__":
    _main()
