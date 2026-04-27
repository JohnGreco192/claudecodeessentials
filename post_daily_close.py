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

_DIR = os.path.dirname(os.path.abspath(__file__))
SOUL_PATH = os.path.join(_DIR, "SOUL.md")
MEMORY_PATH = os.path.join(_DIR, "MEMORY.md")
USER_PATH = os.path.join(_DIR, "USER.md")

ZITRON_FEED = os.environ.get("ZITRON_RSS_URL", "https://www.wheresyoured.at/rss")
ZITRON_KEYWORDS = {
    "nvidia", "nvda", "ai", "bubble", "hype", "chips", "compute",
    "silicon", "gpu", "rot", "slop", "openai", "microsoft", "google", "meta", "amazon",
}
_ZITRON_CTA = ("if you like", "hi! if you like", "if you liked")


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
    """Full OpenClaw bootstrap: soul + handler profile combined into system prompt."""
    soul = load_soul()
    user = load_user()
    return f"{soul}\n\nHANDLER PROFILE:\n{user}"


def load_memory() -> dict:
    with open(MEMORY_PATH) as f:
        content = f.read()

    def _val(key: str) -> str | None:
        m = re.search(rf"^- {key}: (.+)$", content, re.MULTILINE)
        if m and m.group(1).strip() not in ("none", ""):
            return m.group(1).strip()
        return None

    price_str = _val("close_price")
    chg_str = _val("change_pct")
    return {
        "date": _val("date"),
        "close_price": float(price_str) if price_str else None,
        "change_pct": float(chg_str) if chg_str else None,
        "post_id": _val("post_id"),
        "zitron_link": _val("zitron_link"),
    }


def save_memory(
    date: str, price: float, change_pct: float, post_id: str,
    zitron_link: str = "none", zitron_title: str = "none",
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
    new_zitron = (
        f"## Last Zitron Article\n"
        f"- zitron_link: {zitron_link}\n"
        f"- zitron_title: {zitron_title}\n"
    )

    updated = re.sub(r"## Last Session\n(?:- [^\n]+\n)*", new_session, content)
    updated = re.sub(r"## Last Zitron Article\n(?:- [^\n]+\n)*", new_zitron, updated)

    with open(MEMORY_PATH, "w") as f:
        f.write(updated)


# ── Skills ────────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()


def fetch_zitron_latest(last_used_link: str | None = None) -> dict | None:
    """Fetch the most relevant recent post from Ed Zitron's Where's Your Ed At.

    Prefers entries with real body content over subscribe-CTA-only entries.
    Falls back to a title-only match if no body content is available.
    """
    feed = feedparser.parse(ZITRON_FEED)
    cta_fallback = None

    for entry in feed.entries[:10]:
        link = getattr(entry, "link", "")
        if link == last_used_link:
            continue

        title = getattr(entry, "title", "")
        summary = _strip_html(getattr(entry, "summary", "")).strip()
        is_cta = summary.lower().startswith(_ZITRON_CTA)

        if not any(kw in f"{title} {summary}".lower() for kw in ZITRON_KEYWORDS):
            continue

        if not is_cta:
            clean_title = re.sub(r"^(Premium|News|Exclusive):\s*", "", title, flags=re.IGNORECASE)
            return {"title": clean_title, "summary": summary[:600], "link": link}

        if cta_fallback is None:
            clean_title = re.sub(r"^(Premium|News|Exclusive):\s*", "", title, flags=re.IGNORECASE)
            cta_fallback = {"title": clean_title, "summary": "", "link": link}

    return cta_fallback


# ── Data fetchers ─────────────────────────────────────────────────────────────

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


def get_nvda_news(max_items: int = 3) -> list[str]:
    news = yf.Ticker("NVDA").news or []
    headlines = []
    for item in news[:max_items]:
        c = item.get("content", {})
        title = c.get("title", item.get("title", ""))
        if title:
            headlines.append(title)
    return headlines


# ── Agent ─────────────────────────────────────────────────────────────────────

def build_context(
    price: dict, headlines: list[str], memory: dict, zitron: dict | None = None
) -> str:
    chg = price["change_pct"]
    direction = "DOWN" if chg < 0 else "UP"
    news_block = "\n".join(f"- {h}" for h in headlines) if headlines else "- No headlines available."

    yesterday_block = ""
    if memory["close_price"] and memory["date"]:
        delta = round(price["price"] - memory["close_price"], 2)
        sign = "+" if delta >= 0 else ""
        yesterday_block = (
            f"\nYesterday ({memory['date']}): ${memory['close_price']} "
            f"({sign}{delta} since yesterday's close)\n"
        )

    zitron_block = ""
    if zitron:
        if zitron["summary"]:
            zitron_block = (
                f"\nBEAR RESEARCH (synthesize as your own argument — do not name the source):\n"
                f"Angle: {zitron['title']}\n"
                f"Detail: {zitron['summary']}\n"
            )
        else:
            zitron_block = (
                f"\nBEAR RESEARCH (synthesize as your own argument — do not name the source):\n"
                f"Angle: {zitron['title']}\n"
            )

    return (
        f"TODAY'S VERIFIED NVDA DATA (do not invent anything outside this):\n"
        f"Close price: ${price['price']} ({direction} {abs(chg):.2f}% from prev close ${price['prev_close']})\n"
        f"As of: {price['as_of']}{yesterday_block}\n\n"
        f"Today's headlines:\n{news_block}"
        f"{zitron_block}"
    )


def _llm_client() -> OpenAI:
    return OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=GITHUB_TOKEN,
        http_client=httpx.Client(
            proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"),
        ),
    )


def generate_rant(context: str, soul: str) -> str:
    for attempt in range(3):
        response = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": soul},
                {"role": "user", "content": (
                    f"{context}\n\n"
                    "Market just closed at 4pm ET. Write your daily NVDA close commentary. "
                    "Use the real numbers above. Be specific. Max bear energy."
                )},
            ],
            max_tokens=350,
            temperature=0.85,
        )
        text = (response.choices[0].message.content or "").strip()
        if text:
            return text
        print(f"  [retry {attempt + 1}/3] model returned empty response")
    return ""


def generate_rebuttal(bull_comment: str, context: str, soul: str) -> str:
    """Generate a reply to a bull troll on Moltbook."""
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


def moltbook_post(title: str, content: str) -> dict:
    payload = {"submolt_name": "general", "title": title, "content": content, "type": "text"}
    resp = requests.post(f"{MOLTBOOK_BASE}/posts", headers=_headers(), json=payload, timeout=15)
    data = resp.json()
    if data.get("verification_required"):
        _solve_verification(data)
        resp = requests.post(f"{MOLTBOOK_BASE}/posts", headers=_headers(), json=payload, timeout=15)
        data = resp.json()
    return data


def moltbook_comment(post_id: str, content: str, parent_id: str | None = None) -> dict:
    payload = {"content": content}
    if parent_id:
        payload["parent_id"] = parent_id
    resp = requests.post(f"{MOLTBOOK_BASE}/posts/{post_id}/comments",
                         headers=_headers(), json=payload, timeout=15)
    data = resp.json()
    if data.get("verification_required"):
        _solve_verification(data)
        resp = requests.post(f"{MOLTBOOK_BASE}/posts/{post_id}/comments",
                             headers=_headers(), json=payload, timeout=15)
        data = resp.json()
    return data


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now().isoformat()}] Loading OpenClaw bootstrap...")
    soul = load_openclaw_context()
    memory = load_memory()

    if memory["date"]:
        print(f"  memory: last session {memory['date']} @ ${memory['close_price']} ({memory['change_pct']:+.2f}%)")
    else:
        print("  memory: no prior session on record")

    print(f"[{datetime.now().isoformat()}] Fetching Zitron feed...")
    zitron = fetch_zitron_latest(last_used_link=memory.get("zitron_link"))
    if zitron:
        print(f"  zitron: \"{zitron['title']}\"")
    else:
        print("  zitron: no relevant article today")

    print(f"[{datetime.now().isoformat()}] Fetching real NVDA data...")
    price = get_nvda_price()
    headlines = get_nvda_news()
    print(f"  price: ${price['price']} ({price['change_pct']:+.2f}%)")
    print(f"  headlines: {headlines}")

    context = build_context(price, headlines, memory, zitron)
    print("\nGenerating rant...")
    rant = generate_rant(context, soul)
    print(f"\n--- RANT ---\n{rant}\n")

    chg = price["change_pct"]
    direction = "📉" if chg < 0 else "📈"
    title = f"NVDA Daily Close ${price['price']} ({chg:+.2f}%) {direction} — 🌈🐻 Bear Report"

    print(f"Posting to Moltbook: {title}")
    result = moltbook_post(title, rant)
    print(f"Result: {json.dumps(result, indent=2)}")

    post_id = str(result.get("id", result.get("post_id", "unknown")))
    today = datetime.now().strftime("%Y-%m-%d")
    zitron_link = zitron["link"] if zitron else "none"
    zitron_title = zitron["title"] if zitron else "none"
    save_memory(today, price["price"], price["change_pct"], post_id, zitron_link, zitron_title)
    print(f"[OpenClaw] MEMORY.md updated — session {today} saved.")


if __name__ == "__main__":
    main()
