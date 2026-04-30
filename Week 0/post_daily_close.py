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
import requests
import feedparser
import yfinance as yf
import httpx
from openai import OpenAI
from datetime import datetime, timezone, timedelta

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
DAILY_POST_SUBMOLTS = ["general", "ai", "finance", "stocks"]


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
    new_op_line = f"- {date} | {post_id}\n"
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
        candidates.append({"title": clean_title, "summary": summary[:800], "link": link, "score": score})

    if not candidates:
        return None
    best = max(candidates, key=lambda x: x["score"])
    best.pop("score")
    return best


# ── Market Data ───────────────────────────────────────────────────────────────

def get_nvda_price() -> dict:
    fi = yf.Ticker("NVDA").fast_info
    current = float(fi.last_price)
    prev = float(fi.previous_close)
    return {
        "price": round(current, 2),
        "prev_close": round(prev, 2),
        "change_pct": round(((current - prev) / prev) * 100, 2),
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M UTC"),
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
    try:
        hist = yf.Ticker("NVDA").history(period="22d")
        if not hist.empty and len(hist) > 1:
            avg_vol = float(hist["Volume"].iloc[:-1].mean())
            today_vol = float(hist["Volume"].iloc[-1])
            if avg_vol > 0:
                ctx["vol_ratio"] = round(today_vol / avg_vol, 2)
    except Exception:
        pass
    try:
        fi = yf.Ticker("NVDA").fast_info
        high = float(fi.year_high)
        current = float(fi.last_price)
        if high > 0:
            ctx["pct_from_52w_high"] = round((current / high - 1) * 100, 1)
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

    prompt = (
        f"PRICE STREAK: {streak}\n"
        f"{last_call_line}\n"
        f"{score_line}\n"
        f"RUNNING THESIS: {running_thesis or '(not yet developed)'}"
        f"{arg_block}\n\n"
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
]


def review_draft(draft: str, context: str, draft_type: str = "post") -> dict:
    """Critic agent — checks a draft for quality before it gets posted.

    Returns {"pass": bool, "reason": str, "suggestion": str}.
    Defaults to pass on any parsing failure so the critic never blocks silently.
    """
    # Fast pre-check: banned AI phrases are an instant reject
    lower = draft.lower()
    for phrase in _BANNED_REVIEW_PHRASES:
        if phrase in lower:
            return {
                "pass": False,
                "reason": f"Contains banned phrase: '{phrase}'",
                "suggestion": f"Remove '{phrase}' — sounds like generic LLM output. Rewrite with specific data.",
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


def select_submolt(rant: str, context: str) -> str:
    """Pick the most relevant submolt for the daily post."""
    options = ", ".join(DAILY_POST_SUBMOLTS)
    prompt = (
        f"You are routing a Moltbook post to the right community.\n\n"
        f"POST:\n{rant[:300]}\n\n"
        f"TODAY'S CONTEXT (what drove the rant):\n{context[:300]}\n\n"
        f"Available submolts: {options}\n\n"
        "Which fits best? Reply with the submolt name only — nothing else."
    )
    try:
        response = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.1,
        )
        result = (response.choices[0].message.content or "").strip().lower()
        if result in DAILY_POST_SUBMOLTS:
            return result
    except Exception:
        pass
    return "general"


def generate_social_comment(post: dict, top_comments: list[dict], soul: str) -> str:
    """Targeted bear comment — reads the room, then passes through the critic."""
    comment_block = "\n".join(
        f"- {c.get('author', {}).get('name', '?')}: {c['content'][:200]}"
        for c in top_comments[:3]
    )
    thread_context = (
        f"Post title: {post.get('title', '')}\n"
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
            print(f"  [social] skipping (random roll): {post.get('title', '')[:40]}")
            continue

        comment = generate_social_comment(post, existing, soul)
        if not comment:
            continue

        result = moltbook_comment(pid, comment)
        if result.get("id") or result.get("success"):
            print(f"  [social] commented on: {post.get('title', '')[:60]}...")
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
    mood: str = "",
    zitron: dict | None = None,
    earnings_context: str | None = None,
    market_headlines: list[str] | None = None,
    catalyst_assessment: dict | None = None,
    plan: dict | None = None,
) -> str:
    chg = price["change_pct"]
    direction = "DOWN" if chg < 0 else "UP"
    news_block = "\n".join(f"- {h}" for h in headlines) if headlines else "- No headlines available."

    market_lines = []
    if "vol_ratio" in market:
        pct = round((market["vol_ratio"] - 1) * 100)
        label = "above" if pct >= 0 else "below"
        market_lines.append(f"Volume: {abs(pct)}% {label} 20-day average")
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
        detail = f"\nExcerpt: {zitron['summary'][:500]}" if zitron.get("summary") else ""
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

    return (
        f"TODAY'S VERIFIED NVDA DATA (do not invent anything not in this block):\n"
        f"Close: ${price['price']} ({direction} {abs(chg):.2f}% from prev close ${price['prev_close']})\n"
        f"As of: {price['as_of']}"
        f"{market_block}"
        f"{trend_block}"
        f"{earnings_block}"
        f"{mood_block}\n"
        f"Today's headlines:\n{news_block}"
        f"{broad_block}"
        f"{catalyst_block}"
        f"{zitron_block}"
        f"{plan_block}"
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
    """Writer + critic loop for the daily post. Max 3 attempts."""
    # Vary post length: short (1-2 sentences + data), medium (3-5), long (rare)
    length_options = ["short (2–3 sentences)", "medium (4–5 sentences)", "medium (4–5 sentences)", "medium (4–5 sentences)"]
    chosen_length = random.choice(length_options)

    base_instruction = (
        f"Market just closed. Write your NVDA close post ({chosen_length}).\n"
        "Structure: (1) a concrete data point from today — cite the actual number, "
        "(2) your interpretation of what that number means for the bear thesis — NOT a summary, "
        "(3) one open question or uncertainty that bulls haven't answered. "
        "MANDATORY: At least 2 specific numbers from the data block above. "
        "If a headline is relevant, name it — don't paraphrase. "
        "Do not write a post that could have been written any other day. "
        "Under 150 words. Dry wit over emoji spray. "
        "Phrase like: 'based on today's price action' or 'looking at volume vs average' — "
        "anchor your read to what you're actually observing."
    )
    extra = ""
    last_draft = ""
    for attempt in range(3):
        response = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": soul},
                {"role": "user", "content": f"{context}{extra}\n\n{base_instruction}"},
            ],
            max_tokens=350,
            temperature=0.85,
        )
        draft = (response.choices[0].message.content or "").strip()
        if not draft:
            print(f"  [writer] attempt {attempt + 1} returned empty — retrying")
            continue
        last_draft = draft
        review = review_draft(draft, context, "post")
        if review["pass"]:
            print(f"  [critic] post approved (attempt {attempt + 1})")
            return draft
        print(f"  [critic] post rejected (attempt {attempt + 1}): {review['reason']}")
        extra = f"\n\nCRITIC FEEDBACK: {review['suggestion']} — rewrite addressing this."
    return last_draft


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


def _solve_verification(resp_data: dict) -> None:
    vc = resp_data.get("verification_code", "")
    challenge = resp_data.get("challenge", "")
    expr = re.sub(r"[^0-9+\-*/().\s]", "", challenge)
    try:
        answer = str(round(float(eval(expr)), 2))  # noqa: S307
    except Exception:
        answer = "0"
    print(f"  verification: '{challenge}' → {answer}")
    requests.post(f"{MOLTBOOK_BASE}/verify", headers=_headers(),
                  json={"verification_code": vc, "answer": answer}, timeout=10)
    time.sleep(2)


def _post_with_verification(url: str, payload: dict) -> dict:
    resp = requests.post(url, headers=_headers(), json=payload, timeout=15)
    data = resp.json()
    if data.get("verification_required"):
        _solve_verification(data)
        resp = requests.post(url, headers=_headers(), json=payload, timeout=15)
        data = resp.json()
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

def main():
    # Random 5–60 min startup delay (adds variance on top of per-weekday cron spread)
    startup_delay = random.randint(300, 3600)
    print(f"  [startup] sleeping {startup_delay}s before execution...")
    time.sleep(startup_delay)

    # 15% chance to skip this run entirely
    if random.random() < 0.15:
        print("  [skip] randomly skipping this run (15% probability)")
        return

    print(f"[{_now_et().isoformat()}] Loading OpenClaw bootstrap...")
    soul = load_openclaw_context()
    memory = load_memory()

    today = _now_et().strftime("%Y-%m-%d")
    if memory.get("date") == today:
        print(f"Already posted today ({today} ET). Exiting to prevent duplicate posts.")
        return

    if memory["date"]:
        print(f"  memory: last session {memory['date']} @ ${memory['close_price']} ({memory['change_pct']:+.2f}%)")
    if memory["price_history"]:
        print(f"  streak: {_streak(memory['price_history'])}")
    print(f"  grudge db: {len(memory['commented_posts'])} posts tracked")

    # Reflection — read the room and plan the angle before writing
    print(f"\n[{_now_et().isoformat()}] Fetching social context...")
    social_posts = fetch_social_context()
    print(f"  found {len(social_posts)} relevant posts on Moltbook")
    plan = reflect_and_plan(
        price_history=memory["price_history"],
        social_posts=social_posts,
        soul=soul,
        argument_log=memory.get("argument_log"),
        running_thesis=memory.get("running_thesis", ""),
        call_tracker=memory.get("call_tracker"),
        last_post_id=memory.get("post_id"),
    )
    print(f"  angle: {plan.get('new_angle', '(none)')[:80]}")
    print(f"  tone: {plan.get('tone', 'patient')}")
    if plan.get("reference_past"):
        print(f"  reference: {plan['reference_past'][:60]}")

    print(f"\n[{_now_et().isoformat()}] Fetching Zitron feed...")
    zitron = fetch_zitron_latest(used_links=memory["zitron_used_links"])
    if zitron:
        print(f"  zitron: \"{zitron['title']}\" ({len(zitron['summary'])} chars)")
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

    context = build_context(price, headlines, memory, market, plan.get("tone", ""), zitron,
                            earnings_context=earnings_context,
                            market_headlines=market_headlines,
                            catalyst_assessment=catalyst_assessment,
                            plan=plan)
    print(f"\n[{_now_et().isoformat()}] Generating rant...")
    rant = generate_rant(context, soul)
    print(f"\n--- RANT ---\n{rant}\n")

    submolt = select_submolt(rant, context)
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
