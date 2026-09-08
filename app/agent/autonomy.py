"""The autonomy ladder — a routine earns permission, and can lose it.

app/analytics/confidence.py already gates CAPITAL on evidence. This gates
PERMISSION on evidence, which is a separate axis and was missing: a routine
could quietly graduate from "I ran it once by hand" to "it runs unattended"
without anything ever checking that it still worked.

Two ideas do the real work here.

FIRST: autonomy is a runtime privilege, not a property. Every level has a
promotion gate, and — the half that systems usually omit — a DEMOTION rule. A
routine whose pass rate decays moves back down automatically. Without that, a
system only ever accumulates permissions, and the day something breaks is the
day it has the most authority it has ever had.

SECOND: the approval line is drawn by REVERSIBILITY, not by importance. Research,
drafting, staging and simulating are all safely undoable, so they should finish
without asking. Sending, publishing, purchasing, deleting and overwriting are
not, so they park no matter how confident the run was. A good cycle ends with
every reversible step complete and every irreversible step staged with its exact
proposed action visible.

The failure this is built against is the "return at 10%" one: a routine that
stops the moment it sees a future approval step, which turns an agent into a
slower way of doing the work yourself. Finish the 90%, stage the rest.

Receipts close the loop. A routine reports runs, passes, repairs and repeated
failures, and then three questions get asked of it that a pass rate cannot
answer — most importantly whether anyone would notice if it disappeared. A
routine that would not be missed is deleted, because an automation portfolio's
value is not its size.

    python -m app.agent.autonomy
    python -m app.agent.autonomy --audit
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..config import ROOT

STATE_PATH = ROOT / "data" / "autonomy.json"
RECORD_PATH = ROOT / "data" / "forward_record.jsonl"

# --- the ladder ------------------------------------------------------------
OBSERVE, PREPARE, EXECUTE_STAGED, SCHEDULED, COORDINATE = 0, 1, 2, 3, 4

LEVELS = {
    OBSERVE: ("OBSERVE", "watches and records; changes nothing"),
    PREPARE: ("PREPARE", "researches, drafts, classifies, stages reversible work"),
    EXECUTE_STAGED: ("EXECUTE_STAGED", "completes the path, parks every consequential step"),
    SCHEDULED: ("SCHEDULED", "starts without a prompt and returns a receipt"),
    COORDINATE: ("COORDINATE", "routes work across routines, escalates only judgment"),
}

# Promotion requires proof. Demotion requires only decay — deliberately
# asymmetric, because the cost of over-trusting a broken routine is real money
# and the cost of under-trusting a good one is a manual run.
MIN_CLEAN_RUNS = 5
PROMOTE_PASS_RATE = 1.0
DEMOTE_PASS_RATE = 0.8
STALE_AFTER_DAYS = 14

# The ONLY session label a scheduled record can carry. Verified against
# app/data/market_hours.py (which emits closed|premarket|regular|afterhours)
# and against LiveLoop._run, which calls cycle() solely when st.is_open — i.e.
# session == "regular". In premarket/afterhours the loop refreshes prices and
# writes nothing, so those labels can never appear in a scheduled row.
# "manual" is a hand-run: it proves the code executes, never that the schedule
# fires, and must not satisfy a promotion gate.
LIVE_SESSIONS = {"regular"}

# --- the approval line, drawn by reversibility -----------------------------
REVERSIBLE = {
    "research", "summarize", "classify", "screen", "score", "backtest",
    "draft", "organize", "stage", "simulate", "record", "snapshot", "report",
}
IRREVERSIBLE = {
    "place_order", "cancel_order", "modify_order", "exercise_option",
    "transfer", "purchase", "sell", "delete", "overwrite", "rotate_credential",
    "change_permission", "publish", "send_message", "accept_terms",
}


def classify_action(action: str) -> tuple[str, str]:
    """FINISH or PARK, with the reason. Unknown actions PARK — fail closed."""
    a = action.strip().lower()
    if a in REVERSIBLE:
        return "FINISH", "reversible — nothing here cannot be undone"
    if a in IRREVERSIBLE:
        return "PARK", "irreversible — stage it and show the exact proposed action"
    return "PARK", "unclassified — an unknown action is treated as irreversible"


@dataclass
class Receipt:
    routine: str
    runs: int = 0
    passed: int = 0
    required_human_repair: int = 0
    repeated_failures: list[str] = field(default_factory=list)
    last_run: str | None = None
    expected_runs: int | None = None      # what the schedule implies

    @property
    def pass_rate(self) -> float:
        return self.passed / self.runs if self.runs else 0.0

    @property
    def ran_when_it_should(self) -> bool | None:
        """Question 1. None when there is no schedule to judge against."""
        if self.expected_runs is None:
            return None
        return self.runs >= self.expected_runs

    def recommendation(self) -> str:
        if not self.runs:
            return "no runs recorded — cannot promote, cannot judge"
        if self.repeated_failures:
            return "repeated failure — repair before any promotion"
        if self.pass_rate < DEMOTE_PASS_RATE:
            return "DEMOTE — pass rate has decayed below the floor"
        if self.pass_rate >= PROMOTE_PASS_RATE and self.runs >= MIN_CLEAN_RUNS:
            return "eligible for promotion"
        return "keep at current level; not enough clean runs yet"

    def to_dict(self) -> dict:
        return {"routine": self.routine, "runs": self.runs, "passed": self.passed,
                "pass_rate": round(self.pass_rate, 3),
                "required_human_repair": self.required_human_repair,
                "repeated_failures": self.repeated_failures,
                "last_run": self.last_run,
                "ran_when_it_should": self.ran_when_it_should,
                "recommendation": self.recommendation()}


@dataclass
class Routine:
    name: str
    owns: str                      # the ONE result, in a sentence
    level: int
    done_when: list[str]
    irreversible_steps: list[str] = field(default_factory=list)
    max_retries: int = 2
    escalation: str = "stop and surface to the human"
    would_be_missed: bool = True   # question 3, answered honestly by a human

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["level_name"] = LEVELS[self.level][0]
        return d


# --- the registry ----------------------------------------------------------
# One entry per thing that actually runs. If a module is not here, it is not a
# routine — it is a library, or it is dead code.
ROUTINES: list[Routine] = [
    Routine(
        name="live_loop", owns="record what the system WOULD do, before outcomes are known",
        level=OBSERVE,
        done_when=["a row appended to data/forward_record.jsonl per cycle",
                   "session state correctly reflects market hours",
                   "zero orders placed — this level changes nothing"],
        irreversible_steps=[],
        escalation="log and continue; a failed cycle must never kill the loop",
    ),
        Routine(
        name="researcher", owns="outward coverage brief for names not in the book",
        level=PREPARE,
        done_when=["data/research/brief_latest.json written",
                   "shortlist + MCP queue present",
                   "no buy/sell ratings emitted"],
        irreversible_steps=[],
    ),
Routine(
        name="thesis_ledger", owns="grade each core holding against checkpoints fixed in advance",
        level=PREPARE,
        done_when=["every held name has a verdict or is explicitly UNGRADED",
                   "snapshot age reported, and flagged when stale",
                   "filings overlay applied as an independent checker"],
        irreversible_steps=[],
    ),
    Routine(
        name="options_desk", owns="surface event-driven options candidates for review",
        level=PREPARE,
        done_when=["every candidate names a scheduled catalyst inside the horizon",
                   "core-thesis conflicts blocked, not merely noted",
                   "NO_TRADE returned when there is no catalyst"],
        irreversible_steps=["place_order"],
    ),
    Routine(
        name="daily_plan", owns="emit a vetted trade plan to data/trade_plan.json",
        level=EXECUTE_STAGED,
        done_when=["plan written", "guardrails re-checked against live account",
                   "every order intent staged with symbol, side, size and value"],
        irreversible_steps=["place_order"],
        escalation="halt on drawdown_halt_active or kill_switch; place nothing",
    ),
]


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def live_loop_receipt() -> Receipt:
    """A real receipt for the one routine that leaves a trace on disk."""
    r = Receipt(routine="live_loop")
    if not RECORD_PATH.exists():
        return r
    sessions: list[str] = []
    for line in RECORD_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            r.required_human_repair += 1
            continue
        r.runs += 1
        r.passed += 1 if row.get("prices") else 0
        r.last_run = row.get("recorded_at_et", r.last_run)
        sessions.append(row.get("session", "?"))

    # Question 1, answered against the actual market calendar rather than a
    # feeling: how many trading days have elapsed since the first record?
    if r.last_run:
        try:
            from ..data.market_hours import is_trading_day
            first = datetime.fromisoformat(
                json.loads(RECORD_PATH.read_text(encoding="utf-8").splitlines()[0])
                ["recorded_at_et"]).date()
            days = [first + timedelta(days=i)
                    for i in range((date.today() - first).days + 1)]
            # Keep a genuine zero. Collapsing it to None would report "unknown"
            # when the truthful answer is "no trading day has elapsed yet".
            r.expected_runs = sum(1 for d in days if is_trading_day(d))
        except Exception:
            r.expected_runs = None

    # "manual" is a hand-run cycle, not evidence the schedule works. Only an
    # actually-open session proves live behaviour, so check for its absence
    # rather than for the presence of "closed".
    if sessions and not any(s in LIVE_SESSIONS for s in sessions):
        r.repeated_failures.append(
            f"no cycle has ever run during an open session (saw: "
            f"{', '.join(sorted(set(sessions)))}) — live behaviour is unproven, "
            f"so this routine cannot be promoted off OBSERVE on this record")
    return r


def audit() -> dict:
    state = _load_state()
    receipts = {"live_loop": live_loop_receipt()}
    rows = []
    for rt in ROUTINES:
        rec = receipts.get(rt.name) or Receipt(routine=rt.name)
        level = state.get(rt.name, {}).get("level", rt.level)
        proposed = level
        note = ""
        if rec.runs and rec.pass_rate < DEMOTE_PASS_RATE and level > OBSERVE:
            proposed = level - 1
            note = "DEMOTE: pass rate below floor"
        elif (rec.runs >= MIN_CLEAN_RUNS and rec.pass_rate >= PROMOTE_PASS_RATE
              and not rec.repeated_failures and level < COORDINATE):
            proposed = level + 1
            note = "eligible for promotion — requires explicit human sign-off"
        rows.append({"routine": rt, "receipt": rec, "level": level,
                     "proposed_level": proposed, "note": note})
    return {"rows": rows, "as_of": date.today().isoformat()}


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rep = audit()

    if args.json:
        print(json.dumps({"as_of": rep["as_of"], "rows": [
            {"routine": r["routine"].to_dict(), "receipt": r["receipt"].to_dict(),
             "level": r["level"], "proposed_level": r["proposed_level"], "note": r["note"]}
            for r in rep["rows"]]}, indent=2))
        return

    print("=" * 76)
    print("  AUTONOMY LADDER — permission is earned, and can be taken back")
    print("=" * 76)
    print(f"  as of {rep['as_of']}\n")
    for r in rep["rows"]:
        rt, rec = r["routine"], r["receipt"]
        lvl = LEVELS[r["level"]]
        print(f"  {'-' * 72}")
        print(f"  {rt.name:16} L{r['level']} {lvl[0]:16} {lvl[1]}")
        print(f"     owns   {rt.owns}")
        print(f"     runs {rec.runs}  passed {rec.passed}  pass-rate {rec.pass_rate:.0%}"
              f"  repairs {rec.required_human_repair}")
        q1 = rec.ran_when_it_should
        if q1 is None:
            q1s = "unknown (no schedule to judge against)"
        elif rec.expected_runs == 0:
            q1s = "no trading day has elapsed since the first record — nothing due yet"
        elif q1:
            q1s = f"yes ({rec.runs} runs vs {rec.expected_runs} trading days)"
        else:
            q1s = f"NO — {rec.runs} runs vs {rec.expected_runs} trading days expected"
        print(f"     Q1 ran when it should?      {q1s}")
        print(f"     Q3 would it be missed?      {'yes' if rt.would_be_missed else 'NO — delete it'}")
        for f in rec.repeated_failures:
            print(f"     !  {f}")
        if rt.irreversible_steps:
            print(f"     PARK  {', '.join(rt.irreversible_steps)}")
        print(f"     -> {rec.recommendation()}")
        if r["note"]:
            print(f"     -> {r['note']}")

    print(f"\n  {'=' * 72}")
    print("  APPROVAL LINE (by reversibility, not importance)")
    print(f"     FINISH  {', '.join(sorted(REVERSIBLE))}")
    print(f"     PARK    {', '.join(sorted(IRREVERSIBLE))}")
    print("     Anything unclassified PARKS. A cycle should finish every reversible")
    print("     step and stage the rest — never stop at 10% because step 9 needs a yes.")


if __name__ == "__main__":
    _main()
