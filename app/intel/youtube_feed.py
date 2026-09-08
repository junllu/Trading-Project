"""YouTube ingestion for curated analyst sources (e.g. Professor Jiang).

Pulls a channel's recent uploads via YouTube's public RSS feed (no API key, no
quota) and their transcripts via youtube-transcript-api. Returns dated raw
material — it deliberately does NOT decide what the videos *mean*.

That separation is on purpose: turning a transcript into a per-symbol bias is a
judgment call, and this session's whole discipline is that unvalidated judgment
shouldn't silently become a trading signal. Extraction happens explicitly (by
Claude in-session, or an analyst backend), and the resulting view is written to
app/intel/feeds.py with the video's real publish date so it stays point-in-time
honest.

    python -m app.intel.youtube_feed --channel https://www.youtube.com/@SomeHandle
    python -m app.intel.youtube_feed --channel UCxxxx --limit 5 --transcripts
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
WATCH_URL = "https://www.youtube.com/watch?v={vid}"


@dataclass
class Video:
    video_id: str
    title: str
    published: str                 # YYYY-MM-DD
    url: str = ""
    transcript: str = ""

    def to_dict(self) -> dict:
        return {"video_id": self.video_id, "title": self.title, "published": self.published,
                "url": self.url, "transcript_chars": len(self.transcript)}


def resolve_channel_id(channel: str) -> str:
    """Accept a raw channel id, an @handle, or any channel URL; return the UC... id."""
    c = channel.strip()
    if re.fullmatch(r"UC[\w-]{20,}", c):
        return c
    m = re.search(r"/channel/(UC[\w-]{20,})", c)
    if m:
        return m.group(1)

    # Handle (@name) resolution scrapes the channel page, which YouTube often
    # refuses to serve to non-browser clients (observed: 404 on valid handles).
    # The RSS + transcript path below works fine; only this lookup is fragile.
    import requests
    headers = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
               "Accept-Language": "en-US,en;q=0.9"}
    url = c if c.startswith("http") else f"https://www.youtube.com/{c.lstrip('/')}"
    try:
        r = requests.get(url, timeout=30, headers=headers, cookies={"CONSENT": "YES+cb"})
        if r.ok:
            for pat in (r'"channelId"\s*:\s*"(UC[\w-]{20,})"', r'/channel/(UC[\w-]{20,})'):
                m = re.search(pat, r.text)
                if m:
                    return m.group(1)
    except requests.RequestException:
        pass
    raise ValueError(
        f"could not resolve a channel id from {channel!r}. YouTube blocks handle lookups from "
        "non-browser clients. Pass the UC... channel id directly, or a URL containing "
        "/channel/UC... — open the channel in a browser, View Source, and search for "
        '"channelId". The RSS + transcript path works normally once you have the id.'
    )


def recent_videos(channel: str, limit: int = 10) -> list[Video]:
    """Recent uploads with real publish dates, via the channel's public RSS feed."""
    import xml.etree.ElementTree as ET

    import requests
    cid = resolve_channel_id(channel)
    r = requests.get(RSS_URL.format(cid=cid), timeout=30)
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    out: list[Video] = []
    for entry in ET.fromstring(r.text).findall("a:entry", ns)[:limit]:
        vid = entry.findtext("yt:videoId", default="", namespaces=ns)
        title = entry.findtext("a:title", default="", namespaces=ns)
        published = (entry.findtext("a:published", default="", namespaces=ns) or "")[:10]
        if vid:
            out.append(Video(video_id=vid, title=title, published=published,
                             url=WATCH_URL.format(vid=vid)))
    return out


def transcript(video_id: str, languages: tuple[str, ...] = ("en", "en-US", "zh-Hans", "zh")) -> str:
    """Full transcript text, or '' when captions aren't available."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        if hasattr(api, "fetch"):                       # 1.x instance API
            fetched = api.fetch(video_id, languages=list(languages))
            return " ".join(s.text for s in fetched).strip()
        # 0.6.x static API
        rows = YouTubeTranscriptApi.get_transcript(video_id, languages=list(languages))
        return " ".join(r["text"] for r in rows).strip()
    except Exception:
        return ""                                        # no captions / restricted — not fatal


def fetch_channel(channel: str, limit: int = 10, with_transcripts: bool = True) -> list[Video]:
    vids = recent_videos(channel, limit)
    if with_transcripts:
        for v in vids:
            v.transcript = transcript(v.video_id)
    return vids


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, help="@handle, channel URL, or UC... id")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--transcripts", action="store_true", help="also pull transcript text")
    ap.add_argument("--dump", help="write transcripts to this directory")
    args = ap.parse_args()

    vids = fetch_channel(args.channel, args.limit, with_transcripts=args.transcripts)
    for v in vids:
        mark = f"{len(v.transcript):>6} chars" if args.transcripts else "        -"
        print(f"{v.published}  {mark}  {v.video_id}  {v.title[:70]}")
    if args.dump and args.transcripts:
        from pathlib import Path
        d = Path(args.dump)
        d.mkdir(parents=True, exist_ok=True)
        for v in vids:
            if v.transcript:
                (d / f"{v.published}_{v.video_id}.txt").write_text(
                    f"{v.title}\n{v.url}\n\n{v.transcript}", encoding="utf-8")
        print(f"wrote transcripts to {d}")


if __name__ == "__main__":
    _main()
