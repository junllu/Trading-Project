"""Calibration — turning a conviction SCORE into a checkable PROBABILITY.

The conviction engine emits a number in [-1, 1]. Nobody can say what 0.62 means.
It is not a probability, not an expected return, not a Sharpe. It ranks symbols
against each other and nothing more — which makes it unfalsifiable, because
there is no outcome that would prove a 0.62 wrong.

That is the deeper problem behind the walk-forward result. A score you cannot be
wrong about cannot be improved either: with no error signal, "iteration" is just
re-ranking. So the fix is not a better score, it is a score with units.

    score  0.62      unfalsifiable
    prob   "62% chance of beating SPY over the next 21 sessions"
                     wrong 38% of the time BY CONSTRUCTION, and measurable

Two different things then become measurable, and they are NOT the same:

    DISCRIMINATION  can it tell winners from losers at all? (AUC-like)
    CALIBRATION     when it says 62%, does that happen 62% of the time?

A model can discriminate well and be badly calibrated (right ordering, wrong
confidence), or be perfectly calibrated and useless (always says 50% and is
right half the time). Position sizing needs BOTH: you size on the probability,
so an overconfident 90% that is really 60% is how accounts blow up.

Scoring:

    Brier  mean squared error of probabilities. Lower is better. The reference
           point that matters is the base rate — always predicting the base rate
           gives a Brier score that any useful model must beat. This is the
           equivalent of "did it beat doing nothing" from confidence.py.
    ECE    expected calibration error: average gap between stated confidence and
           realised frequency, bucketed. This is what a reliability curve shows.

Platt scaling maps raw scores to probabilities by fitting a logistic. That fit
is itself a model and can overfit, so it MUST be fitted on train and scored on
test — the same discipline as everything else here.

    python -m app.analytics.calibration
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field

from ..config import ROOT

RECORD_PATH = ROOT / "data" / "forward_record.jsonl"


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


@dataclass
class Bin:
    lo: float
    hi: float
    n: int = 0
    predicted_sum: float = 0.0
    hits: int = 0

    @property
    def mean_predicted(self) -> float:
        return self.predicted_sum / self.n if self.n else 0.0

    @property
    def observed(self) -> float:
        return self.hits / self.n if self.n else 0.0

    @property
    def gap(self) -> float:
        return self.mean_predicted - self.observed


@dataclass
class CalibrationReport:
    n: int
    base_rate: float
    brier: float
    brier_base: float          # always predicting the base rate
    ece: float
    bins: list[Bin] = field(default_factory=list)

    @property
    def brier_skill(self) -> float:
        """1 - brier/brier_base. Positive means better than the base rate.

        Deliberately mirrors the edge-vs-hold test in confidence.py: being
        'accurate' is uninteresting if a constant prediction does as well.
        """
        return 1.0 - (self.brier / self.brier_base) if self.brier_base > 0 else 0.0

    def verdict(self) -> str:
        if self.n < 30:
            return f"n={self.n} — too few outcomes to say anything. Not a result."
        if self.brier_skill <= 0:
            return ("NO SKILL — a constant prediction of the base rate does as well "
                    "or better. The probabilities carry no information.")
        if self.ece > 0.10:
            return (f"DISCRIMINATES BUT MISCALIBRATED — Brier skill {self.brier_skill:+.3f} "
                    f"but ECE {self.ece:.3f}. Ordering is useful; the stated confidence "
                    f"is not. Do NOT size positions off these numbers until recalibrated.")
        return (f"CALIBRATED — Brier skill {self.brier_skill:+.3f}, ECE {self.ece:.3f}. "
                f"Stated confidence can be sized on.")

    def to_dict(self) -> dict:
        return {"n": self.n, "base_rate": round(self.base_rate, 4),
                "brier": round(self.brier, 5), "brier_base": round(self.brier_base, 5),
                "brier_skill": round(self.brier_skill, 4), "ece": round(self.ece, 4),
                "verdict": self.verdict(),
                "bins": [{"range": [round(b.lo, 2), round(b.hi, 2)], "n": b.n,
                          "predicted": round(b.mean_predicted, 3),
                          "observed": round(b.observed, 3), "gap": round(b.gap, 3)}
                         for b in self.bins if b.n]}


def evaluate(probs: list[float], outcomes: list[int], n_bins: int = 10) -> CalibrationReport:
    """Score probabilistic predictions against binary outcomes (1 = happened)."""
    n = len(probs)
    if n == 0 or n != len(outcomes):
        return CalibrationReport(0, 0.0, 0.0, 0.0, 0.0)

    base = sum(outcomes) / n
    brier = sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / n
    brier_base = sum((base - o) ** 2 for o in outcomes) / n

    bins = [Bin(i / n_bins, (i + 1) / n_bins) for i in range(n_bins)]
    for p, o in zip(probs, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        b = bins[idx]
        b.n += 1
        b.predicted_sum += p
        b.hits += o
    ece = sum(b.n * abs(b.gap) for b in bins) / n
    return CalibrationReport(n, base, brier, brier_base, ece, bins)


@dataclass
class PlattScaler:
    """score -> probability via a fitted logistic. Fit on TRAIN only.

    a and b are learned; the mapping is not a formula anyone can assert. Fitting
    it on the same data you score is how a miscalibrated model is made to look
    calibrated, which is why `fit` and `evaluate` are separate calls taking
    separate data.
    """
    a: float = 1.0
    b: float = 0.0
    fitted_on: int = 0

    def predict(self, score: float) -> float:
        return _sigmoid(self.a * score + self.b)

    def fit(self, scores: list[float], outcomes: list[int],
            iters: int = 500, lr: float = 0.1) -> "PlattScaler":
        """Plain gradient descent on log-loss. Small data, no need for Newton."""
        n = len(scores)
        if n < 10:
            self.fitted_on = n
            return self
        a, b = 1.0, 0.0
        for _ in range(iters):
            ga = gb = 0.0
            for s, o in zip(scores, outcomes):
                p = _sigmoid(a * s + b)
                err = p - o
                ga += err * s
                gb += err
            a -= lr * ga / n
            b -= lr * gb / n
        self.a, self.b, self.fitted_on = a, b, n
        return self


def split_fit_score(scores: list[float], outcomes: list[int],
                    train_frac: float = 0.6) -> dict:
    """Fit the scaler on the FIRST portion, score on the rest. Time-ordered.

    A random split would leak: adjacent observations share regime, so shuffling
    puts near-duplicates of test rows into train. The split is chronological for
    the same reason the walk-forward is.
    """
    n = len(scores)
    if n < 20:
        return {"error": f"n={n} — need at least 20 time-ordered outcomes"}
    cut = int(n * train_frac)
    scaler = PlattScaler().fit(scores[:cut], outcomes[:cut])
    test_probs = [scaler.predict(s) for s in scores[cut:]]
    rep = evaluate(test_probs, outcomes[cut:])
    return {"fitted_on": cut, "scored_on": n - cut,
            "a": round(scaler.a, 4), "b": round(scaler.b, 4), **rep.to_dict()}


def _load_records() -> list[dict]:
    if not RECORD_PATH.exists():
        return []
    out = []
    for line in RECORD_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def report() -> dict:
    """Agent entrypoint — grade whatever probabilities the record actually holds.

    Deliberately reports the SHAPE of the evidence before any score, because
    with a handful of unresolved predictions a Brier number would be arithmetic
    dressed as a finding. Nothing is graded until its horizon has elapsed, for
    the same reason confidence.py refuses to: peeking at partial outcomes is how
    a track record gets talked up.
    """
    from ..analytics.conviction import PROBABILITY_HORIZON_SESSIONS

    rows = _load_records()
    probs, uncal = [], 0
    for r in rows:
        for c in r.get("convictions", []):
            if "probability" in c:
                probs.append(float(c["probability"]))
                if not c.get("calibrated", False):
                    uncal += 1

    return {
        "record_rows": len(rows),
        "predictions_with_probability": len(probs),
        "uncalibrated": uncal,
        "horizon_sessions": PROBABILITY_HORIZON_SESSIONS,
        "gradable": 0,
        "status": ("no probabilistic predictions recorded yet" if not probs else
                   f"{len(probs)} probabilities recorded; none have completed the "
                   f"{PROBABILITY_HORIZON_SESSIONS}-session horizon, so none are gradable"),
        "note": ("The conviction engine now emits a probability, so this CAN be scored. "
                 "It needs elapsed time, not more code — the first grades land "
                 f"{PROBABILITY_HORIZON_SESSIONS} sessions after the first live record."),
    }


def _demo() -> None:
    """Three synthetic models, to show what the metrics actually separate."""
    import random
    random.seed(7)
    n = 2000

    # The outcome must be DRAWN from the true probability, not derived from a
    # hidden label. Generating `p = 0.75 if truth else 0.25` makes the outcome
    # deterministic given truth, so the honest forecast is 1.0 rather than 0.75
    # and a well-calibrated model scores as underconfident. That is a bug in the
    # simulation, not a property of calibration, and it is easy to ship by
    # accident.
    true_p = [random.uniform(0.05, 0.95) for _ in range(n)]
    truth = [1 if random.random() < p else 0 for p in true_p]

    def stretch(p: float, k: float) -> float:
        return min(0.99, max(0.01, 0.5 + (p - 0.5) * k))

    cases = {
        "informative + calibrated": list(true_p),
        "informative + OVERCONFIDENT": [stretch(p, 1.9) for p in true_p],
        "informative + underconfident": [stretch(p, 0.4) for p in true_p],
        "calibrated but USELESS": [0.5] * n,
    }
    print("=" * 76)
    print("  CALIBRATION — discrimination and confidence are different things")
    print("=" * 76)
    for name, probs in cases.items():
        r = evaluate(probs, truth)
        print(f"\n  {name}")
        print(f"    Brier {r.brier:.4f}  (base rate {r.brier_base:.4f})   "
              f"skill {r.brier_skill:+.3f}   ECE {r.ece:.3f}")
        print(f"    -> {r.verdict()}")
    print("\n  The middle case is the dangerous one: it ranks well and would look")
    print("  good on any accuracy metric, but its stated confidence is inflated.")
    print("  Size positions on that and the losses arrive faster than the model")
    print("  says they should.")


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="show what the metrics separate")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.demo:
        _demo()
        return

    rows = []
    if RECORD_PATH.exists():
        for line in RECORD_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    n_conv = sum(len(r.get("convictions", [])) for r in rows)

    print("=" * 76)
    print("  CALIBRATION OF THE LIVE CONVICTION SCORE")
    print("=" * 76)
    print(f"\n  forward_record rows : {len(rows)}")
    print(f"  conviction scores   : {n_conv}")
    print("\n  The live score is NOT yet emitted as a probability, so there is nothing")
    print("  here to calibrate. That is the gap this module names:")
    print("    a score of 0.62 has no outcome that would falsify it;")
    print("    a claim of '62% over 21 sessions' is wrong 38% of the time by design,")
    print("    and every one of those is a measurement.")
    print("\n  Run --demo to see what Brier skill and ECE separate.")
    if args.json:
        print(json.dumps({"rows": len(rows), "convictions": n_conv}, indent=2))


if __name__ == "__main__":
    _main()
