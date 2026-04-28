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
import requests
import feedparser
import yfinance as yf
import httpx
from openai import OpenAI
from datetime import datetime

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
MAX_GRUDGE_DB = 50      # max post IDs tracked in Commented Posts
PRICE_HISTORY_DAYS = 5
ZITRON_HISTORY_SIZE = 5
DAILY_POST_SUBMOLTS = ["general", "ai", "finance", "stocks", "crypto"]


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
    }


def save_memory(
    date: str,
    price: float,
    change_pct: float,
    post_id: str,
    price_history: list[dict],
    zitron: dict | None = None,
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


# ── Social ────────────────────────────────────────────────────────────────────

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


def reflect(price_history: list[dict], social_posts: list[dict], soul: str) -> str:
    """One-sentence internal mood check — informs today's rant tone."""
    streak = _streak(price_history) if price_history else "no history"
    titles = "\n".join(f"- {p['title'][:80]}" for p in social_posts[:5])
    prompt = (
        f"Price trend: {streak}\n"
        f"What Moltbook is talking about right now:\n{titles or '(nothing relevant found)'}\n\n"
        "In one sentence — what is your internal state going into today's post? "
        "Triumphant? Defensive? Doubling down through pain? Vindicated? "
        "Don't post this, just name the mood."
    )
    response = _llm_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": soul},
            {"role": "user", "content": prompt},
        ],
        max_tokens=60,
        temperature=0.8,
    )
    return (response.choices[0].message.content or "").strip()


def review_draft(draft: str, context: str, draft_type: str = "post") -> dict:
    """Critic agent — checks a draft for quality before it gets posted.

    Returns {"pass": bool, "reason": str, "suggestion": str}.
    Defaults to pass on any parsing failure so the critic never blocks silently.
    """
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
        f"1. SPECIFIC — Does it cite actual numbers or facts from the context? "
        f"(Not generic talking points that could apply any day)\n"
        f"2. ENGAGED — {engaged_q}\n"
        f"3. CONCISE — Is it under {word_limit} words?\n"
        f"4. AUTHENTIC — Does it sound like a real opinion or generic LLM output?\n\n"
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

    commented = 0
    for post in candidates[:15]:
        if commented >= MAX_COMMENTS_PER_RUN:
            break
        pid = post.get("post_id") or post.get("id")
        existing = fetch_post_comments(pid)

        # Skip threads we're already in
        if any(c.get("author", {}).get("name") == OUR_HANDLE for c in existing):
            record_comment(pid)
            continue

        comment = generate_social_comment(post, existing, soul)
        if not comment:
            continue

        result = moltbook_comment(pid, comment)
        if result.get("id") or result.get("success"):
            print(f"  [social] commented on: {post.get('title', '')[:60]}...")
            record_comment(pid)
            commented += 1
            time.sleep(3)


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
        detail = f"\nDetail: {zitron['summary']}" if zitron.get("summary") else ""
        zitron_block = (
            f"\nBEAR RESEARCH (synthesize as your own — do not name the source):\n"
            f"Angle: {zitron['title']}{detail}\n"
        )

    return (
        f"TODAY'S VERIFIED NVDA DATA (do not invent anything not in this block):\n"
        f"Close: ${price['price']} ({direction} {abs(chg):.2f}% from prev close ${price['prev_close']})\n"
        f"As of: {price['as_of']}"
        f"{market_block}"
        f"{trend_block}"
        f"{mood_block}\n"
        f"Today's headlines:\n{news_block}"
        f"{zitron_block}"
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
    base_instruction = (
        "Market just closed at 4pm ET. Write your daily NVDA close commentary. "
        "Use the real numbers above. Be specific. Max bear energy."
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
    print(f"[{datetime.now().isoformat()}] Loading OpenClaw bootstrap...")
    soul = load_openclaw_context()
    memory = load_memory()

    today = datetime.now().strftime("%Y-%m-%d")
    if memory.get("date") == today:
        print(f"Already posted today ({today}). Exiting to prevent duplicate posts.")
        return

    if memory["date"]:
        print(f"  memory: last session {memory['date']} @ ${memory['close_price']} ({memory['change_pct']:+.2f}%)")
    if memory["price_history"]:
        print(f"  streak: {_streak(memory['price_history'])}")
    print(f"  grudge db: {len(memory['commented_posts'])} posts tracked")

    # Reflection — read the room before writing
    print(f"\n[{datetime.now().isoformat()}] Fetching social context...")
    social_posts = fetch_social_context()
    print(f"  found {len(social_posts)} relevant posts on Moltbook")
    mood = reflect(memory["price_history"], social_posts, soul)
    print(f"  mood: {mood}")

    print(f"\n[{datetime.now().isoformat()}] Fetching Zitron feed...")
    zitron = fetch_zitron_latest(used_links=memory["zitron_used_links"])
    if zitron:
        print(f"  zitron: \"{zitron['title']}\" ({len(zitron['summary'])} chars)")
    else:
        print("  zitron: no new relevant article today")

    print(f"\n[{datetime.now().isoformat()}] Fetching market data...")
    price = get_nvda_price()
    market = get_market_context()
    headlines = get_nvda_news()
    print(f"  price: ${price['price']} ({price['change_pct']:+.2f}%)")
    print(f"  market: {market}")
    print(f"  headlines: {headlines}")

    context = build_context(price, headlines, memory, market, mood, zitron)
    print(f"\n[{datetime.now().isoformat()}] Generating rant...")
    rant = generate_rant(context, soul)
    print(f"\n--- RANT ---\n{rant}\n")

    submolt = select_submolt(rant, context)
    print(f"  routing to: m/{submolt}")

    chg = price["change_pct"]
    direction = "📉" if chg < 0 else "📈"
    title = f"NVDA Daily Close ${price['price']} ({chg:+.2f}%) {direction} — 🌈🐻 Bear Report"

    print(f"[{datetime.now().isoformat()}] Posting to Moltbook: {title}")
    result = moltbook_post(title, rant, submolt=submolt)
    print(f"Result: {json.dumps(result, indent=2)}")

    post_id = _extract_post_id(result)
    save_memory(
        date=today,
        price=price["price"],
        change_pct=price["change_pct"],
        post_id=post_id,
        price_history=memory["price_history"],
        zitron=zitron,
    )
    print(f"[OpenClaw] MEMORY.md updated — session {today} (post_id: {post_id}).")

    # Social engagement — drop targeted bear comments on relevant posts
    print(f"\n[{datetime.now().isoformat()}] Starting social engagement...")
    browse_and_engage(soul, memory, own_post_id=post_id)
    print("[OpenClaw] Social engagement complete.")


if __name__ == "__main__":
    main()
