List the agents I can talk to, and route me to one.

Run this to show the roster and each agent's charter:

```
.venv\Scripts\python.exe -m app.intel.agent_chat --list
```

Then show me the table below and tell me which commands exist.

| command | agent | what it owns |
|---|---|---|
| `/researcher` | researcher | outward coverage — names I do NOT own, in live themes |
| `/chief` | chief | the one decision queue; routes and gates every checker |
| `/program` | program manager | pace to $1M, and what is blocking delivery |
| `/exits` | exit monitor | every held position against its own exit plan |
| `/thomas` | thomas | execution — the only seat that touches real money |

Two rules that apply to every one of these, and that you must not let a
conversation erode:

1. **Ask the agent, not the oracle.** Each command answers from that agent's own
   `report()`. If the report does not contain the answer, the answer is "the
   report does not say" — never a plausible reconstruction.
2. **No agent rules on whether something works.** That question is
   DETERMINISTIC (see `app/intel/routing.py`) and belongs to deflated Sharpe,
   walk-forward folds and `confidence.py`. Say so and name the decider.

For cheap follow-up questions about an agent's existing report, use the local
model rather than spending a cloud call:

```
.venv\Scripts\python.exe -m app.intel.agent_chat <agent> "<question>"
```

That runs on local Ollama (qwen2.5), costs nothing, and is constrained to answer
only from the report. Reserve your own reasoning for the hard judgement:
synthesis, anomalies, and proposing what to change.
