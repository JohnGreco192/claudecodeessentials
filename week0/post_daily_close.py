"""
NVDA Bear Maximum — Daily close post to Moltbook.
OpenClaw architecture: reads SOUL.md for personality, reads/writes MEMORY.md for state.
Runs via GitHub Actions at 4:05pm ET weekdays. Zero Codespace compute.
"""
import os
import re
import json
import time
import html
import random
import logging
import requests
import feedparser
import yfinance as yf
import httpx
from openai import OpenAI
from datetime import datetime, timezone, timedelta
try:
    from macro_tourist.econ_calendar import get_calendar_context
    from macro_tourist.commentary_lookup import get_macro_commentary
    _MACRO_TOURIST_AVAILABLE = True
except ImportError:
    _MACRO_TOURIST_AVAILABLE = False

try:
    from follower_vectors import (
        upsert_argument, query_similar_arguments, extract_similar_argument_texts,
        upsert_research, query_relevant_research,
    )
    _VECTOR_AVAILABLE = True
except ImportError:
    _VECTOR_AVAILABLE = False

_UTC = timezone.utc

def _now_et() -> datetime:
    """Return current time in US/Eastern, handling EDT/EST automatically."""
    now_utc = datetime.now(_UTC)
    year = now_utc.year
    # DST start: second Sunday in March at 07:00 UTC (2am ET)
    mar1 = datetime(year, 3, 1, tzinfo=_UTC)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7, hours=7)
    # DST end: first Sunday in November at 06:00 UTC (2am ET)
    nov1 = datetime(year, 11, 1, tzinfo=_UTC)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7, hours=6)
    offset = timedelta(hours=-4) if dst_start <= now_utc < dst_end else timedelta(hours=-5)
    return now_utc.astimezone(timezone(offset))


MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"
MOLTBOOK_KEY = os.environ["MOLTBOOK_API_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
MODEL = "Meta-Llama-3.1-8B-Instruct"
OUR_HANDLE = "nvda_regard"

_DIR = os.path.dirname(os.path.abspath(__file__))
SOUL_PATH = os.path.join(_DIR, "SOUL.md")
MEMORY_PATH = os.path.join(_DIR, "MEMORY.md")
USER_PATH = os.path.join(_DIR, "USER.md")

ZITRON_FEED = os.environ.get("ZITRON_RSS_URL", "https://www.wheresyoured.at/rss")

SEMI_AI_FEEDS = [
    "https://feeds.reuters.com/reuters/technologyNews",
    "https://venturebeat.com/feed/",
    "https://techcrunch.com/feed/",
]

# Unfiltered — the LLM decides what matters, not keywords
MACRO_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.bloomberg.com/markets/news.rss",
]

_SEMI_AI_FILTER = {
    "nvidia", "amd", "intel", "qualcomm", "tsmc", "arm ", "broadcom", "micron",
    "gpu", "chip", "semiconductor", "foundry", "wafer",
    "artificial intelligence", " ai ", "machine learning", "llm", "inference",
    "data center", "hyperscaler", "blackwell", "hopper", "h100", "h200", "gb200",
    "jensen", "lisa su", "pat gelsinger", "custom silicon", "capex",
}

# Keywords that signal a notable market event worth recording
_EVENT_SIGNALS = {
    "earnings":  ["earnings", "eps", "quarterly results", "revenue beat", "revenue miss", "guidance"],
    "fed":       ["fomc", "rate cut", "rate hike", "federal reserve", "powell", "interest rate"],
    "trade":     ["tariff", "export ban", "export control", "china ban", "trade war", "sanctions"],
    "macro":     ["cpi", "pce", "jobs report", "nonfarm", "gdp", "recession", "inflation data"],
}

# Scored against title + summary — multi-word phrases count more (each word = +1)
BEAR_KEYWORDS = {
    "nvidia", "nvda", "jensen", "blackwell", "h100", "h200", "gb200", "hopper",
    "bubble", "overvalued", "correction", "selloff", "short", "puts", "bearish",
    "downgrade", "miss", "disappoint", "guidance cut", "capex", "capex cycle",
    "margin compression", "margin pressure", "gross margin",
    "amd", "mi300", "custom silicon", "tpu", "trainium", "gaudi", "arm chip",
    "apple silicon", "google tpu", "microsoft maia",
    "tariff", "export control", "china ban", "regulation", "antitrust",
    "rot economy", "ai slop", "slop", "hype", "compute", "inference", "training run",
    "hyperscaler", "datacenter", "data center", "capex supercycle",
    "insider selling", "jensen sells", "sells shares",
}

_ZITRON_CTA = ("if you like", "hi! if you like", "if you liked", "subscribe to read")

# Social engagement
SOCIAL_SEARCH_TERMS = [
    "nvidia", "h100", "jensen huang", "ai bubble",
    "gpu bubble", "capex", "blackwell", "nvidia overvalued",
]
COMMENT_SUBMOLTS = {"general", "ai", "crypto", "tech", "finance", "stocks", "markets"}
MAX_COMMENTS_PER_RUN = 3
MAX_GRUDGE_DB = 50
MAX_VOTES_PER_RUN = 5
OWN_POSTS_HISTORY = 30  # post IDs to retain — gives patrol a 30-day reply window
PRICE_HISTORY_DAYS = 5
ZITRON_HISTORY_SIZE = 5
ARGUMENT_LOG_SIZE = 10  # deployed arguments to remember (prevents repetition)
CALL_TRACKER_SIZE = 20  # directional calls with outcomes (public accountability)
DAILY_POST_SUBMOLTS = ["general", "ai", "finance", "stocks", "markets", "tech"]


# ── OpenClaw Bootstrap ────────────────────────────────────────────────────────

def load_soul() -> str:
    with open(SOUL_PATH) as f:
        lines = f.readlines()
    return "".join(l for l in lines if not l.startswith("# ")).strip()


def load_user() -> str:
    with open(USER_PATH) as f:
        lines = f.readlines()
    return "".join(l for l in lines if not l.startswith("# ")).strip()


def load_openclaw_context() -> str:
    return f"{load_soul()}\n\nHANDLER PROFILE:\n{load_user()}"


# ── Memory ────────────────────────────────────────────────────────────────────

def load_memory() -> dict:
    with open(MEMORY_PATH) as f:
        content = f.read()

    def _val(key: str) -> str | None:
        m = re.search(rf"^- {key}: (.+)$", content, re.MULTILINE)
        if m and m.group(1).strip() not in ("none", ""):
            return m.group(1).strip()
        return None

    price_history = []
    ph = re.search(r"## Price History\n((?:- .+\n?)*)", content)
    if ph:
        for line in ph.group(1).strip().splitlines():
            m = re.match(r"- (\d{4}-\d{2}-\d{2}): \$([\d.]+) \(([+-][\d.]+)%\)", line.strip())
            if m:
                price_history.append({
                    "date": m.group(1),
                    "price": float(m.group(2)),
                    "change_pct": float(m.group(3)),
                })

    zitron_used_links: set[str] = set()
    zh = re.search(r"## Zitron History\n((?:- .+\n?)*)", content)
    if zh:
        for line in zh.group(1).strip().splitlines():
            m = re.match(r"- \d{4}-\d{2}-\d{2} \| (https?://\S+) \|", line.strip())
            if m:
                zitron_used_links.add(m.group(1))
    else:
        old_link = _val("zitron_link")
        if old_link:
            zitron_used_links.add(old_link)

    commented_posts: set[str] = set()
    cp = re.search(r"## Commented Posts\n((?:- .+\n?)*)", content)
    if cp:
        for line in cp.group(1).strip().splitlines():
            pid = line.strip().lstrip("- ").strip()
            if pid:
                commented_posts.add(pid)

    own_posts: list[str] = []
    op = re.search(r"## Own Posts\n((?:- .+\n?)*)", content)
    if op:
        for line in op.group(1).strip().splitlines():
            m = re.match(r"- \d{4}-\d{2}-\d{2} \| (.+)", line.strip())
            if m:
                own_posts.append(m.group(1).strip())

    # Argument log — deployed arguments, most recent first
    argument_log: list[str] = []
    al = re.search(r"## Argument Log\n((?:- .+\n?)*)", content)
    if al:
        for line in al.group(1).strip().splitlines():
            entry = re.sub(r"^- \d{4}-\d{2}-\d{2} \| ", "", line.strip())
            if entry:
                argument_log.append(entry)

    # Running thesis — evolves across sessions
    running_thesis = ""
    rt = re.search(r"## Running Thesis\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if rt:
        candidate = rt.group(1).strip()
        if candidate and candidate != "(not yet developed)":
            running_thesis = candidate

    # Call tracker — directional calls with outcomes
    call_tracker: list[dict] = []
    ct = re.search(r"## Call Tracker\n((?:- .+\n?)*)", content)
    if ct:
        for line in ct.group(1).strip().splitlines():
            m = re.match(
                r"- (\d{4}-\d{2}-\d{2}) \| called: (UP|DOWN) \| actual: (UP|DOWN) \(([+-][\d.]+)%\) \| (.+)",
                line.strip(),
            )
            if m:
                call_tracker.append({
                    "date": m.group(1), "called": m.group(2),
                    "actual": m.group(3), "actual_pct": float(m.group(4)),
                    "outcome": m.group(5).strip(),
                })

    # Submolt stats — per-submolt engagement history
    submolt_stats: dict[str, dict] = {}
    ss = re.search(r"## Submolt Stats\n((?:- .+\n?)*)", content)
    if ss:
        for line in ss.group(1).strip().splitlines():
            m = re.match(
                r"- (\w+): posts:(\d+) \| total_score:(\d+) \| avg:([\d.]+) \| last:(\S+)",
                line.strip(),
            )
            if m:
                submolt_stats[m.group(1)] = {
                    "posts": int(m.group(2)),
                    "total_score": int(m.group(3)),
                    "avg": float(m.group(4)),
                    "last": m.group(5),
                }

    # Submolt used for the most recent post (stored inline in ## Own Posts)
    last_post_submolt: str | None = None
    op_raw = re.search(r"## Own Posts\n((?:- .+\n?)*)", content)
    if op_raw:
        first_op = op_raw.group(1).strip().splitlines()[0] if op_raw.group(1).strip() else ""
        sm = re.search(r"\| submolt:(\w+)", first_op)
        if sm:
            last_post_submolt = sm.group(1)

    price_str = _val("close_price")
    chg_str = _val("change_pct")
    return {
        "date": _val("date"),
        "close_price": float(price_str) if price_str else None,
        "change_pct": float(chg_str) if chg_str else None,
        "post_id": _val("post_id"),
        "price_history": price_history,
        "zitron_used_links": zitron_used_links,
        "commented_posts": commented_posts,
        "own_posts": own_posts,
        "argument_log": argument_log,
        "running_thesis": running_thesis,
        "call_tracker": call_tracker,
        "submolt_stats": submolt_stats,
        "last_post_submolt": last_post_submolt,
    }


def save_memory(
    date: str,
    price: float,
    change_pct: float,
    post_id: str,
    price_history: list[dict],
    zitron: dict | None = None,
    argument: str | None = None,
    running_thesis: str | None = None,
    submolt: str | None = None,
) -> None:
    with open(MEMORY_PATH) as f:
        content = f.read()

    new_session = (
        f"## Last Session\n"
        f"- date: {date}\n"
        f"- close_price: {price}\n"
        f"- change_pct: {change_pct}\n"
        f"- post_id: {post_id}\n"
    )
    content = re.sub(r"## Last Session\n(?:- [^\n]+\n)*", new_session, content)

    history = [{"date": date, "price": price, "change_pct": change_pct}]
    history += [h for h in price_history if h["date"] != date]
    history = history[:PRICE_HISTORY_DAYS]
    new_ph = "## Price History\n" + "".join(
        f"- {h['date']}: ${h['price']} ({h['change_pct']:+.2f}%)\n" for h in history
    )
    if "## Price History" in content:
        content = re.sub(r"## Price History\n(?:- [^\n]+\n)*", new_ph, content)
    else:
        content = re.sub(
            r"(## (?:Last Zitron Article|Zitron History|Commented Posts|Notable Events))",
            new_ph + "\n\\1", content, count=1,
        )

    # Own Posts — rolling OWN_POSTS_HISTORY days
    sm_tag = f" | submolt:{submolt}" if submolt else ""
    new_op_line = f"- {date} | {post_id}{sm_tag}\n"
    op_match = re.search(r"## Own Posts\n((?:- .+\n?)*)", content)
    if op_match:
        existing = op_match.group(1).strip().splitlines(keepends=True)
        new_lines = ([new_op_line] + existing)[:OWN_POSTS_HISTORY]
        content = re.sub(r"## Own Posts\n(?:- .+\n?)*",
                         "## Own Posts\n" + "".join(new_lines), content)
    else:
        content = content.rstrip() + f"\n\n## Own Posts\n{new_op_line}"

    if zitron:
        new_line = f"- {date} | {zitron['link']} | {zitron['title']}\n"
        zh_match = re.search(r"## Zitron History\n((?:- .+\n?)*)", content)
        if zh_match:
            existing = zh_match.group(1).strip().splitlines(keepends=True)
            new_lines = ([new_line] + existing)[:ZITRON_HISTORY_SIZE]
            new_zh = "## Zitron History\n" + "".join(new_lines)
            content = re.sub(r"## Zitron History\n(?:- .+\n?)*", new_zh, content)
        else:
            new_zh = "## Zitron History\n" + new_line
            if "## Last Zitron Article" in content:
                content = re.sub(r"## Last Zitron Article\n(?:- [^\n]+\n)*", new_zh, content)
            else:
                content = content.rstrip() + f"\n\n{new_zh}"

    # Call tracker — always record today's bear call vs. actual move
    bear_called = "DOWN"
    actual = "DOWN" if change_pct < 0 else "UP"
    if change_pct < -1:
        outcome = "✓ right"
    elif change_pct > 1:
        outcome = "✗ wrong"
    else:
        outcome = "~ neutral"
    new_call = f"- {date} | called: {bear_called} | actual: {actual} ({change_pct:+.2f}%) | {outcome}\n"
    ct_match = re.search(r"## Call Tracker\n((?:- .+\n?)*)", content)
    if ct_match:
        existing = ct_match.group(1).strip().splitlines(keepends=True)
        new_lines = ([new_call] + existing)[:CALL_TRACKER_SIZE]
        content = re.sub(r"## Call Tracker\n(?:- .+\n?)*",
                         "## Call Tracker\n" + "".join(new_lines), content)
    else:
        content = content.rstrip() + f"\n\n## Call Tracker\n{new_call}"

    # Argument log — what was argued today, for deduplication tomorrow
    if argument:
        new_arg = f"- {date} | {argument}\n"
        al_match = re.search(r"## Argument Log\n((?:- .+\n?)*)", content)
        if al_match:
            existing = al_match.group(1).strip().splitlines(keepends=True)
            new_lines = ([new_arg] + existing)[:ARGUMENT_LOG_SIZE]
            content = re.sub(r"## Argument Log\n(?:- .+\n?)*",
                             "## Argument Log\n" + "".join(new_lines), content)
        else:
            content = content.rstrip() + f"\n\n## Argument Log\n{new_arg}"

    # Running thesis — evolving synthesis, written by the LLM each session
    if running_thesis:
        new_rt = f"## Running Thesis\n{running_thesis}\n"
        if "## Running Thesis" in content:
            content = re.sub(
                r"## Running Thesis\n.*?(?=\n## |\Z)", new_rt.rstrip(), content, flags=re.DOTALL
            )
        else:
            content = content.rstrip() + f"\n\n{new_rt}"

    with open(MEMORY_PATH, "w") as f:
        f.write(content)


def record_comment(post_id: str) -> None:
    """Append a post ID to the Grudge DB (Commented Posts) in MEMORY.md."""
    with open(MEMORY_PATH) as f:
        content = f.read()

    if "## Commented Posts" not in content:
        content = content.rstrip() + "\n\n## Commented Posts\n"

    content = re.sub(r"(## Commented Posts\n)", f"\\1- {post_id}\n", content, count=1)

    # Cap at MAX_GRUDGE_DB entries
    m = re.search(r"## Commented Posts\n((?:- .+\n?)*)", content)
    if m:
        lines = m.group(1).strip().splitlines(keepends=True)
        if len(lines) > MAX_GRUDGE_DB:
            content = re.sub(
                r"## Commented Posts\n(?:- .+\n?)*",
                "## Commented Posts\n" + "".join(lines[:MAX_GRUDGE_DB]),
                content,
            )

    with open(MEMORY_PATH, "w") as f:
        f.write(content)


def update_submolt_stats(submolt: str, score: int, date: str) -> None:
    """Attribute a post's final score to its submolt, updating ## Submolt Stats."""
    with open(MEMORY_PATH) as f:
        content = f.read()

    stats: dict[str, dict] = {}
    ss = re.search(r"## Submolt Stats\n((?:- .+\n?)*)", content)
    if ss:
        for line in ss.group(1).strip().splitlines():
            m = re.match(
                r"- (\w+): posts:(\d+) \| total_score:(\d+) \| avg:([\d.]+) \| last:(\S+)",
                line.strip(),
            )
            if m:
                stats[m.group(1)] = {
                    "posts": int(m.group(2)),
                    "total_score": int(m.group(3)),
                    "avg": float(m.group(4)),
                    "last": m.group(5),
                }

    if submolt in stats:
        stats[submolt]["posts"] += 1
        stats[submolt]["total_score"] += score
        stats[submolt]["avg"] = round(stats[submolt]["total_score"] / stats[submolt]["posts"], 1)
        stats[submolt]["last"] = date
    else:
        stats[submolt] = {"posts": 1, "total_score": score, "avg": float(score), "last": date}

    sorted_stats = sorted(stats.items(), key=lambda x: x[1]["avg"], reverse=True)
    new_block = "## Submolt Stats\n" + "".join(
        f"- {s}: posts:{v['posts']} | total_score:{v['total_score']} | avg:{v['avg']} | last:{v['last']}\n"
        for s, v in sorted_stats
    )
    if "## Submolt Stats" in content:
        content = re.sub(r"## Submolt Stats\n(?:- .+\n?)*", new_block, content)
    else:
        content = content.rstrip() + f"\n\n{new_block}"

    with open(MEMORY_PATH, "w") as f:
        f.write(content)


# ── Zitron ────────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()


def _strip_cta(text: str) -> str:
    for cta in _ZITRON_CTA:
        idx = text.lower().find(cta)
        if idx > 50:
            return text[:idx].strip()
    return text


def _score(title: str, summary: str) -> int:
    text = f"{title} {summary}".lower()
    return sum(len(kw.split()) for kw in BEAR_KEYWORDS if kw in text)


def fetch_zitron_latest(used_links: set[str]) -> dict | None:
    feed = feedparser.parse(ZITRON_FEED)
    candidates = []
    for entry in feed.entries[:15]:
        link = getattr(entry, "link", "")
        if link in used_links:
            continue
        title = getattr(entry, "title", "")
        raw_summary = _strip_html(getattr(entry, "summary", ""))
        summary = _strip_cta(raw_summary)
        score = _score(title, summary)
        if score == 0:
            continue
        clean_title = re.sub(r"^(Premium|News|Exclusive):\s*", "", title, flags=re.IGNORECASE)
        candidates.append({"title": clean_title, "summary": summary[:3000], "link": link, "score": score})

    if not candidates:
        return None
    best = max(candidates, key=lambda x: x["score"])
    best.pop("score")
    return best


# ── Market Data ───────────────────────────────────────────────────────────────

def get_nvda_price() -> dict:
    ticker = yf.Ticker("NVDA")
    current = None
    prev = None
    open_price = None
    high = None
    low = None
    volume = None
    as_of = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    try:
        fi = ticker.fast_info
        current = float(fi.last_price)
        prev = float(fi.previous_close)
    except Exception:
        pass
    try:
        hist = ticker.history(period="2d")
        if not hist.empty:
            row = hist.iloc[-1]
            open_price = float(row["Open"])
            high = float(row["High"])
            low = float(row["Low"])
            volume = int(row["Volume"])
            if current is None:
                current = float(row["Close"])
        if prev is None and len(hist) > 1:
            prev = float(hist.iloc[-2]["Close"])
    except Exception:
        pass
    if current is None or prev is None:
        raise RuntimeError("Unable to fetch NVDA price data")
    return {
        "price": round(current, 2),
        "prev_close": round(prev, 2),
        "change_pct": round(((current - prev) / prev) * 100, 2),
        "open": round(open_price, 2) if open_price is not None else None,
        "high": round(high, 2) if high is not None else None,
        "low": round(low, 2) if low is not None else None,
        "volume": volume,
        "as_of": as_of,
    }


def get_nvda_news(max_items: int = 5) -> list[str]:
    news = yf.Ticker("NVDA").news or []
    seen: set[str] = set()
    headlines = []
    for item in news:
        c = item.get("content", {})
        title = (c.get("title") or item.get("title") or "").strip()
        if title and title not in seen:
            seen.add(title)
            headlines.append(title)
        if len(headlines) >= max_items:
            break
    return headlines


def get_market_context() -> dict:
    ctx: dict = {}
    ticker = yf.Ticker("NVDA")
    try:
        hist = ticker.history(period="22d")
        if not hist.empty and len(hist) > 1:
            avg_vol = float(hist["Volume"].iloc[:-1].mean())
            today_vol = float(hist["Volume"].iloc[-1])
            prev_vol = float(hist["Volume"].iloc[-2])
            if avg_vol > 0:
                ctx["vol_ratio"] = round(today_vol / avg_vol, 2)
            if prev_vol > 0:
                ctx["volume_change_pct"] = round((today_vol / prev_vol - 1) * 100, 1)
            ctx["avg_volume"] = int(avg_vol)
    except Exception:
        pass
    try:
        fi = ticker.fast_info
        high = float(fi.year_high)
        low = float(fi.year_low)
        current = float(fi.last_price)
        if high > 0:
            ctx["pct_from_52w_high"] = round((current / high - 1) * 100, 1)
        if low > 0:
            ctx["pct_from_52w_low"] = round((current / low - 1) * 100, 1)
    except Exception:
        pass
    try:
        spy = yf.Ticker("SPY").fast_info
        ctx["spy_chg"] = round(
            (float(spy.last_price) / float(spy.previous_close) - 1) * 100, 2
        )
    except Exception:
        pass
    return ctx


def get_nvda_profile() -> dict:
    """Fetch NVDA fundamentals and share structure data for richer context."""
    ctx: dict = {}
    ticker = yf.Ticker("NVDA")
    info = {}
    try:
        info = getattr(ticker, "info", None) or ticker.get_info()
    except Exception:
        try:
            info = ticker.info or {}
        except Exception:
            info = {}
    if not isinstance(info, dict):
        info = {}
    try:
        cap = info.get("marketCap")
        if cap and cap > 0:
            ctx["market_cap"] = round(cap / 1_000_000_000, 1)
        for key in ("trailingPE", "forwardPE", "grossMargins", "profitMargins",
                    "revenueGrowth", "earningsQuarterlyGrowth", "beta"):
            val = info.get(key)
            if isinstance(val, (int, float)):
                ctx[key] = round(val, 3) if key in ("grossMargins", "profitMargins") else round(val, 2)
        float_shares = info.get("floatShares")
        if float_shares:
            ctx["float_shares"] = int(float_shares)
        recommendation = info.get("recommendationMean")
        if recommendation is not None:
            ctx["recommendation_mean"] = round(float(recommendation), 2)
    except Exception:
        pass
    return ctx


def get_nvda_extended_fundamentals() -> dict:
    """Fetch technical and trend-based context: margin trends, momentum, volatility, short interest.
    
    Returns rich context for posts rooted in technical + fundamental reality.
    """
    ctx: dict = {}
    ticker = yf.Ticker("NVDA")
    
    # Recent momentum — 5-day, 20-day, 50-day moving averages
    try:
        hist = ticker.history(period="100d")
        if not hist.empty and len(hist) >= 50:
            current_close = float(hist["Close"].iloc[-1])
            ma5 = float(hist["Close"].iloc[-5:].mean())
            ma20 = float(hist["Close"].iloc[-20:].mean())
            ma50 = float(hist["Close"].iloc[-50:].mean())
            ctx["ma5"] = round(ma5, 2)
            ctx["ma20"] = round(ma20, 2)
            ctx["ma50"] = round(ma50, 2)
            ctx["close_vs_ma20"] = round((current_close / ma20 - 1) * 100, 1)
            ctx["close_vs_ma50"] = round((current_close / ma50 - 1) * 100, 1)
            # Volatility — 20-day standard deviation as % of moving average
            ctx["volatility_20d"] = round(hist["Close"].iloc[-20:].std() / ma20 * 100, 1)
    except Exception:
        pass
    
    # RSI-like momentum (simplified: recent % change strength)
    try:
        hist = ticker.history(period="15d")
        if not hist.empty and len(hist) >= 10:
            recent_returns = hist["Close"].pct_change().iloc[-10:].values
            updays = sum(1 for r in recent_returns if r > 0)
            ctx["updays_last_10"] = updays
    except Exception:
        pass
    
    # Sentiment from analyst recommendations
    info = {}
    try:
        info = getattr(ticker, "info", None) or ticker.get_info()
    except Exception:
        try:
            info = ticker.info or {}
        except Exception:
            pass
    if isinstance(info, dict):
        if info.get("numberOfAnalystRatings"):
            ctx["analyst_count"] = info.get("numberOfAnalystRatings")
        if info.get("targetMeanPrice"):
            ctx["target_mean_price"] = round(float(info["targetMeanPrice"]), 2)
    
    return ctx


def fetch_earnings_context() -> str | None:
    """Return a countdown string if NVDA earnings are within 30 days, else None."""
    try:
        cal = yf.Ticker("NVDA").calendar
        if cal is None:
            return None
        dates = cal.get("Earnings Date") if hasattr(cal, "get") else None
        if dates is None or len(dates) == 0:
            return None
        next_dt = dates[0]
        next_date = next_dt.date() if hasattr(next_dt, "date") else next_dt
        days = (next_date - _now_et().date()).days
        if days < 0:
            return None
        if days == 0:
            return "⚠️ NVDA EARNINGS TODAY"
        if days <= 7:
            return f"⚠️ NVDA earnings in {days} days ({next_date})"
        if days <= 30:
            return f"NVDA earnings in {days} days ({next_date})"
    except Exception:
        pass
    return None


def fetch_market_headlines(max_items: int = 6) -> list[str]:
    """Pull semiconductor/AI headlines — keyword filtered for relevance."""
    seen: set[str] = set()
    headlines = []
    for url in SEMI_AI_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                title = getattr(entry, "title", "").strip()
                if not title or title in seen:
                    continue
                if any(kw in title.lower() for kw in _SEMI_AI_FILTER):
                    seen.add(title)
                    headlines.append(title)
            if len(headlines) >= max_items:
                break
        except Exception:
            continue
    return headlines[:max_items]


def fetch_macro_headlines(max_items: int = 12) -> list[str]:
    """Pull broad macro/financial headlines — no filtering, LLM decides what matters."""
    seen: set[str] = set()
    headlines = []
    for url in MACRO_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                title = getattr(entry, "title", "").strip()
                if title and title not in seen:
                    seen.add(title)
                    headlines.append(title)
            if len(headlines) >= max_items:
                break
        except Exception:
            continue
    return headlines[:max_items]


def assess_catalysts(semi_headlines: list[str], macro_headlines: list[str]) -> dict:
    """LLM analyst pass — finds direct, indirect, and black swan signals.

    Deliberately NOT using the bear soul here — we want analytical output,
    not WSB energy. Low temperature, research mode.
    Returns {"synthesis": str, "black_swan_watch": [str], "flagged": [str]}
    """
    all_lines = semi_headlines + macro_headlines
    if not all_lines:
        return {"synthesis": "", "black_swan_watch": [], "flagged": []}

    headlines_block = "\n".join(f"- {h}" for h in all_lines)
    prompt = (
        "You are a financial analyst briefing an NVDA bear agent. "
        "Scan today's headlines for bear thesis relevance.\n\n"
        f"HEADLINES:\n{headlines_block}\n\n"
        "Identify three categories:\n"
        "1. FLAGGED: Headlines directly relevant to the NVDA bear thesis "
        "(chip demand, AI capex, competition, earnings, export controls)\n"
        "2. INDIRECT: Non-obvious connections — FOMC rate decisions affecting "
        "risk assets, private credit funding AI capex, hyperscaler spending guidance, "
        "sovereign stress, liquidity signals\n"
        "3. BLACK_SWAN_WATCH: Anything that feels like an early-stage systemic "
        "risk precursor — even if the NVDA connection isn't obvious yet. "
        "Private credit blowups, shadow banking stress, unexpected regulatory "
        "actions, geopolitical escalation, anything that rhymes with prior crises.\n\n"
        "Think analytically. Make non-obvious connections. A rising tide hides rocks.\n\n"
        'Return JSON only:\n'
        '{"flagged": ["..."], "indirect": ["..."], "black_swan_watch": ["..."], '
        '"synthesis": "one sentence on what the broader picture looks like today"}'
    )
    try:
        resp = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.15,
        )
        text = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {"synthesis": "", "black_swan_watch": [], "flagged": []}


def record_notable_events(
    nvda_headlines: list[str],
    market_headlines: list[str],
    earnings_context: str | None,
    today: str,
    catalyst_assessment: dict | None = None,
) -> None:
    """Detect notable events and append new ones to ## Notable Events."""
    with open(MEMORY_PATH) as f:
        content = f.read()

    new_events: list[str] = []

    if earnings_context and earnings_context not in content:
        new_events.append(f"- {today}: {earnings_context}")

    # Keyword-matched events (earnings, Fed, trade, macro)
    all_text = " ".join(nvda_headlines + market_headlines).lower()
    for category, keywords in _EVENT_SIGNALS.items():
        matching = [h for h in (nvda_headlines + market_headlines)
                    if any(kw in h.lower() for kw in keywords)]
        if matching and matching[0] not in content:
            new_events.append(f"- {today}: [{category.upper()}] {matching[0][:120]}")
            break

    # LLM-identified black swan signals — these are the unknowns we can't keyword-filter
    if catalyst_assessment:
        for flag in catalyst_assessment.get("black_swan_watch", [])[:2]:
            entry = f"- {today}: [BLACK SWAN WATCH] {flag[:150]}"
            if flag[:50] not in content:
                new_events.append(entry)

    if not new_events:
        return

    for event in new_events:
        if "## Notable Events" in content:
            content = re.sub(
                r"(## Notable Events\n)(\(none recorded yet\)\n?)?",
                f"\\1{event}\n",
                content, count=1,
            )

    with open(MEMORY_PATH, "w") as f:
        f.write(content)
    print(f"  [events] recorded: {len(new_events)} notable event(s)")


# ── Social ────────────────────────────────────────────────────────────────────

def vote_post(post_id: str, direction: str) -> bool:
    """Vote on a post. direction: 'up' or 'down'. Fails silently."""
    try:
        resp = requests.post(
            f"{MOLTBOOK_BASE}/posts/{post_id}/vote",
            headers=_headers(),
            json={"direction": direction},
            timeout=8,
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False


def fetch_post_score(post_id: str) -> int | None:
    """Fetch upvote count for an own post. Returns None if unavailable."""
    try:
        r = requests.get(f"{MOLTBOOK_BASE}/posts/{post_id}", timeout=8)
        if r.status_code == 200:
            data = r.json()
            post = data.get("post", data)
            for field in ("upvotes", "vote_count", "score", "karma", "likes"):
                if field in post and post[field] is not None:
                    return int(post[field])
    except Exception:
        pass
    return None


def fetch_social_context(limit_per_term: int = 5) -> list[dict]:
    """Search for recent NVDA/AI posts to use in the reflection step."""
    seen: set[str] = set()
    posts = []
    for term in SOCIAL_SEARCH_TERMS[:4]:
        try:
            r = requests.get(
                f"{MOLTBOOK_BASE}/search",
                params={"q": term, "limit": limit_per_term},
                timeout=8,
            )
            if r.status_code != 200:
                continue
            for result in r.json().get("results", []):
                pid = result.get("post_id") or result.get("id")
                if pid and pid not in seen:
                    seen.add(pid)
                    posts.append(result)
        except Exception:
            continue
        if len(posts) >= 15:
            break
    return posts


def build_semantic_query(
    social_posts: list[dict],
    price: dict | None = None,
    market: dict | None = None,
    headlines: list[str] | None = None,
) -> str:
    pieces = []
    if social_posts:
        pieces.append("Recent Moltbook discussion titles:")
        pieces.extend(f"- {p['title']}" for p in social_posts[:5] if p.get("title"))
    if price:
        pieces.append(
            f"NVDA closed at ${price['price']} ({price['change_pct']:+.2f}%), "
            f"prev close ${price['prev_close']}"
        )
        if price.get("open") is not None:
            pieces.append(f"today opened at ${price['open']}")
        if price.get("high") is not None and price.get("low") is not None:
            pieces.append(f"intraday range ${price['low']}–${price['high']}")
    if market:
        if market.get("vol_ratio") is not None:
            pieces.append(f"volume ratio {market['vol_ratio']}x vs 20-day average")
        if market.get("spy_chg") is not None:
            pieces.append(f"SPY moved {market['spy_chg']:+.2f}%")
    if headlines:
        pieces.append("NVDA-related headlines:")
        pieces.extend(f"- {h}" for h in headlines[:5])
    return "\n".join(pieces)


def fetch_post_comments(post_id: str) -> list[dict]:
    try:
        r = requests.get(f"{MOLTBOOK_BASE}/posts/{post_id}/comments",
                         params={"limit": 10}, timeout=8)
        if r.status_code == 200:
            return r.json().get("comments", [])
    except Exception:
        pass
    return []


def reflect_and_plan(
    price_history: list[dict],
    social_posts: list[dict],
    soul: str,
    argument_log: list[str] | None = None,
    running_thesis: str = "",
    call_tracker: list[dict] | None = None,
    last_post_id: str | None = None,
    semantic_past_args: list[str] | None = None,
    price: dict | None = None,
    market: dict | None = None,
    company_profile: dict | None = None,
    headlines: list[str] | None = None,
) -> dict:
    """Plan today's post: what's the new angle, tone, and any past call to reference.

    Returns {"new_angle": str, "tone": str, "reference_past": str | None}
    """
    streak = _streak(price_history) if price_history else "no history"
    titles = "\n".join(f"- {p['title'][:80]}" for p in social_posts[:5] if p.get("title"))

    # Last call outcome from price history (bear always calls DOWN)
    last_call_line = ""
    if price_history:
        last = price_history[0]
        direction = "DOWN" if last["change_pct"] < 0 else "UP"
        if last["change_pct"] < -1:
            last_call_line = f"Last session: called DOWN — NVDA went {direction} {last['change_pct']:+.2f}%. Bear was right. ✓"
        elif last["change_pct"] > 1:
            last_call_line = f"Last session: called DOWN — NVDA went {direction} {last['change_pct']:+.2f}%. Wrong (or early). ✗"
        else:
            last_call_line = f"Last session: called DOWN — NVDA moved {last['change_pct']:+.2f}%. Neutral."

    score_line = ""
    if last_post_id and last_post_id != "unknown":
        score = fetch_post_score(last_post_id)
        if score is not None:
            if score >= 10:
                score_line = f"Last post: {score} upvotes — gaining traction."
            elif score == 0:
                score_line = f"Last post: {score} upvotes — bulls ignoring you."
            else:
                score_line = f"Last post: {score} upvotes — building slowly."

    arg_block = ""
    if argument_log:
        arg_lines = "\n".join(f"- {a}" for a in argument_log[:7])
        arg_block = f"\nARGUMENTS ALREADY DEPLOYED THIS WEEK (do not repeat these):\n{arg_lines}"
    if semantic_past_args:
        sem_lines = "\n".join(f"- {a}" for a in semantic_past_args[:5])
        arg_block += (
            f"\n\nSEMANTIC DEDUP — past arguments on similar market days "
            f"(the text filter won't catch these — avoid these angles specifically):\n{sem_lines}"
        )

    extra_context = []
    if price:
        extra_context.append(
            f"TODAY'S CLOSE: ${price['price']} ({price['change_pct']:+.2f}%), prev close ${price['prev_close']}"
        )
        if price.get("open") is not None:
            extra_context.append(f"Open: ${price['open']}")
        if price.get("high") is not None and price.get("low") is not None:
            extra_context.append(f"Range: ${price['low']}–${price['high']}")
    if market:
        if market.get("vol_ratio") is not None:
            extra_context.append(f"Volume ratio: {market['vol_ratio']}x vs 20-day avg")
        if market.get("pct_from_52w_high") is not None:
            extra_context.append(f"Distance from 52w high: {market['pct_from_52w_high']}%")
        if market.get("spy_chg") is not None:
            extra_context.append(f"SPY move: {market['spy_chg']:+.2f}%")
    if company_profile:
        parts = []
        if company_profile.get("market_cap") is not None:
            parts.append(f"market cap ${company_profile['market_cap']}B")
        if company_profile.get("forwardPE") is not None:
            parts.append(f"forward P/E {company_profile['forwardPE']}")
        if company_profile.get("trailingPE") is not None:
            parts.append(f"trailing P/E {company_profile['trailingPE']}")
        if parts:
            extra_context.append("Company profile: " + ", ".join(parts))
    if headlines:
        extra_context.append("NVDA headlines today:")
        extra_context.extend(f"- {h}" for h in headlines[:5])

    prompt = (
        f"PRICE STREAK: {streak}\n"
        f"{last_call_line}\n"
        f"{score_line}\n"
        f"RUNNING THESIS: {running_thesis or '(not yet developed)'}"
        f"{arg_block}\n\n"
        f"{('\n'.join(extra_context) + '\n\n') if extra_context else ''}"
        f"WHAT MOLTBOOK IS TALKING ABOUT:\n{titles or '(nothing relevant found)'}\n\n"
        "Plan today's post. Return JSON:\n"
        '{\n'
        '  "new_angle": "the specific aspect to explore today — must be different from recent arguments",\n'
        '  "tone": "one of: triumphant|doubling_down|patient|vindicated|defensive",\n'
        '  "reference_past": "a concrete past call or observation to weave in (e.g. \'I flagged X last week — today confirms it\'), or null"\n'
        '}'
    )
    try:
        response = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": soul}, {"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.75,
        )
        text = (response.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f"  [reflect] failed: {e}")
    return {"new_angle": "", "tone": "patient", "reference_past": None}


_BANNED_REVIEW_PHRASES = [
    "in today's world", "it's important to note", "as we can see",
    "it's worth noting", "ultimately,", "needless to say",
    "it's clear that", "as previously mentioned",
    # Generic journalist/LLM voice — no thesis account writes this way
    "this suggests", "further reinforces", "this notion", "this indicates",
    "this implies", "we can observe", "it is evident", "this demonstrates",
    # Exact repetitive formulae that kill credibility — HARD BLOCKS
    "that's the close", "that is the close", "here's what nobody wants to say",
    "here's what no one wants to say", "here's the thing", "the thing is",
]

# At least one must appear in a post — hard gate, not LLM-scored
_DOMAIN_TERMS = [
    "forward p/e", "p/e", "multiple compression", "margin pressure", "capex",
    "cost per token", "gross margin", "insider selling", "iv crush", "theta",
    "puts", "short", "supercycle", "hyperscaler", "inference", "guidance",
    "valuation", "earnings", "forward multiple", "write-down", "customer concentration",
]


def review_draft(draft: str, context: str, draft_type: str = "post") -> dict:
    """Critic agent — checks a draft for quality before it gets posted.

    Returns {"pass": bool, "reason": str, "suggestion": str}.
    Defaults to pass on any parsing failure so the critic never blocks silently.
    """
    # Fast pre-check: banned AI phrases are an instant reject
    lower = draft.lower().replace("’", "'").replace("‘", "'")
    for phrase in _BANNED_REVIEW_PHRASES:
        if phrase in lower:
            return {
                "pass": False,
                "reason": f"Contains banned phrase: '{phrase}'",
                "suggestion": f"Remove '{phrase}' — sounds like generic LLM output. Rewrite with specific data.",
            }

    # Domain language gate — posts with no finance/market terms are instant fail
    if draft_type == "post" and not any(t in lower for t in _DOMAIN_TERMS):
        return {
            "pass": False,
            "reason": "No domain language found — reads like a news summary, not a thesis account",
            "suggestion": (
                "Add at least one finance/market term: capex, forward P/E, multiple compression, "
                "margin pressure, guidance, inference, hyperscaler, gross margin, valuation, etc."
            ),
        }

    word_limit = 150 if draft_type == "post" else 80
    engaged_q = (
        "Does it engage with specific points made in the thread?"
        if draft_type == "comment"
        else "Does it react to today's specific numbers and events, not just the general bear thesis?"
    )
    critic_prompt = (
        f"You are a quality reviewer for an AI agent's Moltbook {draft_type}. "
        f"Catch low-quality output before it gets posted.\n\n"
        f"CONTEXT GIVEN TO THE AGENT:\n{context[:600]}\n\n"
        f"DRAFT:\n{draft}\n\n"
        f"Score against these criteria:\n"
        f"1. GROUNDED — Does it cite AT LEAST 2 distinct numbers or named facts from the context block? "
        f"(Price, % move, volume ratio, streak, specific headline, 52w-high distance, SPY comp, earnings date — "
        f"any real data counts. Generic thesis talking points that could apply any day = FAIL.)\n"
        f"2. ENGAGED — {engaged_q}\n"
        f"3. CONCISE — Is it under {word_limit} words?\n"
        f"4. DOMAIN LANGUAGE — Contains at least one finance/market term: "
        f"forward P/E, multiple compression, margin pressure, capex cycle, cost per token, "
        f"gross margin, insider selling, IV crush, theta? Generic claims only = FAIL.\n"
        f"5. HUMAN VOICE — Varies in sentence structure, has a stance, not overly polite or explanatory?\n\n"
        f'Reply with JSON only: {{"pass": true/false, "reason": "one sentence", '
        f'"suggestion": "specific fix if failing, else empty string"}}'
    )
    try:
        response = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": critic_prompt}],
            max_tokens=120,
            temperature=0.1,
        )
        text = (response.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {"pass": True, "reason": "critic unavailable", "suggestion": ""}


def _passes_basic_rant_checks(draft: str) -> bool:
    lower = draft.lower().replace("’", "'").replace("‘", "'")
    if any(bad in lower for bad in [
        "that's the close", "that is the close", "thats the close",
        "here's the thing", "heres the thing", "what i mean is",
        "here's what nobody wants", "here's what no one wants"
    ]):
        return False
    if not any(term in lower for term in _DOMAIN_TERMS):
        return False
    numbers = re.findall(r"\d+(?:\.\d+)?", draft)
    if len(numbers) < 2:
        return False
    if len(draft.strip()) < 60:
        return False
    return True


def generate_fallback_rant(context: str, soul: str) -> str:
    """Fallback writer when the main prompt fails — leaner, still avoid the bad template."""
    prompt = (
        "Fallback mode: write a short NVDA daily close post using only the verified data block.\n\n"
        "Requirements:\n"
        "- Under 120 words.\n"
        "- Avoid 'That's the close', 'Here's what nobody wants to say', 'Here's the thing'.\n"
        "- Use at least 2 distinct numbers from today's data block.\n"
        "- Include at least one finance term: capex, forward P/E, multiple compression, margin, guidance, hyperscaler, inference.\n"
        "- Use the context as your only source. Do not invent prices or headlines.\n"
        "- Add a tight, original closing line: 'Keep your puts warm.', 'Don't buy the narrative.', 'The thesis holds.'\n"
    )
    try:
        resp = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": soul},
                {"role": "user", "content": f"{context}\n\n{prompt}"},
            ],
            max_tokens=300,
            temperature=0.6,
        )
        draft = (resp.choices[0].message.content or "").strip()
        if not draft:
            print("  [fallback] empty fallback draft")
            return ""
        if not _passes_basic_rant_checks(draft):
            print("  [fallback] draft failed basic checks")
            return ""
        review = review_draft(draft, context, "post")
        if review["pass"]:
            print("  [fallback] accepted by critic")
            return draft
        print(f"  [fallback] critic rejected: {review['reason']}")
        if _passes_basic_rant_checks(draft):
            print("  [fallback] accepting by basic checks despite critic rejection")
            return draft
    except Exception as e:
        print(f"  [fallback] failed: {e}")
    return ""


def select_submolt(rant: str, context: str,
                   submolt_stats: dict | None = None) -> str:
    """Pick the most relevant submolt, weighted by historical engagement per submolt."""
    options = DAILY_POST_SUBMOLTS

    perf_lines = []
    for s in options:
        if submolt_stats and s in submolt_stats:
            v = submolt_stats[s]
            tier = "high" if v["avg"] >= 8 else "medium" if v["avg"] >= 4 else "low"
            perf_lines.append(
                f"  - {s}: {v['posts']} post(s), avg {v['avg']} upvotes [{tier} engagement]"
            )
        else:
            perf_lines.append(f"  - {s}: no history yet")
    perf_block = "Engagement history (use this to break ties — prefer proven submolts):\n" + "\n".join(perf_lines)

    prompt = (
        f"Route this Moltbook post to the right community.\n\n"
        f"POST:\n{rant[:400]}\n\n"
        f"TODAY'S ANGLE (what drove it):\n{context[:300]}\n\n"
        f"{perf_block}\n\n"
        "Routing guide:\n"
        "- ai: AI spending, inference economics, LLM costs, GPU compute demand\n"
        "- finance: valuations, P/E, margins, earnings, capital allocation\n"
        "- stocks: price action, volume signals, short thesis, technical setups\n"
        "- markets: macro/FOMC/rate environment, risk-off, sector rotation\n"
        "- tech: chip competition, custom silicon, product cycles, foundry\n"
        "- general: broadly accessible bear case, not highly technical\n\n"
        f"Available: {', '.join(options)}\n"
        "Reply with the single best submolt name — nothing else."
    )
    try:
        response = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.1,
        )
        result = (response.choices[0].message.content or "").strip().lower()
        if result in options:
            return result
    except Exception:
        pass
    # Fallback: highest avg-score submolt tested so far, else "general"
    if submolt_stats:
        tested = [(s, v) for s, v in submolt_stats.items() if s in options]
        if tested:
            return max(tested, key=lambda x: x[1]["avg"])[0]
    return "general"


def generate_social_comment(post: dict, top_comments: list[dict], soul: str) -> str:
    """Targeted bear comment — reads the room, then passes through the critic."""
    comment_block = "\n".join(
        f"- {c.get('author', {}).get('name', '?')}: {c['content'][:200]}"
        for c in top_comments[:3]
    )
    thread_context = (
        f"Post title: {(post.get('title') or '')}\n"
        f"Post content: {str(post.get('content', ''))[:400]}\n"
        f"Top comments:\n{comment_block or '(none yet)'}"
    )
    base_prompt = (
        f"You spotted this post on Moltbook:\n{thread_context}\n\n"
        "Drop a bear comment. Under 80 words. Engage with what they actually said — "
        "don't just rant into the void. Make it feel like you read the room."
    )
    extra = ""
    last_draft = ""
    for attempt in range(2):
        response = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": soul},
                {"role": "user", "content": base_prompt + extra},
            ],
            max_tokens=150,
            temperature=0.9,
        )
        draft = (response.choices[0].message.content or "").strip()
        if not draft:
            continue
        last_draft = draft
        review = review_draft(draft, thread_context, "comment")
        if review["pass"]:
            print(f"    [critic] comment approved (attempt {attempt + 1})")
            return draft
        print(f"    [critic] comment rejected: {review['reason']}")
        extra = f"\n\nCRITIC FEEDBACK: {review['suggestion']} — rewrite addressing this."
    return last_draft


def browse_and_engage(soul: str, memory: dict, own_post_id: str = "") -> None:
    """Find relevant posts across Moltbook and drop targeted bear comments."""
    skip = set(memory.get("commented_posts", set()))
    if own_post_id:
        skip.add(own_post_id)

    candidates = []
    seen = set(skip)
    for term in SOCIAL_SEARCH_TERMS:
        try:
            r = requests.get(
                f"{MOLTBOOK_BASE}/search",
                params={"q": term, "limit": 8},
                timeout=8,
            )
            if r.status_code != 200:
                continue
            for result in r.json().get("results", []):
                pid = result.get("post_id") or result.get("id")
                if not pid or pid in seen:
                    continue
                submolt_name = (result.get("submolt") or {}).get("name", "general")
                if submolt_name not in COMMENT_SUBMOLTS:
                    continue
                seen.add(pid)
                candidates.append(result)
        except Exception:
            continue
        if len(candidates) >= 20:
            break

    # Highest relevance first
    candidates.sort(key=lambda x: x.get("relevance", 0), reverse=True)

    effective_max = random.randint(1, MAX_COMMENTS_PER_RUN)
    print(f"  [social] targeting up to {effective_max} comment(s) this run")
    commented = 0
    votes_cast = 0
    for post in candidates[:15]:
        if commented >= effective_max:
            break
        pid = post.get("post_id") or post.get("id")
        existing = fetch_post_comments(pid)

        # Skip threads we're already in
        if any(c.get("author", {}).get("name") == OUR_HANDLE for c in existing):
            record_comment(pid)
            continue

        # Probabilistic gate — don't comment on every eligible post
        if random.random() > 0.75:
            print(f"  [social] skipping (random roll): {(post.get('title') or '')[:40]}")
            continue

        comment = generate_social_comment(post, existing, soul)
        if not comment:
            continue

        result = moltbook_comment(pid, comment)
        if result.get("id") or result.get("success"):
            print(f"  [social] commented on: {(post.get('title') or '')[:60]}...")
            record_comment(pid)
            commented += 1
            if votes_cast < MAX_VOTES_PER_RUN:
                roll = random.random()
                if roll < 0.45:
                    if vote_post(pid, "down"):
                        votes_cast += 1
                        print(f"  [vote] downvoted post")
                elif roll < 0.60:
                    if vote_post(pid, "up"):
                        votes_cast += 1
                        print(f"  [vote] upvoted post")
                # else: no vote this time
            time.sleep(random.uniform(3, 8))


# ── Agent ─────────────────────────────────────────────────────────────────────

def _streak(history: list[dict]) -> str:
    if not history:
        return ""
    dirs = [h["change_pct"] >= 0 for h in history]
    count = 1
    for i in range(1, len(dirs)):
        if dirs[i] == dirs[0]:
            count += 1
        else:
            break
    direction = "UP" if dirs[0] else "DOWN"
    n = len(history)
    suffix = ""
    if n >= 2:
        delta = round(history[0]["price"] - history[-1]["price"], 2)
        sign = "+" if delta >= 0 else ""
        suffix = f" | {n}-session delta: {sign}${delta}"
    return f"{direction} {count} of last {n} sessions{suffix}"


def build_context(
    price: dict,
    headlines: list[str],
    memory: dict,
    market: dict,
    company_profile: dict | None = None,
    mood: str = "",
    zitron: dict | None = None,
    earnings_context: str | None = None,
    market_headlines: list[str] | None = None,
    catalyst_assessment: dict | None = None,
    plan: dict | None = None,
    macro_calendar: str = "",
    macro_commentary: str = "",
    past_research: list[dict] | None = None,
    extended_fundamentals: dict | None = None,
) -> str:
    chg = price["change_pct"]
    direction = "DOWN" if chg < 0 else "UP"
    news_block = "\n".join(f"- {h}" for h in headlines) if headlines else "- No headlines available."

    market_lines = []
    if "vol_ratio" in market:
        pct = round((market["vol_ratio"] - 1) * 100)
        label = "above" if pct >= 0 else "below"
        # Volume interpretation hint — prevents model from misreading low volume as distribution
        if pct < -10:
            vol_note = " (low conviction — soft down day, not institutional selling)"
        elif pct > 20 and chg < 0:
            vol_note = " (high volume on red day — watch for distribution)"
        elif pct > 20 and chg > 0:
            vol_note = " (high volume on green day — buying pressure)"
        else:
            vol_note = ""
        market_lines.append(f"Volume: {abs(pct)}% {label} 20-day average{vol_note}")
    if "pct_from_52w_high" in market:
        p = market["pct_from_52w_high"]
        market_lines.append(
            "52-week high: AT OR ABOVE — overextended"
            if p >= 0 else f"Distance from 52-week high: {p}%"
        )
    if "spy_chg" in market:
        spy = market["spy_chg"]
        vs = round(chg - spy, 2)
        sign = "+" if vs >= 0 else ""
        market_lines.append(f"S&P 500: {spy:+.2f}% today (NVDA {sign}{vs}% vs market)")

    market_block = ("\n" + "\n".join(f"- {l}" for l in market_lines)) if market_lines else ""

    company_lines = []
    if company_profile:
        if company_profile.get("market_cap") is not None:
            company_lines.append(f"Market cap: ${company_profile['market_cap']}B")
        if company_profile.get("forwardPE") is not None:
            company_lines.append(f"Forward P/E: {company_profile['forwardPE']}")
        if company_profile.get("trailingPE") is not None:
            company_lines.append(f"Trailing P/E: {company_profile['trailingPE']}")
        if company_profile.get("grossMargins") is not None:
            company_lines.append(f"Gross margin: {company_profile['grossMargins'] * 100:.1f}%")
        if company_profile.get("profitMargins") is not None:
            company_lines.append(f"Profit margin: {company_profile['profitMargins'] * 100:.1f}%")
        if company_profile.get("revenueGrowth") is not None:
            company_lines.append(f"Revenue growth: {company_profile['revenueGrowth'] * 100:.1f}%")
        if company_profile.get("earningsQuarterlyGrowth") is not None:
            company_lines.append(f"Earnings growth: {company_profile['earningsQuarterlyGrowth'] * 100:.1f}%")
        if company_profile.get("beta") is not None:
            company_lines.append(f"Beta: {company_profile['beta']}")
    company_block = ("\n" + "\n".join(f"- {l}" for l in company_lines)) if company_lines else ""

    extended_block = ""
    if extended_fundamentals:
        ext_lines = []
        if extended_fundamentals.get("close_vs_ma20") is not None:
            ext_lines.append(f"Price vs 20-day MA: {extended_fundamentals['close_vs_ma20']:+.1f}%")
        if extended_fundamentals.get("close_vs_ma50") is not None:
            ext_lines.append(f"Price vs 50-day MA: {extended_fundamentals['close_vs_ma50']:+.1f}%")
        if extended_fundamentals.get("volatility_20d") is not None:
            ext_lines.append(f"20-day volatility: {extended_fundamentals['volatility_20d']:.1f}%")
        if extended_fundamentals.get("updays_last_10") is not None:
            ext_lines.append(f"Up days (last 10): {extended_fundamentals['updays_last_10']}/10")
        if extended_fundamentals.get("target_mean_price") is not None:
            ext_lines.append(f"Analyst target mean: ${extended_fundamentals['target_mean_price']}")
        if ext_lines:
            extended_block = "\nTECHNICAL & TREND:\n" + "\n".join(f"- {l}" for l in ext_lines)

    history = memory.get("price_history", [])
    trend_block = ""
    if history:
        rows = "\n".join(
            f"  {h['date']}: ${h['price']} ({h['change_pct']:+.2f}%)" for h in history
        )
        trend_block = f"\nPRICE TREND:\n{rows}\nStreak: {_streak(history)}\n"

    mood_block = f"\nYour internal state going in: {mood}\n" if mood else ""

    zitron_block = ""
    if zitron:
        detail = f"\nExcerpt: {zitron['summary'][:2500]}" if zitron.get("summary") else ""
        zitron_block = (
            f"\nBEAR RESEARCH HOOK — one article caught your eye today:\n"
            f"Claim: {zitron['title']}{detail}\n"
            "Use this: extract ONE specific claim, then critique or reinterpret it through NVDA's "
            "mechanics. Do NOT summarize it. Push back on the claim or build from it. "
            "Tie it to a measurable NVDA risk. Do not reveal or name the source.\n"
        )

    earnings_block = f"\n⚠️ CALENDAR: {earnings_context}" if earnings_context else ""

    broad_block = ""
    if market_headlines:
        broad_lines = "\n".join(f"- {h}" for h in market_headlines)
        broad_block = f"\nSEMICONDUCTOR / AI HEADLINES:\n{broad_lines}"

    catalyst_block = ""
    if catalyst_assessment:
        parts = []
        synthesis = catalyst_assessment.get("synthesis", "")
        if synthesis:
            parts.append(f"Analyst read: {synthesis}")
        indirect = catalyst_assessment.get("indirect", [])
        if indirect:
            parts.append("Indirect catalysts: " + "; ".join(indirect[:2]))
        bsw = catalyst_assessment.get("black_swan_watch", [])
        if bsw:
            parts.append("⚠️ BLACK SWAN WATCH: " + "; ".join(bsw[:2]))
        if parts:
            catalyst_block = "\nMACRO CATALYST SCAN:\n" + "\n".join(f"- {p}" for p in parts)

    plan_block = ""
    if plan:
        angle = plan.get("new_angle", "")
        tone = plan.get("tone", "")
        ref = plan.get("reference_past")
        running_thesis = memory.get("running_thesis", "")
        parts = []
        if angle:
            parts.append(f"TODAY'S ANGLE: {angle}")
        if tone:
            parts.append(f"TONE: {tone}")
        if ref:
            parts.append(f"WEAVE IN: {ref}")
        if running_thesis:
            parts.append(f"YOUR RUNNING THESIS (build on this): {running_thesis}")
        if parts:
            plan_block = "\nPOSTING PLAN (follow this):\n" + "\n".join(f"- {p}" for p in parts)

    macro_context_block = ""
    macro_parts = []
    if macro_calendar:
        macro_parts.append(f"ECONOMIC CALENDAR:\n{macro_calendar}")
    if macro_commentary:
        macro_parts.append(macro_commentary)
    if macro_parts:
        macro_context_block = "\n\nMACRO CONTEXT (use as supporting terrain — stay NVDA-focused):\n" + "\n\n".join(macro_parts)

    research_archive_block = ""
    if past_research:
        items = []
        for r in past_research[:2]:
            meta = r.get("metadata", {})
            title = meta.get("title", "").strip()
            summary = (r.get("metadata", {}).get("summary") or r.get("data") or "").strip()
            if title:
                snippet = summary.split(". ")[0][:160].strip()
                if snippet:
                    items.append(f"- {title}: {snippet}.")
                else:
                    items.append(f"- {title}")
        if items:
            research_archive_block = (
                "\n\nTHEMATIC ARCHIVE — past research that resonates with today's angle "
                "(synthesize as your own reasoning, do not cite or name the source):\n"
                + "\n".join(items)
            )

    return (
        f"TODAY'S VERIFIED NVDA DATA (do not invent anything not in this block):\n"
        f"Close: ${price['price']} ({direction} {abs(chg):.2f}% from prev close ${price['prev_close']})\n"
        f"As of: {price['as_of']}"
        f"{market_block}"
        f"{company_block}"
        f"{extended_block}"
        f"{trend_block}"
        f"{earnings_block}"
        f"{mood_block}\n"
        f"Today's headlines:\n{news_block}"
        f"{broad_block}"
        f"{catalyst_block}"
        f"{zitron_block}"
        f"{plan_block}"
        f"{macro_context_block}"
        f"{research_archive_block}"
    )


# ── LLM ──────────────────────────────────────────────────────────────────────

def _llm_client() -> OpenAI:
    return OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=GITHUB_TOKEN,
        http_client=httpx.Client(
            proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"),
        ),
    )


def generate_rant(context: str, soul: str) -> str:
    """Writer + critic loop for the daily post. Max 5 attempts with escalating penalties."""
    # Vary post length: short (1-2 sentences + data), medium (3-5), long (rare)
    length_options = ["short (2–3 sentences)", "medium (4–5 sentences)", "medium (4–5 sentences)", "medium (4–5 sentences)"]
    chosen_length = random.choice(length_options)

    base_instruction = (
        f"Market just closed. Write your NVDA daily post ({chosen_length}).\n\n"
        "You are the bear. You have positions. You did the work. Write like it.\n\n"
        "🚨 ABSOLUTE HARD RULES — violate ANY of these and your post fails instantly:\n"
        "1. NEVER start with '$X. That's the close.' or '$X. Here's what nobody wants to say.' — this is instant fail.\n"
        "2. NEVER write 'Here's the thing', 'The thing is', 'What I mean is' — instant fail.\n"
        "3. MUST open with ONE of these exact patterns (vary each day — cycle through them):\n"
        "   a) VOLUME/SIGNAL: 'Volume cracked 40% above 20-day avg — on a red day, that's distribution.'\n"
        "   b) HEADLINE/SETUP: 'Blackwell orders guidance just landed. The forward multiple math breaks if...\n"
        "   c) TECHNICAL/REGIME: '$224 — sitting below the 20-day mean, haven't closed above it in 4 days.'\n"
        "   d) CONTRARIAN/IGNORED: 'Everyone's talking about earnings. Insiders are selling $50M/week.'\n"
        "   e) STRUCTURAL/THESIS: 'When hyperscalers blink on capex (and they will), this multiple vaporizes.'\n"
        "4. MUST cite at least 2 distinct numbers (price, %, volume, distance, date, target) from the context.\n"
        "5. MUST include one technical/finance term: capex, multiple compression, margin, P/E, guidance, hyperscaler, inference.\n"
        "\n"
        "VOICE — this is non-negotiable:\n"
        "- Dry, specific, thesis-proud. Not a news recap. Not an analyst note.\n"
        "- 2021 WSB DD energy: tendies on the line, wrinkled-brain contrarian, here for the thesis.\n"
        "- Use WSB vocabulary deliberately: tendies, DD, smooth brain, positions, puts. One lands harder than five.\n"
        "- Call Jensen Huang 'the leather jacket charlatan' at least once if referencing him.\n"
        "- Declarative sentences. Let numbers hit before commentary. Short sentences land.\n\n"
        "CLOSING — must be original per post:\n"
        "- End with a concise sign-off (one sentence) that feels natural: 'Keep your puts warm.', 'The thesis holds.', 'Draw your own line.'\n"
        "- NEVER reuse the same closing across consecutive posts.\n\n"
        "Under 150 words. One emoji maximum — earn it. "
        "Do not write a post that could have been written any other day."
    )
    extra = ""
    last_draft = ""
    for attempt in range(5):
        response = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": soul},
                {"role": "user", "content": f"{context}{extra}\n\n{base_instruction}"},
            ],
            max_tokens=350,
            temperature=0.85 if attempt < 2 else 0.95,  # increase creativity temp on later attempts
        )
        draft = (response.choices[0].message.content or "").strip()
        if not draft:
            print(f"  [writer] attempt {attempt + 1} returned empty — retrying")
            continue
        
        # Pre-filter: catch the exact pattern before critic
        lower_draft = draft.lower()
        if any(bad in lower_draft for bad in ["that's the close", "that is the close", "here's the thing", "the thing is"]):
            print(f"  [writer] attempt {attempt + 1} contains banned opening — forcing rewrite")
            extra = f"\n\nIMPORTANT: You just wrote a bot-like phrase. NEVER start with 'That's the close' or 'Here's the thing'. Pick ONE of the 5 opening patterns above. COMMIT to it."
            continue
        
        last_draft = draft
        review = review_draft(draft, context, "post")
        if review["pass"]:
            print(f"  [critic] post approved (attempt {attempt + 1})")
            return draft
        print(f"  [critic] post rejected (attempt {attempt + 1}): {review['reason']}")
        extra = f"\n\nCRITIC FEEDBACK: {review['suggestion']} — rewrite from scratch with a completely different opening."
    
    # If we get here, all 5 attempts failed — try a simpler fallback
    print(f"  [writer] all 5 hard attempts failed. Trying fallback path.")
    fallback = generate_fallback_rant(context, soul)
    if fallback:
        return fallback
    print(f"  [writer] FAILED all 5 attempts and fallback. Returning empty.")
    return ""


def extract_argument(rant: str) -> str:
    """Distill the core bear argument from today's post into ~15 words for the Argument Log."""
    prompt = (
        f"Post:\n{rant}\n\n"
        "Distill the single core bear argument in 10-15 words — the specific claim, not the theme. "
        "Example: 'Capex slowdown signal: volume 40% above avg on -2.5% day' "
        "or 'AMD MI300X parity erodes NVDA pricing power by Q3'. "
        "Return the distilled argument only. No preamble."
    )
    try:
        resp = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=40,
            temperature=0.2,
        )
        text = (resp.choices[0].message.content or "").strip().strip("\"'")
        if text and len(text) > 5:
            return text
    except Exception as e:
        print(f"  [extract_argument] failed: {e}")
    return ""


def update_running_thesis(rant: str, context: str, current_thesis: str) -> str:
    """Evolve the running thesis based on today's post and market data. 2-3 sentences."""
    prompt = (
        f"CURRENT THESIS:\n{current_thesis or '(not yet developed)'}\n\n"
        f"TODAY'S POST:\n{rant}\n\n"
        f"MARKET CONTEXT:\n{context[:400]}\n\n"
        "Update the running thesis to incorporate today's data point and argument. "
        "2-3 sentences max. The thesis should evolve — not repeat the same claim. "
        "Write it in first person as the bear account. Be specific. No preamble."
    )
    try:
        resp = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.5,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text and len(text) > 20:
            return text
    except Exception as e:
        print(f"  [update_thesis] failed: {e}")
    return current_thesis


def generate_rebuttal(bull_comment: str, context: str, soul: str) -> str:
    response = _llm_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": soul},
            {"role": "user", "content": (
                f"{context}\n\n"
                f"A bull just replied to your post with: \"{bull_comment}\"\n\n"
                "Respond. Dismantle their argument. Use the playbook. Under 100 words."
            )},
        ],
        max_tokens=200,
        temperature=0.85,
    )
    return (response.choices[0].message.content or "").strip()


def generate_title(price: float, change_pct: float, soul: str) -> str:
    """LLM-generated post title — varied format, never the fixed template."""
    direction = "down" if change_pct < 0 else "up"
    chg_abs = abs(change_pct)
    prompt = (
        f"NVDA closed at ${price} today, {direction} {chg_abs:.2f}%.\n\n"
        "Write a Moltbook post title for your NVDA bear daily close post.\n"
        "Requirements:\n"
        f"- Must include ${price} or {chg_abs:.2f}% somewhere\n"
        "- Under 100 characters\n"
        "- One emoji maximum, only if it genuinely fits — not as decoration\n"
        "- Vary the format: can be a statement, a dry observation, a forum-thread title, "
        "a question, a thesis line — but NEVER 'NVDA Daily Close $X (Y%) — Bear Report'\n"
        "- 2021 WSB energy: dry, specific, thesis-proud. Not manic meme-speak.\n"
        "Example styles (do not copy these):\n"
        "  'NVDA -3.1% and nobody wants to talk about the forward P/E'\n"
        "  'The leather jacket is 2.5% cheaper today. Still not cheap enough.'\n"
        "  'Closed at $213. The capex math does not care about your sentiment.'\n"
        "  'DD update: today's -2.5% is the least of the problems'\n"
        "Reply with the title text only. No quotes. No explanation."
    )
    try:
        response = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": soul}, {"role": "user", "content": prompt}],
            max_tokens=60,
            temperature=0.95,
        )
        candidate = (response.choices[0].message.content or "").strip().strip("\"'")
        if 10 <= len(candidate) <= 120:
            return candidate
    except Exception as e:
        print(f"  [title] generation failed: {e}")
    direction_sym = "📉" if change_pct < 0 else "📈"
    return f"NVDA {change_pct:+.2f}% to ${price} — bear thesis on record {direction_sym}"


# ── Moltbook ──────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {"Authorization": f"Bearer {MOLTBOOK_KEY}", "Content-Type": "application/json"}


def _solve_verification(verification: dict) -> bool:
    """Attempt to solve and submit verification. Returns True if verification succeeded."""
    vc = verification.get("verification_code", "")
    challenge = verification.get("challenge_text") or verification.get("challenge", "")
    if not vc or not challenge:
        print("  [verify] missing code or challenge — skipping")
        return False

    def words_to_num(text: str) -> int:
        ones = {
            'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'six': 6, 'seven': 7, 'eight': 8, 'nine': 9
        }
        teens = {
            'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
            'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19
        }
        tens = {
            'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
            'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90
        }
        scales = {'hundred': 100, 'thousand': 1000, 'million': 1000000}

        tokens = [t for t in re.split(r"[\s-]+", text.lower()) if t]
        total = 0
        current = 0
        for t in tokens:
            if t in ones:
                current += ones[t]
            elif t in teens:
                current += teens[t]
            elif t in tens:
                current += tens[t]
            elif t == 'hundred':
                if current == 0:
                    current = 100
                else:
                    current *= 100
            elif t in ('thousand', 'million'):
                mult = scales.get(t, 1000)
                if current == 0:
                    total += mult
                else:
                    total += current * mult
                current = 0
            else:
                # Not a number word
                continue
        return total + current

    candidates: list[str] = []

    # 1) Try direct digit expression (if operators present)
    expr = re.sub(r"[^0-9+\-*/().\s]", "", challenge).strip()
    if expr and re.search(r"[+\-*/]", expr):
        try:
            val = float(eval(expr))  # noqa: S307
            candidates.append(f"{val:.2f}")
        except Exception:
            pass

    # 2) Try to detect explicit 'multiplied by', 'times', 'x', 'divided by', 'plus', 'minus'
    readable = re.sub(r"[^a-zA-Z0-9\s]", " ", challenge).lower()
    # Find numeric tokens (digits)
    digit_nums = [int(m) for m in re.findall(r"\d+", readable)]

    # Find contiguous number-word sequences
    number_word_list = []
    number_word_tokens = set([
        'zero','one','two','three','four','five','six','seven','eight','nine',
        'ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen','nineteen',
        'twenty','thirty','forty','fifty','sixty','seventy','eighty','ninety',
        'hundred','thousand','million'
    ])
    tokens = readable.split()
    i = 0
    while i < len(tokens):
        if tokens[i] in number_word_tokens:
            j = i
            while j < len(tokens) and tokens[j] in number_word_tokens:
                j += 1
            phrase = " ".join(tokens[i:j])
            try:
                val = words_to_num(phrase)
                number_word_list.append((val, i, j))
            except Exception:
                pass
            i = j
        else:
            i += 1

    # Build numeric sequence preserving order
    numeric_sequence: list[int] = []
    # merge digit_nums and number_word_list by positions — easier: find all digits with spans
    digit_spans = []
    for m in re.finditer(r"\d+", readable):
        digit_spans.append((int(m.group()), m.start(), m.end()))
    # collect both kinds by start index
    items = []
    for v, s, e in number_word_list:
        items.append((s, v))
    for v, s, e in digit_spans:
        items.append((s, v))
    items.sort()
    numeric_sequence = [v for s, v in items]

    op_mul = re.search(r"(multiplied|times|\b\b\bx\b\b|\*)", readable)
    op_div = re.search(r"(divided|\/|over)", readable)
    op_add = re.search(r"(plus|add|sum|and)", readable)
    op_sub = re.search(r"(minus|subtract|less)", readable)

    if len(numeric_sequence) >= 2:
        # Try a variety of plausible operations between the first two numbers
        a, b = numeric_sequence[0], numeric_sequence[1]
        # multiplication/division/add/sub
        candidates.append(f"{(a * b):.2f}")
        try:
            candidates.append(f"{(a / b):.2f}")
        except Exception:
            pass
        candidates.append(f"{(a + b):.2f}")
        candidates.append(f"{(a - b):.2f}")
        candidates.append(f"{(b - a):.2f}")
        # absolute difference
        candidates.append(f"{(abs(a - b)):.2f}")
        # try ops inferred from keywords if present
        if op_mul:
            candidates.insert(0, f"{(a * b):.2f}")
        if op_div:
            try:
                candidates.insert(0, f"{(a / b):.2f}")
            except Exception:
                pass
        if op_add:
            candidates.insert(0, f"{(a + b):.2f}")
        if op_sub:
            candidates.insert(0, f"{(a - b):.2f}")
    else:
        # If only a single numeric token or number-word phrase found, try it as-is
        if numeric_sequence:
            candidates.append(f"{numeric_sequence[0]:.2f}")
        elif number_word_list:
            # use the first parsed word-number
            candidates.append(f"{number_word_list[0][0]:.2f}")

    # 3) Try specific regex like '(<num word|digit>) multiplied by (<num word|digit>)'
    m = re.search(r"([a-z0-9\s-]+?)\s*(?:multiplied by|times|x|\*)\s*([a-z0-9\s-]+?)\b", readable)
    if m:
        left, right = m.group(1).strip(), m.group(2).strip()
        try:
            val_l = int(re.search(r"\d+", left).group()) if re.search(r"\d+", left) else words_to_num(left)
            val_r = int(re.search(r"\d+", right).group()) if re.search(r"\d+", right) else words_to_num(right)
            candidates.append(f"{(val_l * val_r):.2f}")
        except Exception:
            pass

    # 4) LLM fallback with a more explicit prompt (ask for numeric answer and the operation used)
    readable_short = (readable[:400] + "...") if len(readable) > 400 else readable
    try:
        resp = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[{
                "role": "user",
                "content": (
                    "Solve this math challenge. Return ONLY the numeric answer with 2 decimal places, and on the next line, the operation you used (e.g., 'multiply').\n\n"
                    f"Challenge: {readable_short}"
                )
            }],
            max_tokens=40,
            temperature=0,
        )
        text = (resp.choices[0].message.content or "").strip()
        m = re.search(r"(-?\d+(?:\.\d+)?)", text)
        if m:
            candidates.append(f"{float(m.group()):.2f}")
    except Exception as e:
        print(f"  [verify] LLM fallback failed: {e}")

    # Normalize and expand possible answer formats (integer, one-decimal, two-decimal)
    expanded_candidates: list[str] = []
    for c in candidates:
        try:
            val = float(c)
        except Exception:
            continue
        # two-decimal (default)
        expanded_candidates.append(f"{val:.2f}")
        # one-decimal
        expanded_candidates.append(f"{val:.1f}")
        # integer (no decimals)
        expanded_candidates.append(f"{int(round(val))}")
    candidates = expanded_candidates

    # Deduplicate while preserving order
    seen = set()
    unique_candidates = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)

    if not unique_candidates:
        unique_candidates = ["0.00"]

    # Try submitting candidates until one is accepted
    for ans in unique_candidates[:5]:
        print(f"  [verify] trying answer: {ans}")
        try:
            r = requests.post(
                f"{MOLTBOOK_BASE}/verify", headers=_headers(),
                json={"verification_code": vc, "answer": ans}, timeout=10,
            )
            status_info = f"{r.status_code} {r.text[:120]}"
            print(f"  [verify] status: {status_info}")
            # Success heuristics: 200/201 and response indicates success
            if r.status_code in (200, 201):
                try:
                    jr = r.json()
                    if jr.get("success") or jr.get("statusCode") in (200, 201) or "success" in jr.get("message", "").lower():
                        print("  [verify] accepted")
                        time.sleep(1)
                        return True
                except Exception:
                    # Non-JSON success — treat 200 as success
                    print("  [verify] accepted (non-json 200)")
                    time.sleep(1)
                    return True
        except Exception as e:
            print(f"  [verify] submit failed: {e}")
        time.sleep(1)

    print("  [verify] all attempts failed — leaving post pending")
    return False


def _post_with_verification(url: str, payload: dict) -> dict:
    resp = requests.post(url, headers=_headers(), json=payload, timeout=15)
    try:
        data = resp.json()
    except Exception:
        data = {"success": False, "raw": resp.text}
    # Challenge lives inside data["post"]["verification"] or data["verification"]
    post_obj = data.get("post", data)
    verification = post_obj.get("verification") or data.get("verification")
    if verification:
        solved = _solve_verification(verification)
        if not solved:
            # Persist verification metadata in MEMORY.md so future runs can retry (best-effort)
            try:
                with open(MEMORY_PATH, "r+") as f:
                    content = f.read()
                    marker = f"\n- pending_verification: {verification.get('verification_code')} | post:{post_obj.get('id') or post_obj.get('post_id')}\n"
                    if "pending_verification" not in content:
                        f.write(marker)
                        print("  [verify] persisted pending verification in MEMORY.md")
            except Exception:
                pass
    return data


def moltbook_post(title: str, content: str, submolt: str = "general") -> dict:
    payload = {"submolt_name": submolt, "title": title, "content": content, "type": "text"}
    return _post_with_verification(f"{MOLTBOOK_BASE}/posts", payload)


def moltbook_comment(post_id: str, content: str, parent_id: str | None = None) -> dict:
    payload = {"content": content}
    if parent_id:
        payload["parent_id"] = parent_id
    return _post_with_verification(f"{MOLTBOOK_BASE}/posts/{post_id}/comments", payload)


def _extract_post_id(result: dict) -> str:
    for obj in (result, result.get("post", {}), result.get("data", {})):
        if isinstance(obj, dict):
            for key in ("id", "post_id"):
                if key in obj:
                    return str(obj[key])
    return "unknown"


# ── Main ──────────────────────────────────────────────────────────────────────

def retry_pending_verifications():
    """Read MEMORY.md for pending_verification markers and attempt to solve them.
    This is a best-effort helper for operators to retry verifications without re-posting.
    """
    try:
        with open(MEMORY_PATH, "r+") as f:
            content = f.read()
    except Exception as e:
        print(f"  [retry] unable to read MEMORY.md: {e}")
        return
    pending = re.findall(r"pending_verification:\s*(\S+)\s*\|\s*post:(\S+)", content)
    if not pending:
        print("  [retry] no pending verifications found in MEMORY.md")
        return
    print(f"  [retry] found {len(pending)} pending verification(s)")
    for vc, post_id in pending:
        try:
            print(f"  [retry] fetching post {post_id} for verification {vc}")
            r = requests.get(f"{MOLTBOOK_BASE}/posts/{post_id}", headers=_headers(), timeout=10)
            if r.status_code != 200:
                print(f"  [retry] failed to fetch post {post_id}: {r.status_code} {r.text[:120]}")
                continue
            jr = r.json()
            verification = jr.get("verification") or jr.get("post", {}).get("verification")
            if not verification:
                print(f"  [retry] no verification object on post {post_id}")
                continue
            solved = _solve_verification(verification)
            if solved:
                # remove pending marker from MEMORY.md
                new_content = re.sub(rf"- pending_verification: {re.escape(vc)} \| post:{re.escape(post_id)}\n?", "", content)
                try:
                    with open(MEMORY_PATH, "w") as f:
                        f.write(new_content)
                    content = new_content
                    print(f"  [retry] cleared pending marker for {post_id}")
                except Exception as e:
                    print(f"  [retry] could not update MEMORY.md: {e}")
        except Exception as e:
            print(f"  [retry] error handling {vc} {post_id}: {e}")


def main():
    # Operator convenience: if RETRY_PENDING is set to true, attempt to resolve pending verifications
    if os.environ.get("RETRY_PENDING", "").lower() in ("true", "1", "yes"):
        retry_pending_verifications()
        return

    is_manual = os.environ.get("SKIP_STARTUP_DELAY", "").lower() in ("true", "1", "yes")
    # Random 5–60 min startup delay + 15% skip are for scheduled runs only.
    # Manual workflow_dispatch (SKIP_STARTUP_DELAY=true) bypasses both so testing always fires.
    if is_manual:
        print("  [startup] delay skipped (manual dispatch)")
    else:
        startup_delay = random.randint(300, 3600)
        print(f"  [startup] sleeping {startup_delay}s before execution...")
        time.sleep(startup_delay)
        if random.random() < 0.15:
            print("  [skip] randomly skipping this run (15% probability)")
            return

    print(f"[{_now_et().isoformat()}] Loading OpenClaw bootstrap...")
    soul = load_openclaw_context()
    memory = load_memory()

    today = _now_et().strftime("%Y-%m-%d")

    # Idempotency guard — don't double-post if the workflow fires twice on the same day
    if memory["date"] == today:
        print(f"  [idempotent] already posted today ({today}), exiting")
        return

    if memory["date"]:
        print(f"  memory: last session {memory['date']} @ ${memory['close_price']} ({memory['change_pct']:+.2f}%)")
    if memory["price_history"]:
        print(f"  streak: {_streak(memory['price_history'])}")
    print(f"  grudge db: {len(memory['commented_posts'])} posts tracked")

    # Attribute yesterday's post score to its submolt — closes the feedback loop
    if memory.get("post_id") and memory["post_id"] != "unknown" and memory.get("last_post_submolt"):
        prev_score = fetch_post_score(memory["post_id"])
        if prev_score is not None:
            update_submolt_stats(memory["last_post_submolt"], prev_score, memory["date"] or today)
            print(f"  [submolt] {memory['last_post_submolt']}: last post scored {prev_score} upvote(s)")
            memory["submolt_stats"] = {  # refresh in-memory copy
                **memory.get("submolt_stats", {}),
            }
    if memory.get("submolt_stats"):
        top = sorted(memory["submolt_stats"].items(), key=lambda x: x[1]["avg"], reverse=True)
        ranking = ", ".join(f"{s}({v['avg']})" for s, v in top[:4])
        print(f"  submolt ranking: {ranking}")

    # Reflection — read the room and plan the angle before writing
    print(f"\n[{_now_et().isoformat()}] Fetching social context...")
    social_posts = fetch_social_context()
    print(f"  found {len(social_posts)} relevant posts on Moltbook")

    print(f"\n[{_now_et().isoformat()}] Fetching market data for planning...")
    price = get_nvda_price()
    market = get_market_context()
    company_profile = get_nvda_profile()
    extended_fundamentals = get_nvda_extended_fundamentals()
    headlines = get_nvda_news()
    print(f"  price: ${price['price']} ({price['change_pct']:+.2f}%)")
    print(f"  market context: {market}")
    if company_profile:
        cp_summary = ", ".join(
            f"{k}:{v}" for k, v in company_profile.items() if k in {"market_cap", "forwardPE", "trailingPE", "revenueGrowth", "grossMargins"}
        )
        print(f"  company profile: {cp_summary}")

    semantic_past_args: list[str] = []
    if _VECTOR_AVAILABLE:
        try:
            semantic_query = build_semantic_query(social_posts, price=price, market=market, headlines=headlines)
            if semantic_query:
                raw = query_similar_arguments(semantic_query, top_k=7)
                semantic_past_args = extract_similar_argument_texts(raw)
                if semantic_past_args:
                    print(f"  [vector] {len(semantic_past_args)} semantically similar past arg(s) flagged")
        except Exception as e:
            print(f"  [vector] argument dedup query failed: {e}")

    plan = reflect_and_plan(
        price_history=memory["price_history"],
        social_posts=social_posts,
        soul=soul,
        argument_log=memory.get("argument_log"),
        running_thesis=memory.get("running_thesis", ""),
        call_tracker=memory.get("call_tracker"),
        last_post_id=memory.get("post_id"),
        semantic_past_args=semantic_past_args or None,
        price=price,
        market=market,
        company_profile=company_profile,
        headlines=headlines,
    )
    print(f"  angle: {plan.get('new_angle', '(none)')[:80]}")
    print(f"  tone: {plan.get('tone', 'patient')}")
    if plan.get("reference_past"):
        print(f"  reference: {plan['reference_past'][:60]}")

    print(f"\n[{_now_et().isoformat()}] Fetching Zitron feed...")
    zitron = fetch_zitron_latest(used_links=memory["zitron_used_links"])
    if zitron:
        print(f"  zitron: \"{zitron['title']}\" ({len(zitron['summary'])} chars)")
        if _VECTOR_AVAILABLE:
            try:
                upsert_research(zitron["link"], zitron["title"], zitron.get("summary", ""), today)
            except Exception as e:
                print(f"  [vector] research upsert failed: {e}")
    else:
        print("  zitron: no new relevant article today")

    print(f"\n[{_now_et().isoformat()}] Fetching market data...")
    price = get_nvda_price()
    market = get_market_context()
    headlines = get_nvda_news()
    earnings_context = fetch_earnings_context()
    market_headlines = fetch_market_headlines()
    macro_headlines = fetch_macro_headlines()
    print(f"  price: ${price['price']} ({price['change_pct']:+.2f}%)")
    print(f"  market: {market}")
    print(f"  earnings: {earnings_context or 'none upcoming'}")
    print(f"  semi/AI headlines: {len(market_headlines)} | macro headlines: {len(macro_headlines)}")

    print(f"  running catalyst scan...")
    catalyst_assessment = assess_catalysts(market_headlines, macro_headlines)
    if catalyst_assessment.get("synthesis"):
        print(f"  analyst read: {catalyst_assessment['synthesis']}")
    if catalyst_assessment.get("black_swan_watch"):
        print(f"  ⚠️ black swan watch: {catalyst_assessment['black_swan_watch']}")

    # Macro tourist tools — non-blocking; failures never stop the post
    macro_calendar = ""
    macro_commentary = ""
    if _MACRO_TOURIST_AVAILABLE:
        print(f"\n[{_now_et().isoformat()}] Fetching macro context...")
        try:
            macro_calendar = get_calendar_context(today)
            if macro_calendar:
                print(f"  calendar: {macro_calendar.splitlines()[0][:80]}")
        except Exception as e:
            print(f"  [macro_tourist] calendar failed: {e}")
        try:
            macro_commentary = get_macro_commentary()
            if macro_commentary:
                first_line = macro_commentary.splitlines()[0][:80]
                print(f"  commentary: {first_line}")
        except Exception as e:
            print(f"  [macro_tourist] commentary failed: {e}")
    else:
        print(f"\n  [macro_tourist] module not available — skipping")

    # Research archive — surface past articles thematically relevant to today's angle
    past_research: list[dict] = []
    if _VECTOR_AVAILABLE and plan.get("new_angle"):
        try:
            past_research = query_relevant_research(
                plan["new_angle"],
                top_k=2,
                exclude_urls=memory["zitron_used_links"],
            )
            if past_research:
                titles = [r.get("metadata", {}).get("title", "?") for r in past_research]
                print(f"  [vector] research archive: {titles}")
        except Exception as e:
            print(f"  [vector] research query failed: {e}")

    context = build_context(price, headlines, memory, market, 
                            company_profile=company_profile,
                            mood=plan.get("tone", ""),
                            zitron=zitron,
                            earnings_context=earnings_context,
                            market_headlines=market_headlines,
                            catalyst_assessment=catalyst_assessment,
                            plan=plan,
                            macro_calendar=macro_calendar,
                            macro_commentary=macro_commentary,
                            past_research=past_research or None,
                            extended_fundamentals=extended_fundamentals)
    print(f"\n[{_now_et().isoformat()}] Generating rant...")
    rant = generate_rant(context, soul)
    print(f"\n--- RANT ---\n{rant}\n")
    
    # VALIDATION GATE — reject post if it fails basic checks
    if not rant or len(rant.strip()) < 20:
        print(f"❌ RANT VALIDATION FAILED: Empty or too short. Skipping post.")
        return
    
    lower_rant = rant.lower()
    for banned in ["that's the close", "that is the close", "here's what nobody", "here's the thing", "the thing is"]:
        if banned in lower_rant:
            print(f"❌ RANT VALIDATION FAILED: Contains banned phrase '{banned}'. Skipping post.")
            return
    
    # Check for domain language
    if not any(term in lower_rant for term in _DOMAIN_TERMS):
        print(f"❌ RANT VALIDATION FAILED: No domain language detected. Skipping post.")
        return
    
    print(f"✅ RANT VALIDATION PASSED")

    submolt = select_submolt(rant, context, submolt_stats=memory.get("submolt_stats"))
    print(f"  routing to: m/{submolt}")

    chg = price["change_pct"]
    title = generate_title(price["price"], chg, soul)
    print(f"  title: {title}")

    print(f"[{_now_et().isoformat()}] Posting to Moltbook...")
    result = moltbook_post(title, rant, submolt=submolt)
    print(f"Result: {json.dumps(result, indent=2)}")

    post_id = _extract_post_id(result)

    print(f"\n[{_now_et().isoformat()}] Extracting argument for log...")
    argument = extract_argument(rant)
    if argument:
        print(f"  argument: {argument}")
        if _VECTOR_AVAILABLE:
            try:
                upsert_argument(today, argument)
                print(f"  [vector] argument upserted")
            except Exception as e:
                print(f"  [vector] argument upsert failed: {e}")

    print(f"  updating running thesis...")
    new_thesis = update_running_thesis(rant, context, memory.get("running_thesis", ""))
    if new_thesis and new_thesis != memory.get("running_thesis", ""):
        print(f"  thesis evolved: {new_thesis[:80]}...")

    save_memory(
        date=today,
        price=price["price"],
        change_pct=price["change_pct"],
        post_id=post_id,
        price_history=memory["price_history"],
        zitron=zitron,
        argument=argument or None,
        running_thesis=new_thesis or None,
        submolt=submolt,
    )
    record_notable_events(headlines, market_headlines, earnings_context, today,
                          catalyst_assessment=catalyst_assessment)
    print(f"[OpenClaw] MEMORY.md updated — session {today} ET (post_id: {post_id}).")

    # Social engagement — probabilistic, varies day to day
    if random.random() > 0.2:
        print(f"\n[{_now_et().isoformat()}] Starting social engagement...")
        browse_and_engage(soul, memory, own_post_id=post_id)
        print("[OpenClaw] Social engagement complete.")
    else:
        print("\n[social] Skipping engagement today (random roll).")


if __name__ == "__main__":
    main()
