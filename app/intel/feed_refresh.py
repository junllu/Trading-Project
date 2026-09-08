"""Bi-weekly refresh for curated sources — fetch new material, stage it for review.

Deliberately split into two halves:

  1. FETCH (unattended, safe to schedule) — pull each configured source's new
     posts/videos since the last run and stage the raw text on disk. Pure
     retrieval, no judgment, nothing touches the conviction engine.

  2. EXTRACT (attended) — turn staged text into dated SourceViews via
     `python -m app.intel.feeds add`. This needs judgment about what a source
     actually claimed, so it is NOT automated: an unattended extractor would
     quietly manufacture trading signal out of ambiguous commentary, which is
     exactly the failure mode this project keeps finding.

Configure sources in config/feed_sources.yaml:

    sources:
      - source: professor_jiang      # must match a KNOWN_SOURCES slot
        kind: youtube
        channel: UCxxxxxxxxxxxxxxxxxxxxxx
      - source: serenity
        kind: x                       # staged manually — see note below
        handle: "@example"

X/Twitter has no unauthenticated read path (WebFetch gets HTTP 402), so `x`
sources are listed for tracking only; stage their content by pasting it, or via
the Chrome extension against your logged-in session.

    python -m app.intel.feed_refresh fetch
    python -m app.intel.feed_refresh pending
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import ROOT
from .feeds import KNOWN_SOURCES, FeedStore

CONFIG_PATH = ROOT / "config" / "feed_sources.yaml"
STAGING_DIR = ROOT / "data" / "feeds" / "staging"
STATE_PATH = ROOT / "data" / "feeds" / "refresh_state.json"

REFRESH_DAYS = 14          # bi-weekly


@dataclass
class SourceConfig:
    source: str
    kind: str                       # "youtube" | "x"
    channel: str = ""
    handle: str = ""


def load_sources(path: Path | None = None) -> list[SourceConfig]:
    p = path or CONFIG_PATH
    if not p.exists():
        return []
    try:
        import yaml
    except Exception:
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for row in data.get("sources", []):
        src = str(row.get("source", ""))
        if src not in KNOWN_SOURCES:
            raise ValueError(f"feed_sources.yaml: unknown source '{src}'. Known: {KNOWN_SOURCES}")
        out.append(SourceConfig(source=src, kind=str(row.get("kind", "")),
                                channel=str(row.get("channel", "")),
                                handle=str(row.get("handle", ""))))
    return out


def _state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fetch(limit: int = 10) -> list[Path]:
    """Stage new raw material for every configured source. Returns staged files."""
    sources = load_sources()
    if not sources:
        print(f"No sources configured. Create {CONFIG_PATH} — see this module's docstring.")
        return []

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    state = _state()
    staged: list[Path] = []

    for cfg in sources:
        if cfg.kind != "youtube":
            print(f"[{cfg.source}] kind='{cfg.kind}' has no unattended fetch path — stage manually.")
            continue
        try:
            from .youtube_feed import fetch_channel
            videos = fetch_channel(cfg.channel, limit=limit, with_transcripts=True)
        except Exception as exc:
            print(f"[{cfg.source}] fetch FAILED: {exc}")
            continue

        seen = set(state.get(cfg.source, {}).get("seen_ids", []))
        new = [v for v in videos if v.video_id not in seen]
        for v in new:
            if not v.transcript:
                print(f"[{cfg.source}] {v.video_id} has no captions — skipped")
                continue
            f = STAGING_DIR / f"{cfg.source}_{v.published}_{v.video_id}.txt"
            f.write_text(f"source: {cfg.source}\ntitle: {v.title}\n"
                         f"published: {v.published}\nurl: {v.url}\n\n{v.transcript}",
                         encoding="utf-8")
            staged.append(f)
        state[cfg.source] = {"seen_ids": sorted(seen | {v.video_id for v in videos}),
                             "last_fetch": time.strftime("%Y-%m-%d %H:%M:%S")}
        print(f"[{cfg.source}] {len(new)} new of {len(videos)} recent videos")

    _save_state(state)
    if staged:
        print(f"\nStaged {len(staged)} file(s) in {STAGING_DIR}")
        print("Next (attended): review them, then record views with "
              "`python -m app.intel.feeds add ...`")
    return staged


def pending() -> list[Path]:
    """Staged files not yet turned into views (nothing deletes them automatically)."""
    if not STAGING_DIR.exists():
        return []
    return sorted(STAGING_DIR.glob("*.txt"))


def _main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_fetch = sub.add_parser("fetch", help="pull new material and stage it")
    p_fetch.add_argument("--limit", type=int, default=10)
    sub.add_parser("pending", help="list staged files awaiting extraction")
    sub.add_parser("status", help="feed freshness + staging backlog")
    args = ap.parse_args()

    if args.cmd == "fetch":
        fetch(args.limit)
    elif args.cmd == "pending":
        files = pending()
        print(f"{len(files)} staged file(s) awaiting extraction")
        for f in files:
            print(" ", f.name)
    elif args.cmd == "status":
        for src, st in FeedStore().status().items():
            flag = "STALE" if st["stale"] else "fresh"
            print(f"{src:16} views={st['views']:3}  newest={st['newest']}  [{flag}]")
        print(f"\nstaged awaiting extraction: {len(pending())}")
        print(f"refresh cadence: every {REFRESH_DAYS} days")


if __name__ == "__main__":
    _main()
