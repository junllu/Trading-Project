"""Meta-labeling — stop predicting direction, predict when to TRUST the signal.

The walk-forward result says the conviction engine cannot pick direction: 1 of 6
folds beat buy-and-hold, and the best Sharpe barely clears what selection across
6 configs produces by chance. The usual response is to hunt for a better
directional model. Meta-labeling (Lopez de Prado, AFML ch.3) says that is the
wrong move, and it is worth being precise about why.

Direction is close to a coin flip and always will be — it is the most competed
quantity in markets. But "is THIS particular signal, right now, in this regime,
one of the good ones?" is a much easier question, because it is about the
signal's own reliability rather than about the market:

    PRIMARY model    direction. Already built: sign(conviction).
    META model       P(the primary is right this time). Learned.
    SIZE             the meta probability. Low confidence -> small or nothing.

The asymmetry that makes this work: the meta model can output ZERO. The primary
must always say long or short; the meta model is allowed to say "sit this one
out", and most of the value comes from those refusals rather than from better
entries. A primary with a 51% hit rate that trades every signal is a losing
strategy after costs; the same primary trading only the top-decile-confidence
signals can be a winning one, with no improvement to the primary at all.

Labels follow from that split. The meta target is NOT the return sign — it is
whether the primary's call was correct:

    primary says LONG, price rises  -> meta label 1  (primary was right)
    primary says LONG, price falls  -> meta label 0
    primary says SHORT, price falls -> meta label 1  (primary was right)

So the meta model never has to know which way the market went, only whether the
primary agreed with it. That is a strictly easier learning problem on the same
data, which is the entire trick.

Honest limits: this cannot manufacture edge that is not there. If the primary is
a true coin flip with NO conditional structure — no regime, volatility or
strength in which it does better — the meta model finds nothing and correctly
sizes everything to zero. That is a legitimate and informative outcome, and
`fit_report` reports it as such rather than dressing it up.

    python -m app.ml.meta_label --demo
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field

FEATURES = ("abs_score", "vol", "trend_agreement", "regime")


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


@dataclass
class MetaSample:
    """One primary signal and what became of it."""
    features: dict[str, float]
    primary_direction: int          # +1 long, -1 short
    forward_return: float           # realised over the horizon

    @property
    def label(self) -> int:
        """1 when the primary was RIGHT. Not the return sign."""
        return 1 if self.primary_direction * self.forward_return > 0 else 0


@dataclass
class MetaModel:
    """Logistic on a handful of features. Deliberately small.

    A large model here would overfit a small, serially-correlated sample and
    reproduce the problem this project already has. The point is not capacity,
    it is having a second opinion that is allowed to say no.
    """
    weights: dict[str, float] = field(default_factory=dict)
    bias: float = 0.0
    n_fit: int = 0
    base_rate: float = 0.0

    def predict(self, features: dict[str, float]) -> float:
        z = self.bias + sum(self.weights.get(k, 0.0) * features.get(k, 0.0)
                            for k in FEATURES)
        return _sigmoid(z)

    def fit(self, samples: list[MetaSample], iters: int = 800, lr: float = 0.2,
            l2: float = 0.01) -> "MetaModel":
        n = len(samples)
        if n < 30:
            self.n_fit = n
            return self
        self.weights = {k: 0.0 for k in FEATURES}
        self.bias = 0.0
        self.base_rate = sum(s.label for s in samples) / n
        for _ in range(iters):
            gw = {k: 0.0 for k in FEATURES}
            gb = 0.0
            for s in samples:
                p = self.predict(s.features)
                err = p - s.label
                for k in FEATURES:
                    gw[k] += err * s.features.get(k, 0.0)
                gb += err
            for k in FEATURES:
                # L2 keeps a small sample from producing huge confident weights.
                self.weights[k] -= lr * (gw[k] / n + l2 * self.weights[k])
            self.bias -= lr * gb / n
        self.n_fit = n
        return self

    def size(self, features: dict[str, float], threshold: float = 0.55,
             max_fraction: float = 1.0) -> float:
        """Position size in [0, max_fraction]. Below threshold: do not trade.

        The refusal is the product. Scaling linearly above the threshold rather
        than stepping to full size keeps sizing monotone in confidence, so a
        marginal signal gets a marginal position.
        """
        p = self.predict(features)
        if p < threshold:
            return 0.0
        span = 1.0 - threshold
        return max_fraction * ((p - threshold) / span if span > 0 else 1.0)


def fit_report(train: list[MetaSample], test: list[MetaSample],
               threshold: float = 0.55) -> dict:
    """Fit on train, judge on test, and compare against trading EVERYTHING.

    The benchmark is the primary alone. A meta model that trades every signal is
    not adding anything; the question is whether its refusals were the right
    ones, which only shows up as a difference between filtered and unfiltered
    outcomes.
    """
    from ..analytics.calibration import evaluate

    model = MetaModel().fit(train)
    if model.n_fit < 30:
        return {"error": f"only {model.n_fit} training samples — need 30+"}

    probs = [model.predict(s.features) for s in test]
    labels = [s.label for s in test]
    cal = evaluate(probs, labels)

    # Unfiltered: act on every primary signal, unit size.
    all_ret = [s.primary_direction * s.forward_return for s in test]
    taken = [(model.size(s.features, threshold), s) for s in test]

    # The filter test must compare LIKE WITH LIKE. Measuring sized returns
    # against unsized ones scales the filtered average down by the mean position
    # fraction and makes any filter look harmful regardless of whether its
    # refusals were correct. So the discrimination question is asked on UNSIZED
    # per-signal outcomes over the taken subset, and sizing is reported
    # separately as a capital-efficiency figure.
    kept_ret = [s.primary_direction * s.forward_return for f, s in taken if f > 0]
    n_taken = len(kept_ret)
    refused_ret = [s.primary_direction * s.forward_return for f, s in taken if f <= 0]
    sized_total = sum(f * s.primary_direction * s.forward_return for f, s in taken)

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    improvement = mean(kept_ret) - mean(all_ret)
    return {
        "n_train": len(train), "n_test": len(test),
        "primary_hit_rate": round(sum(labels) / len(labels), 4) if labels else 0.0,
        "meta_calibration": cal.to_dict(),
        "signals_taken": n_taken,
        "signals_refused": len(test) - n_taken,
        "refusal_rate": round(1 - n_taken / len(test), 4) if test else 0.0,
        "mean_return_all_signals": round(mean(all_ret), 5),
        "mean_return_taken": round(mean(kept_ret), 5),
        "mean_return_refused": round(mean(refused_ret), 5),
        "improvement": round(improvement, 5),
        "capital_deployed_frac": round(sum(f for f, _ in taken) / len(test), 3) if test else 0.0,
        "total_sized_return": round(sized_total, 4),
        "weights": {k: round(v, 4) for k, v in model.weights.items()},
        "verdict": _verdict(improvement, cal.brier_skill, n_taken, len(test)),
    }


def _verdict(improvement: float, brier_skill: float, taken: int, total: int) -> str:
    if taken == 0:
        return ("REFUSES EVERYTHING — no regime cleared the confidence bar. Either the "
                "threshold is too high or the primary has no conditional structure.")
    if brier_skill <= 0:
        return ("NO META SKILL — the meta model cannot predict when the primary is right "
                "any better than the base rate. This is the honest outcome when a signal "
                "is uniformly random rather than conditionally good.")
    if improvement <= 0:
        return (f"FILTER HURTS — it refused {total - taken} signals and the survivors did "
                f"no better. The refusals were not the bad ones.")
    return (f"FILTER ADDS VALUE — took {taken}/{total}, mean return {improvement:+.4f} "
            f"better per signal than trading them all. The refusals were correct.")


def _demo() -> None:
    """Two worlds: one where the primary has conditional structure, one where it doesn't."""
    import random
    random.seed(11)

    def make(n: int, conditional: bool) -> list[MetaSample]:
        out = []
        for _ in range(n):
            strength = random.uniform(0.0, 1.0)
            vol = random.uniform(0.2, 1.2)
            agree = random.choice([0.0, 1.0])
            regime = random.choice([0.0, 1.0])
            # In the conditional world the primary is reliable when conviction is
            # strong AND the trend agrees; elsewhere it is a coin flip.
            p_right = 0.5
            if conditional:
                p_right = 0.5 + 0.28 * strength * agree - 0.10 * (vol > 0.9)
                p_right = min(0.92, max(0.35, p_right))
            direction = random.choice([1, -1])
            right = random.random() < p_right
            mag = abs(random.gauss(0, 0.03))
            fwd = mag * direction if right else -mag * direction
            out.append(MetaSample(
                {"abs_score": strength, "vol": vol,
                 "trend_agreement": agree, "regime": regime},
                direction, fwd))
        return out

    print("=" * 78)
    print("  META-LABELING — can we learn WHEN the primary signal is trustworthy?")
    print("=" * 78)
    for label, cond in [("primary has conditional structure", True),
                        ("primary is uniformly random", False)]:
        train, test = make(600, cond), make(400, cond)
        r = fit_report(train, test)
        print(f"\n  {label}")
        print(f"    primary hit rate      {r['primary_hit_rate']:.3f}")
        print(f"    meta Brier skill      {r['meta_calibration']['brier_skill']:+.4f}"
              f"   ECE {r['meta_calibration']['ece']:.3f}")
        print(f"    took {r['signals_taken']}/{r['n_test']}  "
              f"(refused {r['refusal_rate']:.0%})")
        print(f"    per-signal   all {r['mean_return_all_signals']:+.5f}"
              f"   taken {r['mean_return_taken']:+.5f}"
              f"   refused {r['mean_return_refused']:+.5f}")
        print(f"    delta from refusing  {r['improvement']:+.5f}"
              f"   ·  capital deployed {r['capital_deployed_frac']:.0%}")
        print(f"    weights {r['weights']}")
        print(f"    -> {r['verdict']}")
    print("\n  The second case is the control. If meta-labeling 'worked' there too,")
    print("  the method would be fitting noise and could not be trusted in the first.")


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if args.demo:
        _demo()
        return
    print("Meta-labeling needs (signal, features, forward return) triples.")
    print("The live loop records convictions but not yet their realised outcomes,")
    print("so there is nothing to fit. Run --demo to see the method and its control.")


if __name__ == "__main__":
    _main()
