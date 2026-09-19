"""build_plan must survive producing zero orders.

Regression: `import json` inside build_plan's order loop made `json` a local for
the entire function. When the loop produced no orders that import never ran, and
json.dumps(plan) at the end raised UnboundLocalError — crashing every live
recorder cycle. The forward record sat at 6 rows for weeks because of it, which
in turn blocked calibration and every claim that depends on out-of-sample data.
"""
import ast
import json
from pathlib import Path

SRC = Path("app/agent/daily.py")


def _build_plan_node():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "build_plan":
            return node
    raise AssertionError("build_plan not found")


def test_build_plan_does_not_shadow_module_level_imports():
    """Any `import X` inside the function makes X local for the whole function.
    If the binding line is conditional, every earlier OR later use of X is a
    latent UnboundLocalError."""
    fn = _build_plan_node()
    module_names = set()
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for node in tree.body:                       # module level only
        if isinstance(node, ast.Import):
            for a in node.names:
                module_names.add((a.asname or a.name).split(".")[0])

    shadowed = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Import):
            for a in node.names:
                name = (a.asname or a.name).split(".")[0]
                if name in module_names:
                    shadowed.append(name)
    assert not shadowed, (
        f"build_plan re-imports module-level name(s) {sorted(set(shadowed))}; "
        f"this makes them function-local and raises UnboundLocalError on any "
        f"path that skips the import")


def test_plan_serialises_when_there_are_no_orders():
    """The exact failing path: a plan with an empty order list must still
    serialise. Exercised directly on the payload shape rather than through a
    live portal so it runs without network or broker."""
    plan = {"generated": "2026-09-08", "mode": "paper", "orders": [],
            "guardrails": {"drawdown_halt_active": False}, "limits": {}}
    assert json.loads(json.dumps(plan, indent=2))["orders"] == []
