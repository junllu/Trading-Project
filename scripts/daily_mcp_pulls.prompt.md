Run the portal's queued Tier-2 research pulls, then stop. This is a scheduled,
unattended job: nobody is watching, so it is read-only market data plus one
snapshot file. It places no orders and runs no trading routine.

## What the job is

The portal (the brain) holds no broker credentials by design, so the Tier-2 data
its screener needs — peer valuation multiples and quarterly revenue/margin
history — can only be fetched by a Claude session with the `robinhood-trading`
MCP. `app/analytics/discovery.py` queues those pulls; nothing else can clear
them. That queue is what you are draining.

## Steps

1. Read `data/research/brief_latest.json` and take the list at `.mcp_queue`. If
   the file is missing, or the queue is empty, regenerate the brief first:

       .venv\Scripts\python.exe -m app.analytics.researcher --json

   If the queue is still empty after that, print `queue empty — nothing to do`
   and stop. That is a success, not a failure.

2. Each queue entry names a call, e.g. `get_equity_fundamentals(['CRWD'])` or
   `get_financials(['AMD'], quarterly, 8)`. Parse out the distinct symbols
   wanted for each of the two call types.

3. Fetch:
   - `mcp__robinhood-trading__get_equity_fundamentals` — max 10 symbols per
     call, so batch if needed.
   - `mcp__robinhood-trading__get_financials` — `period="quarterly"`,
     `limit=8`.

4. Write `data/fundamentals/<YYYY-MM-DD>.json` for today's date, matching the
   schema of the newest existing file in that directory. Read one first to
   confirm the shape. It is:

       {"snapshot_date", "market_date", "source", "note",
        "symbols": {"<TICKER>": {"pe_ratio", "pb_ratio", "market_cap_b",
                                 "high_52w", "high_52w_date", "low_52w",
                                 "sector",
                                 "quarters": [{"end", "revenue_m", "net_margin"}]}}}

   Rules that matter:
   - `market_cap_b` is market cap in BILLIONS; `revenue_m` is revenue in
     MILLIONS. Convert — do not paste raw figures.
   - `net_margin` from the API is already a percentage. Do not multiply by 100.
   - When trailing earnings are negative the reported P/E is meaningless: set
     `pe_ratio` to `null` and add a `pe_note` saying so. Never write a negative
     P/E as if it were a valuation.
   - A symbol that returns no data goes in the summary as "no data", not
     silently dropped, and not written with zeros.
   - Do not overwrite an existing dated file with fewer symbols than it already
     has — merge into it instead. These snapshots are point-in-time evidence.

4b. FILINGS BACKFILL — held names only, and only if time allows.

   Find what is missing:

       .venv\Scripts\python.exe -c "from app.intel.onboard import held_symbols, _snapshot_has; print([s for s in held_symbols() if not _snapshot_has(s,'filings')])"

   For **at most 5 symbols per run**, call `get_sec_filing_index(symbol)` then
   `get_sec_filing_facts` for: InventoryNet, CostOfGoodsAndServicesSold,
   CostOfRevenue, RevenueFromContractWithCustomerExcludingAssessedTax. Write
   them into `data/fundamentals/filings_<YYYY-MM-DD>.json`, same
   `{"symbols": {...}}` shape, MERGING with any existing file for today.

   Five per run is deliberate. Twenty-five symbols is ~50 calls and would make
   this job long enough to collide with the next scheduled run; the backlog
   drains over a week instead, and the queue is idempotent so nothing is lost.

   Skip any symbol that returns no filings (ETFs, ADRs and SPACs often have
   none) and record it as `{"no_filings": true}` so it is not retried forever.

5. Verify the snapshot is visible to the portal:

       .venv\Scripts\python.exe -c "from app.intel.onboard import _snapshot_has; print({s: _snapshot_has(s,'fundamentals') for s in ['<symbols you wrote>']})"

6. REGENERATE THE BRIEF. The dashboard reads a persisted artefact, not live
   state, so until the researcher re-runs it keeps displaying the queue as it
   was BEFORE this job drained it:

       .venv\Scripts\python.exe -m app.analytics.researcher

   Without this the pulls complete correctly and the page still says "9 MCP
   pulls queued", which reads as a broken automation rather than a stale
   render. Run it even when the queue was already empty.

7. Print one short summary line: which symbols were written, which returned no
   data, and the file path.

## Hard limits

- Place, modify or cancel NO orders. Exercise no options. You have no tools to
  do so and must not seek any.
- Do not run `app.agent`, the daily agent, or anything that routes signals to an
  executor.
- Do not edit code, config, or `.env`. The only file you write is the dated
  snapshot under `data/fundamentals/`.
