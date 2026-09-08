"""Webull OFFICIAL OpenAPI adapter — read-only.

Supersedes the credential-scraping approach in brokers/webull.py, which drives
the unofficial `webull` package with your account username, password and trade
PIN. This one uses Webull's official OpenAPI: an App Key / App Secret pair you
generate at developer.webull.com and can revoke, with no account password
involved. Both packages install a top-level `webull` namespace and currently
coexist, but prefer this path.

DELIBERATELY READ-ONLY. The SDK also exposes place_order / cancel_order /
replace_order; none of it is wired here. Webull holds ~38% of the book in an
account nothing else can reach, so the value is an accurate consolidated
position — not another way to fire orders. Adding write access should be a
separate, explicit decision with its own guardrails (Webull supports
WEBULL_SYMBOL_WHITELIST, WEBULL_MAX_ORDER_NOTIONAL_USD, WEBULL_MAX_ORDER_QUANTITY).

Credentials come from the environment only — never arguments, never committed:

    WEBULL_APP_KEY=...
    WEBULL_APP_SECRET=...
    WEBULL_REGION_ID=us
    WEBULL_ENVIRONMENT=prod        # or uat for the sandbox

    python -m app.brokers.webull_openapi accounts
    python -m app.brokers.webull_openapi positions
    python -m app.brokers.webull_openapi holdings-yaml
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass


class WebullAuthError(RuntimeError):
    pass


@dataclass
class WebullOpenAPI:
    app_key: str = ""
    app_secret: str = ""
    region_id: str = "us"

    def __post_init__(self) -> None:
        # Load .env ourselves — this module has a __main__ entry point, so it can
        # run without app.config (which is what normally calls load_dotenv).
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass
        self.app_key = self.app_key or os.getenv("WEBULL_APP_KEY", "")
        self.app_secret = self.app_secret or os.getenv("WEBULL_APP_SECRET", "")
        self.region_id = self.region_id or os.getenv("WEBULL_REGION_ID", "us")
        self._api = None
        self._account = None

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    def _client(self):
        if not self.configured:
            raise WebullAuthError(
                "WEBULL_APP_KEY / WEBULL_APP_SECRET are not set. Generate an API key pair at "
                "developer.webull.com and put them in .env (which is git-ignored). "
                "Do not paste them into chat."
            )
        if self._api is None:
            from webull.core.client import ApiClient
            from webull.trade.trade_client import AccountV2, ClientInitializer
            api = ApiClient(self.app_key, self.app_secret, self.region_id)
            ClientInitializer.initializer(api)
            self._api = api
            self._account = AccountV2(api)
        return self._api

    def accounts(self) -> list[dict]:
        self._client()
        return _payload(self._account.get_account_list())

    def positions(self, account_id: str) -> list[dict]:
        self._client()
        return _payload(self._account.get_account_position(account_id))

    def balance(self, account_id: str) -> dict:
        self._client()
        return _payload(self._account.get_account_balance(account_id))

    def activities(self, account_id: str, page_size: int = 100) -> list[dict]:
        """Transaction history — the multi-year record the CSV export can't reach."""
        self._client()
        from webull.trade.trade_client import Activity
        return _payload(Activity(self._api).get_activities(account_id, page_size=page_size))


def _payload(resp):
    """SDK responses wrap the useful bit differently across endpoints."""
    if resp is None:
        return []
    for attr in ("data", "result", "json"):
        v = getattr(resp, attr, None)
        if callable(v):
            try:
                v = v()
            except Exception:
                v = None
        if v is not None:
            return v
    return resp


def _main() -> None:
    # .env MUST be loaded before argparse computes its defaults. It used to be
    # loaded inside WebullOpenAPI.__post_init__, which runs several lines later —
    # so --account-id always defaulted to "" no matter what .env said, and every
    # command silently fell through to "first account returned". That is the
    # $7.94 Roth IRA, not the $53k individual account, and it fails by printing
    # an empty position list rather than an error.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Webull official OpenAPI (read-only).")
    ap.add_argument("cmd", choices=["auth", "accounts", "positions", "balance", "activities",
                                    "holdings-yaml"])
    ap.add_argument("--account-id", default=os.getenv("WEBULL_ACCOUNT_ID", ""))
    args = ap.parse_args()

    wb = WebullOpenAPI()
    if not wb.configured:
        print("Webull OpenAPI not configured. Set WEBULL_APP_KEY and WEBULL_APP_SECRET in .env")
        print("Generate them at developer.webull.com, then run: webull-skill auth  (2FA)")
        return

    if args.cmd == "auth":
        # The SDK creates a token then POLLS (default 300s) waiting for you to
        # approve it out-of-band in the Webull app. It prints nothing while it
        # waits, which looks like a hang — hence the explicit instructions here.
        print("Webull OpenAPI authorization")
        print("-" * 60)
        print("1. Have the Webull APP open on your phone, ready to approve.")
        print("2. This will request a token, then poll for up to 5 minutes.")
        print("3. Approve the API authorization request when it appears in the app")
        print("   (enter the verification code the app shows you — IN THE APP,")
        print("    not here and not in chat).")
        print("-" * 60)
        print("requesting token, polling for approval...", flush=True)
        try:
            wb._client()
        except Exception as exc:
            msg = str(exc)
            print(f"\nFAILED: {type(exc).__name__}: {msg[:300]}")
            if "EXPIRED" in msg:
                print("\n-> The approval window lapsed. Re-run this command and approve")
                print("   in the Webull app promptly (within ~5 minutes).")
            elif "SIGNATURE" in msg.upper() or "401" in msg:
                print("\n-> Looks like a key/secret or region mismatch. Check WEBULL_APP_KEY,")
                print("   WEBULL_APP_SECRET and WEBULL_REGION_ID in .env.")
            return
        print("\nAuthorized. Token cached locally; later commands should work without re-auth.")
        return

    if args.cmd == "accounts":
        for a in wb.accounts():
            print(a)
        return

    acct = args.account_id
    if not acct:
        accts = wb.accounts()
        if not accts:
            print("no accounts returned")
            return
        # Never guess across multiple accounts. Picking the first one returns an
        # empty position list for an empty account, which reads as "no positions"
        # rather than "wrong account" — the book then looks liquidated.
        if len(accts) > 1:
            print(f"WEBULL_ACCOUNT_ID is not set and {len(accts)} accounts exist. "
                  f"Refusing to guess.\n")
            for a in accts:
                if isinstance(a, dict):
                    print(f"  {a.get('account_id') or a.get('accountId')}  "
                          f"number={a.get('account_number') or a.get('accountNumber')}  "
                          f"{a.get('account_class') or a.get('accountClass') or ''}")
            print("\nSet WEBULL_ACCOUNT_ID in .env, or pass --account-id.")
            return
        acct = (accts[0].get("account_id") or accts[0].get("accountId")
                if isinstance(accts[0], dict) else str(accts[0]))
        print(f"(using the only account: {acct})")

    if args.cmd == "positions":
        for p in wb.positions(acct):
            print(p)
    elif args.cmd == "balance":
        print(wb.balance(acct))
    elif args.cmd == "activities":
        for a in wb.activities(acct):
            print(a)
    elif args.cmd == "holdings-yaml":
        # Emit rows ready to paste into config/holdings.yaml
        for p in wb.positions(acct):
            if not isinstance(p, dict):
                continue
            sym = p.get("symbol") or p.get("ticker") or "?"
            qty = p.get("quantity") or p.get("position") or 0
            avg = p.get("costPrice") or p.get("avgCost") or p.get("cost_price") or 0
            last = p.get("lastPrice") or p.get("marketValue") or avg
            print(f"  - {{symbol: {sym}, shares: {qty}, avg_price: {avg}, "
                  f"last: {last}, broker: webull}}")


if __name__ == "__main__":
    _main()
