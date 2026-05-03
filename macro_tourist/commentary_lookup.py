"""
Macro Tourist — Commentary Lookup Tool.

Fetches latest macro commentary from fintwit favorites via:
  1. YouTube Transcript API (Jim Bianco, Tony Greer on Blockworks/Forward Guidance)
  2. MacroVoices.com free transcript pages (Patrick Ceresna, Jim Bianco)
  3. Substack free RSS (Jared Dillian — The Daily Dirtnap free tier)
  4. Fallback: commentary_cache.json (last successful fetch)

Returns clean text excerpts ready for LLM context injection.
No API keys required — YouTube channel RSS is public.
"""
import os
import re
import json
import time
import logging
import requests
import feedparser
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(_DIR, "commentary_cache.json")

# ── Source Definitions ─────────────────────────────────────────────────────────

# YouTube channel RSS feeds (no API key — public XML endpoint)
YOUTUBE_CHANNELS = {
    "blockworks_macro":  "UCb-nZIBqS7VoQpQ8FuVW4qg",
    "forward_guidance":  "UCJyXMPNa27axwUvpPo4yPZA",
    "real_vision":       "UCBMR4mTFtjMctYx_lD1xFpA",
}

# Keywords that identify a relevant episode for each voice
TARGET_VOICES = {
    "jim_bianco": {
        "keywords":  ["bianco", "jim bianco"],
        "channels":  ["blockworks_macro", "forward_guidance", "real_vision"],
        "label":     "Jim Bianco",
    },
    "tony_greer": {
        "keywords":  ["tony greer", "morning navigator", "greer"],
        "channels":  ["blockworks_macro", "real_vision"],
        "label":     "Tony Greer",
    },
    "kevin_muir": {
        "keywords":  ["kevin muir", "market huddle", "macro tourist"],
        "channels":  ["real_vision"],
        "label":     "Kevin Muir",
    },
}

# MacroVoices episode list page — free professionally edited transcripts
MACROVOICES_EPISODES_URL = "https://www.macrovoices.com/podcast-episodes"
MACROVOICES_TRANSCRIPT_PATTERN = re.compile(
    r'href="(/\d{4}-\d{2}-\d{2}[^"]+)"[^>]*>[^<]*(?:jim bianco|patrick ceresna|tony greer|kevin muir)',
    re.IGNORECASE,
)

# Substack RSS for Jared Dillian (free preview tier)
DILLIAN_RSS = "https://jared-dillian.beehiiv.com/feed"
DILLIAN_LABEL = "Jared Dillian"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

MAX_TRANSCRIPT_CHARS = 2500
MAX_SUMMARY_CHARS = 800
FETCH_TIMEOUT = 15


# ── YouTube Channel RSS ────────────────────────────────────────────────────────

def _youtube_channel_rss(channel_id: str) -> list[dict]:
    """Parses YouTube channel RSS feed — returns list of {id, title, published}."""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:20]:
            video_id = entry.get("yt_videoid") or ""
            if not video_id:
                link = entry.get("link", "")
                m = re.search(r"v=([A-Za-z0-9_-]{11})", link)
                video_id = m.group(1) if m else ""
            if video_id:
                results.append({
                    "id": video_id,
                    "title": entry.get("title", ""),
                    "published": entry.get("published", ""),
                })
        return results
    except Exception as e:
        logging.warning(f"[commentary] YouTube RSS failed for {channel_id}: {e}")
        return []


def _fetch_youtube_transcript(video_id: str) -> str:
    """Fetches YouTube auto-generated transcript via youtube-transcript-api."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join(t["text"] for t in transcript)
    except ImportError:
        logging.warning("[commentary] youtube-transcript-api not installed")
        return ""
    except Exception as e:
        logging.debug(f"[commentary] transcript fetch failed for {video_id}: {e}")
        return ""


def _find_voice_in_youtube(voice_key: str, voice_cfg: dict) -> dict | None:
    """
    Searches target YouTube channels for recent episodes featuring the voice.
    Returns {label, source, date, excerpt} or None.
    """
    keywords = voice_cfg["keywords"]
    label = voice_cfg["label"]

    for channel_key in voice_cfg["channels"]:
        channel_id = YOUTUBE_CHANNELS.get(channel_key)
        if not channel_id:
            continue

        videos = _youtube_channel_rss(channel_id)
        for video in videos:
            title_lower = video["title"].lower()
            if any(kw in title_lower for kw in keywords):
                logging.info(f"[commentary] YouTube match: \"{video['title']}\" ({video['id']})")
                transcript = _fetch_youtube_transcript(video["id"])
                if transcript:
                    excerpt = _trim_transcript(transcript, label)
                    return {
                        "label": label,
                        "source": f"YouTube / {channel_key.replace('_', ' ').title()}",
                        "title": video["title"],
                        "date": _parse_date(video["published"]),
                        "excerpt": excerpt,
                    }
    return None


def _trim_transcript(text: str, speaker_label: str) -> str:
    """
    Returns the most informative slice of a transcript.
    Tries to find the section where the target speaker is introduced,
    then takes ~2500 chars from that point. Falls back to the first 2500 chars.
    """
    lower = text.lower()
    for kw in [speaker_label.lower(), speaker_label.split()[-1].lower()]:
        idx = lower.find(kw)
        if idx > 0:
            start = max(0, idx - 100)
            return text[start : start + MAX_TRANSCRIPT_CHARS].strip()
    return text[:MAX_TRANSCRIPT_CHARS].strip()


# ── MacroVoices Transcript Scrape ──────────────────────────────────────────────

def _fetch_macrovoices_latest() -> dict | None:
    """
    Scrapes MacroVoices episode list for the most recent episode with a known guest.
    Returns {label, source, title, date, excerpt} or None.
    """
    try:
        resp = requests.get(MACROVOICES_EPISODES_URL, headers=_HEADERS, timeout=FETCH_TIMEOUT)
        if resp.status_code != 200:
            return None

        html = resp.text.lower()

        # Find episodes mentioning our target voices
        target_names = ["jim bianco", "tony greer", "patrick ceresna", "kevin muir"]
        for name in target_names:
            idx = html.find(name)
            if idx < 0:
                continue

            # Walk back to find episode URL in surrounding anchor tags
            chunk = resp.text[max(0, idx - 500) : idx + 500]
            url_match = re.search(r'href="(https?://[^"]+macrovoices[^"]+)"', chunk, re.IGNORECASE)
            if not url_match:
                url_match = re.search(r'href="(/[^"]+)"', chunk, re.IGNORECASE)

            if not url_match:
                continue

            episode_path = url_match.group(1)
            if episode_path.startswith("/"):
                episode_url = "https://www.macrovoices.com" + episode_path
            else:
                episode_url = episode_path

            transcript_text = _fetch_macrovoices_transcript(episode_url)
            if not transcript_text:
                continue

            label = name.title()
            # Extract episode title from the chunk
            title_match = re.search(r'<[^>]+class="[^"]*title[^"]*"[^>]*>([^<]+)', chunk, re.IGNORECASE)
            episode_title = title_match.group(1).strip() if title_match else f"MacroVoices with {label}"

            return {
                "label": label,
                "source": "MacroVoices",
                "title": episode_title,
                "date": _today_et(),
                "excerpt": transcript_text[:MAX_TRANSCRIPT_CHARS].strip(),
            }

    except Exception as e:
        logging.warning(f"[commentary] MacroVoices scrape failed: {e}")
    return None


def _fetch_macrovoices_transcript(episode_url: str) -> str:
    """Fetches transcript text from a MacroVoices episode page."""
    try:
        resp = requests.get(episode_url, headers=_HEADERS, timeout=FETCH_TIMEOUT)
        if resp.status_code != 200:
            return ""

        html = resp.text

        # Look for a transcript link on the page
        transcript_links = re.findall(
            r'href="([^"]+(?:transcript|pdf)[^"]*)"',
            html,
            re.IGNORECASE,
        )
        for link in transcript_links:
            if link.endswith(".pdf"):
                # Skip PDFs — require extra parsing
                continue
            if link.startswith("/"):
                link = "https://www.macrovoices.com" + link
            try:
                tr = requests.get(link, headers=_HEADERS, timeout=FETCH_TIMEOUT)
                if tr.status_code == 200 and len(tr.text) > 200:
                    return _strip_html(tr.text)
            except Exception:
                continue

        # Fallback: extract text from the episode page itself
        # Remove script/style blocks
        clean = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r"<style[^>]*>.*?</style>", "", clean, flags=re.DOTALL | re.IGNORECASE)
        # Extract <p> tag content that looks like transcript content (long paragraphs)
        paragraphs = re.findall(r"<p[^>]*>(.{80,}?)</p>", clean, re.DOTALL | re.IGNORECASE)
        if paragraphs:
            text = " ".join(_strip_html(p) for p in paragraphs[:20])
            return text[:MAX_TRANSCRIPT_CHARS].strip()

    except Exception as e:
        logging.warning(f"[commentary] MacroVoices transcript fetch failed for {episode_url}: {e}")
    return ""


# ── Substack RSS (Jared Dillian) ───────────────────────────────────────────────

def _fetch_dillian_substack() -> dict | None:
    """Fetches latest free Jared Dillian post from Beehiiv/Substack RSS."""
    try:
        feed = feedparser.parse(DILLIAN_RSS)
        for entry in feed.entries[:5]:
            summary = entry.get("summary", "") or entry.get("content", [{}])[0].get("value", "")
            if not summary:
                continue
            clean = _strip_html(summary)
            clean = _strip_cta(clean)
            if len(clean) < 100:
                continue
            title = entry.get("title", "The Daily Dirtnap")
            pub = entry.get("published", "")
            return {
                "label": DILLIAN_LABEL,
                "source": "The Daily Dirtnap (free tier)",
                "title": title,
                "date": _parse_date(pub),
                "excerpt": clean[:MAX_SUMMARY_CHARS].strip(),
            }
    except Exception as e:
        logging.warning(f"[commentary] Dillian RSS failed: {e}")
    return None


# ── Cache ──────────────────────────────────────────────────────────────────────

def _load_cache() -> list[dict]:
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def _save_cache(entries: list[dict]) -> None:
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        logging.warning(f"[commentary] cache write failed: {e}")


# ── Utilities ──────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _strip_cta(text: str) -> str:
    """Remove paywall / subscribe CTA lines at the bottom of free previews."""
    _CTA_SIGNALS = (
        "if you like", "if you liked", "subscribe to read", "subscribe for",
        "to keep reading", "this post is for", "become a paid subscriber",
        "upgrade to paid", "get access to",
    )
    lines = text.splitlines()
    clean = []
    for line in lines:
        if any(sig in line.lower() for sig in _CTA_SIGNALS):
            break
        clean.append(line)
    return "\n".join(clean).strip()


def _parse_date(date_str: str) -> str:
    """Parses RFC 2822 or ISO date string to 'Mon DD, YYYY' format."""
    if not date_str:
        return _today_et()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str[:25], fmt)
            return dt.strftime("%b %-d, %Y")
        except ValueError:
            continue
    return date_str[:10]


def _today_et() -> str:
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7, hours=7)
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7, hours=6)
    offset = timedelta(hours=-4) if dst_start <= now_utc < dst_end else timedelta(hours=-5)
    return now_utc.astimezone(timezone(offset)).strftime("%b %-d, %Y")


# ── Main API ───────────────────────────────────────────────────────────────────

def get_macro_commentary(max_voices: int = 2) -> str:
    """
    Fetches latest macro commentary from fintwit favorites.
    Returns formatted string for LLM context injection.
    Falls back to cache if all live fetches fail.

    Format:
      MACRO COMMENTARY (Jim Bianco, Blockworks Macro, Apr 30):
      "...credit spreads widening while equities held — classic late-cycle tell..."

      MACRO COMMENTARY (Jared Dillian, The Daily Dirtnap, May 1):
      "...everyone is crowded into the same TLT trade right now..."
    """
    results = []

    # Source 1: YouTube transcripts for named voices
    for voice_key, voice_cfg in TARGET_VOICES.items():
        if len(results) >= max_voices:
            break
        logging.info(f"[commentary] searching YouTube for {voice_cfg['label']}...")
        found = _find_voice_in_youtube(voice_key, voice_cfg)
        if found:
            results.append(found)
        time.sleep(0.5)

    # Source 2: MacroVoices transcript scrape
    if len(results) < max_voices:
        logging.info("[commentary] checking MacroVoices...")
        found = _fetch_macrovoices_latest()
        if found and not any(r["label"] == found["label"] for r in results):
            results.append(found)

    # Source 3: Jared Dillian Substack RSS
    if len(results) < max_voices:
        logging.info("[commentary] checking Dillian RSS...")
        found = _fetch_dillian_substack()
        if found:
            results.append(found)

    # Save to cache if we got anything
    if results:
        _save_cache(results)
        logging.info(f"[commentary] cached {len(results)} entries")
    else:
        # Fallback to last cached results
        cached = _load_cache()
        if cached:
            logging.info(f"[commentary] using cached commentary ({len(cached)} entries)")
            results = cached[:max_voices]

    if not results:
        return ""

    blocks = []
    for r in results[:max_voices]:
        header = f"MACRO COMMENTARY ({r['label']}, {r['source']}, {r['date']}):"
        blocks.append(f"{header}\n\"{r['excerpt']}\"")

    return "\n\n".join(blocks)


if __name__ == "__main__":
    print("Testing macro commentary lookup...\n")
    commentary = get_macro_commentary(max_voices=2)
    if commentary:
        print(commentary)
    else:
        print("(no commentary retrieved — check network / sources)")
