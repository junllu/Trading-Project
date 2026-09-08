"""Local-LLM extraction — text in, VERIFIED structured facts out.

The one capability this system genuinely lacks. Everything else it does is
arithmetic on numbers someone already tagged: cost elasticity is a division,
vol rank is a percentile, Brier score is a mean. None of that wants a language
model, and putting one there would be strictly worse — slower, non-deterministic
and unauditable.

What it cannot do is read. An earnings call transcript, the MD&A section of a
10-K, a supply-chain note saying "inventories below ten days" — these carry the
operational detail the thesis ledger grades against, and no XBRL tag exposes
them. That is an LLM's job and nothing else's.

WHERE AN LLM BELONGS, AND WHERE IT MUST NOT GO

    MAKER      yes. Proposing structured facts from unstructured text.
    CHECKER    NEVER. A verdict has to be reproducible to be testable. If
               thesis_ledger's grade depends on a model's mood, the whole
               point of writing checkpoints in advance is gone — you can no
               longer be wrong in a way anyone can audit.
    COORDINATOR NEVER. Gating must be deterministic.

So this is a maker, and the maker/checker split applies to it too: the model
PROPOSES, deterministic code DISPOSES.

THE ANTI-HALLUCINATION GATE

A model asked for a number will produce one. That is the failure mode, and it
is silent — a hallucinated "inventory fell to 9 days" is indistinguishable from
a real one downstream, and it would flow into a macro fact and then into a
holding decision.

So every extracted value must appear VERBATIM in the source text. Anything that
does not is dropped and reported as unverified, never quietly kept. This turns
the model from an oracle into a locator: it finds where the number is, and the
verifier confirms the number is really there. A weak 3B model is entirely
adequate for locating, which is why hardware is not the constraint people
assume it is.

    python -m app.intel.llm_extract --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field

DEFAULT_URL = os.getenv("PORTAL_OLLAMA_URL", "http://localhost:11434")
# 3B by default, not 7B. On a 4GB card the 7B (~4.4GB) does not fit, and for
# locating a number in a passage the smaller model is not measurably worse.
DEFAULT_MODEL = os.getenv("PORTAL_LOCAL_LLM_MODEL", "qwen2.5:3b-instruct")


@dataclass
class Extraction:
    fields: dict[str, str] = field(default_factory=dict)      # verified only
    unverified: dict[str, str] = field(default_factory=dict)  # proposed, not found in source
    missing: list[str] = field(default_factory=list)          # model returned nothing
    model: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.fields)

    def to_dict(self) -> dict:
        return {"fields": self.fields, "unverified": self.unverified,
                "missing": self.missing, "model": self.model, "error": self.error,
                "verified_count": len(self.fields),
                "hallucination_rate": (round(len(self.unverified) /
                                             (len(self.fields) + len(self.unverified)), 3)
                                       if (self.fields or self.unverified) else None)}


def _normalise(s: str) -> str:
    """Compare numbers by their digits, ignoring formatting.

    '$1,030 million', '1030', and '1,030' are the same figure. Without this the
    verifier would reject correct extractions for cosmetic reasons and the whole
    gate would be useless.
    """
    return re.sub(r"[^0-9a-z.]", "", s.lower())


def verify(value: str, source: str) -> bool:
    """Does this value actually appear in the source text?"""
    v, src = _normalise(value), _normalise(source)
    if not v:
        return False
    if v in src:
        return True
    # A trailing '.0' from the model on an integer in the source is not a
    # hallucination; anything else is.
    return v.endswith(".0") and v[:-2] in src


def extract(text: str, fields: dict[str, str], model: str = DEFAULT_MODEL,
            base_url: str = DEFAULT_URL, timeout: float = 120.0) -> Extraction:
    """Pull `fields` (name -> description) out of `text`, keeping only verified ones."""
    out = Extraction(model=model)
    if not text.strip():
        out.error = "empty source text"
        return out

    schema = "\n".join(f'  "{k}": {v}' for k, v in fields.items())
    prompt = (
        "Extract values from the PASSAGE below.\n"
        "Rules:\n"
        "  - Copy values EXACTLY as they appear in the passage. Do not convert units, "
        "round, or reformat.\n"
        "  - If a value is not stated in the passage, use null. Never guess.\n"
        "  - Reply with ONE JSON object and nothing else.\n\n"
        f"Fields:\n{{\n{schema}\n}}\n\nPASSAGE:\n\"\"\"\n{text}\n\"\"\"\n"
    )
    try:
        import requests
        r = requests.post(f"{base_url}/api/generate", timeout=timeout, json={
            "model": model, "prompt": prompt, "stream": False,
            # Temperature 0: this is a locating task, not a creative one, and a
            # reproducible maker is worth more than a fluent one.
            "options": {"temperature": 0.0, "num_predict": 400},
            "format": "json",
        })
        r.raise_for_status()
        raw = (r.json().get("response") or "").strip()
    except Exception as exc:
        out.error = f"{type(exc).__name__}: {exc}"
        return out

    try:
        proposed = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            out.error = f"model did not return JSON: {raw[:120]}"
            return out
        try:
            proposed = json.loads(m.group(0))
        except json.JSONDecodeError:
            out.error = f"unparseable JSON: {raw[:120]}"
            return out

    for key in fields:
        val = proposed.get(key)
        if val is None or val == "":
            out.missing.append(key)
            continue
        val = str(val)
        if verify(val, text):
            out.fields[key] = val
        else:
            # The important branch. Kept visible rather than silently dropped,
            # because the RATE of these is the only honest measure of whether
            # this model can be trusted on this kind of text.
            out.unverified[key] = val
    return out


def available(base_url: str = DEFAULT_URL) -> tuple[bool, str]:
    try:
        import requests
        r = requests.get(f"{base_url}/api/tags", timeout=3)
        names = [m["name"] for m in r.json().get("models", [])]
        return bool(names), ", ".join(names) or "no models pulled"
    except Exception as exc:
        return False, f"{type(exc).__name__}"


def report() -> dict:
    """Agent entrypoint — is local extraction available, and on what."""
    ok, detail = available()
    return {"available": ok, "models": detail, "default_model": DEFAULT_MODEL,
            "role": "MAKER only — proposes facts; deterministic checkers judge them",
            "gate": "every value must appear verbatim in the source or it is dropped"}


# A real passage, with real numbers, and one trap.
_SELFTEST_TEXT = """
Micron reported fiscal Q3 revenue of $41,456 million, up from $9,301 million a
year earlier. Cost of goods sold was $6,400 million. Inventories stood at
$8,567 million at quarter end. Management noted that DRAM revenue reached
$31,328 million and declined to give a specific figure for 2027 bit supply
growth, saying only that capacity remains tight.
"""


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    ok, detail = available()
    print("=" * 76)
    print("  LOCAL LLM EXTRACTION — maker only, verified against the source")
    print("=" * 76)
    print(f"  ollama    {'up' if ok else 'DOWN'}   ·   {detail}")
    print(f"  model     {args.model}")
    if not (ok and args.selftest):
        return

    print("\n  self-test on a passage with a deliberate trap: the 2027 bit-supply")
    print("  figure is NOT stated, so a model that invents one must be caught.\n")
    res = extract(_SELFTEST_TEXT, {
        "revenue": "total revenue for the quarter, with units as written",
        "cogs": "cost of goods sold, as written",
        "inventory": "inventory balance at quarter end, as written",
        "dram_revenue": "DRAM segment revenue, as written",
        "bit_supply_growth_2027": "the 2027 bit supply growth percentage",
    }, model=args.model)

    if res.error:
        print(f"  ERROR {res.error}")
        return
    for k, v in res.fields.items():
        print(f"  [verified  ] {k:26} {v}")
    for k, v in res.unverified.items():
        print(f"  [HALLUCINATED] {k:24} {v}   <- not in the source, DROPPED")
    for k in res.missing:
        print(f"  [correctly null] {k:22} not stated in the passage")
    d = res.to_dict()
    print(f"\n  verified {d['verified_count']}   hallucination rate {d['hallucination_rate']}")
    print("\n  The trap field should be `correctly null`. If it is HALLUCINATED, the")
    print("  gate caught it and nothing entered the system — which is the design.")


if __name__ == "__main__":
    _main()
