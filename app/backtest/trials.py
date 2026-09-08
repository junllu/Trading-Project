"""Trial registry and Deflated Sharpe — how to iterate WITHOUT fooling yourself.

The campaign's central finding is that the tuned strategy beat buy-and-hold in
7 of 34 out-of-sample folds with a ~21pp gap. The instinct after a result like
that is to iterate: try more setups, more parameters, more features. That
instinct is correct and it is also how the gap got there.

The mechanism is not subtle. Run N independent strategies on the same data and
the BEST one's Sharpe is inflated by selection alone — with N=100 the maximum
of N draws from a zero-mean distribution sits around 2.5 standard deviations
up. You will find a 1.8 Sharpe in pure noise if you look 100 times. Every
backtest framework reports that 1.8. Almost none report that you looked 100
times, which is the only number that makes 1.8 interpretable.

So this module does two things:

  1. RECORDS every trial, automatically, to data/trials.jsonl. The trial count
     is then a fact on disk rather than a recollection. You cannot under-report
     N to yourself if the tool counts.

  2. DEFLATES the observed Sharpe by that count.

        PSR  Probabilistic Sharpe Ratio — probability the true Sharpe exceeds a
             benchmark, correcting for track length, skew and fat tails.
             Bailey & Lopez de Prado (2012).
        DSR  Deflated Sharpe Ratio — PSR where the benchmark is the Sharpe you
             would EXPECT as the maximum of N trials on zero-edge data.
             Bailey & Lopez de Prado (2014).

A DSR of 0.95 says: given you tried N things, on a track this short, with these
tails, there is a 95% chance the edge is real. A DSR of 0.4 says the result is
indistinguishable from the best of N coin flips — regardless of how good the
equity curve looks.

Two properties worth internalising, because they are the whole point:

  - DSR FALLS as you run more trials on the same data. Iteration has a price
    and this is it, made explicit. The only way to raise DSR honestly is a
    longer track, a bigger effect, or fewer things tried.
  - Non-normality is penalised. Negative skew and fat tails (the shape of a
    strategy that grinds out small wins and occasionally detonates) inflate a
    naive Sharpe. The correction removes that.

    python -m app.backtest.trials              # what have we actually tried?
    python -m app.backtest.trials --sharpe 1.8 --periods 252
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics as st
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import ROOT

TRIALS_PATH = ROOT / "data" / "trials.jsonl"

EULER_MASCHERONI = 0.5772156649015329


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation, ~1e-9 accurate)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > p_high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# A Sharpe needs enough observations for its denominator to mean anything. With
# two similar readings the standard deviation collapses and the ratio explodes:
# a 2-fold backtest of the discovery screen produced a "Sharpe" of 12.15, which
# then became the registry's best result and dominated the deflation. The number
# was never real — it was 1/small.
MIN_OBSERVATIONS_FOR_SHARPE = 8


def sharpe_of(returns: list[float], periods_per_year: int = 252) -> float:
    """Annualised Sharpe. Returns 0.0 when the sample is too small to support one.

    Zero rather than None on purpose: a degenerate configuration was still
    EXAMINED, so it must keep counting toward the trial count N. It simply must
    not be allowed to win. Dropping it instead would quietly shrink N and make
    every surviving result look better than the search justifies.
    """
    if len(returns) < MIN_OBSERVATIONS_FOR_SHARPE:
        return 0.0
    mean = st.mean(returns)
    sd = st.pstdev(returns)
    return (mean / sd) * math.sqrt(periods_per_year) if sd else 0.0


def _moments(returns: list[float]) -> tuple[float, float]:
    """Sample skew and kurtosis (NON-excess: normal == 3.0)."""
    n = len(returns)
    if n < 4:
        return 0.0, 3.0
    mean = st.mean(returns)
    sd = st.pstdev(returns)
    if sd == 0:
        return 0.0, 3.0
    skew = sum((r - mean) ** 3 for r in returns) / (n * sd ** 3)
    kurt = sum((r - mean) ** 4 for r in returns) / (n * sd ** 4)
    return skew, kurt


def probabilistic_sharpe(observed_sr: float, benchmark_sr: float, n_periods: int,
                         skew: float = 0.0, kurtosis: float = 3.0) -> float:
    """P(true Sharpe > benchmark), adjusted for track length and non-normality.

    The denominator is the standard error of the Sharpe estimator. Negative skew
    and fat tails INFLATE it, which lowers the probability — exactly the
    correction a naive annualised Sharpe fails to make for a strategy that wins
    small and often and loses big and rarely.
    """
    if n_periods < 2:
        return 0.0
    var = 1.0 - skew * observed_sr + ((kurtosis - 1.0) / 4.0) * observed_sr ** 2
    if var <= 0:
        return 0.0
    return _norm_cdf((observed_sr - benchmark_sr) * math.sqrt(n_periods - 1) / math.sqrt(var))


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """The Sharpe you EXPECT as the best of N trials when true edge is zero.

    This is the number that makes a backtest interpretable. Its growth in N is
    the reason iteration is expensive: each additional variant raises the bar
    the winner has to clear before it means anything.
    """
    if n_trials < 2 or sharpe_variance <= 0:
        return 0.0
    sd = math.sqrt(sharpe_variance)
    g = EULER_MASCHERONI
    return sd * ((1 - g) * _norm_ppf(1 - 1.0 / n_trials)
                 + g * _norm_ppf(1 - 1.0 / (n_trials * math.e)))


def deflated_sharpe(observed_sr: float, n_periods: int, n_trials: int,
                    sharpe_variance: float, skew: float = 0.0,
                    kurtosis: float = 3.0) -> dict:
    """PSR against the expected-maximum-of-N benchmark. The honest number."""
    bench = expected_max_sharpe(n_trials, sharpe_variance)
    dsr = probabilistic_sharpe(observed_sr, bench, n_periods, skew, kurtosis)
    return {
        "observed_sharpe": round(observed_sr, 3),
        "n_trials": n_trials,
        "n_periods": n_periods,
        "expected_max_sharpe_under_no_edge": round(bench, 3),
        "deflated_sharpe_ratio": round(dsr, 4),
        "psr_vs_zero": round(probabilistic_sharpe(observed_sr, 0.0, n_periods, skew, kurtosis), 4),
        "skew": round(skew, 3),
        "kurtosis": round(kurtosis, 3),
        "verdict": _verdict(dsr, observed_sr, bench),
    }


def _verdict(dsr: float, observed: float, bench: float) -> str:
    if observed <= bench:
        return (f"REJECT — the observed Sharpe {observed:.2f} does not even exceed the "
                f"{bench:.2f} expected from selection alone. This is the best of N "
                f"coin flips.")
    if dsr >= 0.95:
        return "PASS — survives deflation at 95%. Defensible as skill on this evidence."
    if dsr >= 0.80:
        return "WEAK — positive after deflation but under 95%. Not enough to size on."
    return (f"REJECT — DSR {dsr:.2f}. Selection across trials explains this result "
            f"more simply than edge does.")


# --- the registry ----------------------------------------------------------

@dataclass
class Trial:
    """One evaluated configuration. Recorded whether or not it looked good.

    Recording ONLY the promising runs is the failure this guards against: N
    must count everything examined, including the variants abandoned after a
    glance, or the deflation is against a number that flatters the survivor.
    """
    strategy: str
    params: dict
    sharpe: float
    cagr: float = 0.0
    max_drawdown: float = 0.0
    n_periods: int = 0
    window: str = ""
    note: str = ""
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def config_id(self) -> str:
        """Identifies the HYPOTHESIS — strategy plus parameters, no window.

        This is what N counts. Re-scoring one configuration across 34
        walk-forward folds is one hypothesis examined 34 times, not 34
        hypotheses; folding it in would inflate N and flatter the result by
        raising a bar that selection never actually had to clear.
        """
        raw = json.dumps({"s": self.strategy, "p": self.params}, sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    @property
    def fingerprint(self) -> str:
        """Identifies this exact RUN — config plus window. Provenance only."""
        raw = json.dumps({"c": self.config_id, "w": self.window}, sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return dict(self.__dict__) | {"config_id": self.config_id,
                                      "fingerprint": self.fingerprint}


def record(trial: Trial) -> None:
    TRIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRIALS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(trial.to_dict()) + "\n")


def load_trials() -> list[dict]:
    if not TRIALS_PATH.exists():
        return []
    out = []
    for line in TRIALS_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def registry_summary(strategy: str | None = None) -> dict:
    """What have we ACTUALLY tried — and what does the best result survive?"""
    rows = load_trials()
    if strategy:
        rows = [r for r in rows if r.get("strategy") == strategy]
    if not rows:
        return {"n_trials": 0, "note": "no trials recorded — nothing to deflate against"}

    sharpes = [float(r.get("sharpe", 0.0)) for r in rows]
    # N counts distinct HYPOTHESES (config_id), not runs. One configuration
    # scored across 34 folds is one hypothesis, examined 34 times.
    n_distinct = len({r.get("config_id") for r in rows if r.get("config_id")})
    best = max(rows, key=lambda r: float(r.get("sharpe", 0.0)))
    var = st.pvariance(sharpes) if len(sharpes) > 1 else 0.0

    d = deflated_sharpe(
        observed_sr=float(best.get("sharpe", 0.0)),
        n_periods=int(best.get("n_periods", 0)) or 252,
        n_trials=n_distinct,
        sharpe_variance=var,
    )
    return {"n_trials": n_distinct, "n_records": len(rows),
            "sharpe_variance": round(var, 5),
            "best": {"strategy": best.get("strategy"), "params": best.get("params"),
                     "window": best.get("window")},
            **d}


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--sharpe", type=float, default=None,
                    help="deflate an ad-hoc Sharpe against the recorded trial count")
    ap.add_argument("--periods", type=int, default=252)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    s = registry_summary(args.strategy)
    if args.sharpe is not None and s.get("n_trials"):
        s = {**s, **deflated_sharpe(args.sharpe, args.periods, s["n_trials"],
                                    s.get("sharpe_variance", 0.0) or 0.01)}
    if args.json:
        print(json.dumps(s, indent=2))
        return

    print("=" * 74)
    print("  TRIAL REGISTRY — what we tried, and what survives deflation")
    print("=" * 74)
    if not s.get("n_trials"):
        print("\n  No trials recorded yet.\n")
        print("  Until sweeps write here, the trial count is a recollection rather")
        print("  than a fact, and no Sharpe from this project can be deflated honestly.")
        print("\n  The cost of iteration, over 252 periods with Sharpe dispersion 0.5.")
        print("  Two candidates: an exceptional result and an ordinary-good one.\n")
        print(f"    {'trials':>7}  {'bar':>6}   {'SR 1.80':>18}   {'SR 1.00':>18}")
        for n in (2, 10, 50, 200, 1000):
            strong = deflated_sharpe(1.80, 252, n, 0.25)
            typical = deflated_sharpe(1.00, 252, n, 0.25)
            bar = strong["expected_max_sharpe_under_no_edge"]
            def cell(d):
                return ("beaten by noise" if d["observed_sharpe"] <= bar
                        else f"DSR {d['deflated_sharpe_ratio']:.3f}")
            print(f"    {n:>7}  {bar:>6.2f}   {cell(strong):>18}   {cell(typical):>18}")
        print("\n  A Sharpe of 1.80 survives a thousand looks. A Sharpe of 1.00 — the far")
        print("  more common result, and roughly what a good equity curve produces — is")
        print("  indistinguishable from noise once you have examined ~50 variants.")
        print("  That is the price of a search, and it is why N must be counted.")
        return

    print(f"\n  distinct configurations tried : {s['n_trials']}   ({s['n_records']} records)")
    print(f"  best strategy                 : {s['best']['strategy']}  {s['best']['params']}")
    print(f"  observed Sharpe               : {s['observed_sharpe']}")
    print(f"  bar set by selection alone    : {s['expected_max_sharpe_under_no_edge']}")
    print(f"  PSR vs zero                   : {s['psr_vs_zero']}")
    print(f"  DEFLATED SHARPE RATIO         : {s['deflated_sharpe_ratio']}")
    print(f"\n  {s['verdict']}")


if __name__ == "__main__":
    _main()
