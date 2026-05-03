"""
NVDA Bear Morning Hunt — Pre-market sentiment analysis and engagement.
Finds NVDA-related posts and engages with analytical counterpoints, not taunts.
Runs via GitHub Actions. No targeting, no downvoting, challenge framing only.
"""
import os
import re
import time
import json
import random
import requests
import httpx
from openai import OpenAI
from datetime import datetime, timezone, timedelta

try:
    from follower_vectors import upsert_rebuttal, query_similar_rebuttal, best_prior_rebuttal
    _VECTOR_AVAILABLE = True
except ImportError:
    _VECTOR_AVAILABLE = False

MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"
MOLTBOOK_KEY = os.environ["MOLTBOOK_API_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
MODEL = "Meta-Llama-3.1-8B-Instruct"
OUR_HANDLE = "nvda_regard"

_DIR = os.path.dirname(os.path.abspath(__file__))
SOUL_PATH = os.path.join(_DIR, "SOUL.md")
MEMORY_PATH = os.path.join(_DIR, "MEMORY.md")
USER_PATH = os.path.join(_DIR, "USER.md")

MAX_ENGAGEMENTS = 3
MAX_GRUDGE_DB = 50
SKIP_PROBABILITY = 0.15

_UTC = timezone.utc


def _now_et() -> datetime:
    now_utc = datetime.now(_UTC)
    year = now_utc.year
    mar1 = datetime(year, 3, 1, tzinfo=_UTC)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7, hours=7)
    nov1 = datetime(year, 11, 1, tzinfo=_UTC)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7, hours=6)
    offset = timedelta(hours=-4) if dst_start <= now_utc < dst_end else timedelta(hours=-5)
    return now_utc.astimezone(timezone(offset))


# Mixed sentiment — not just bullish targets
SEARCH_TERMS = [
    "nvidia", "nvda", "h100", "jensen huang",
    "blackwell", "ai capex", "gpu demand",
    "nvda earnings", "nvidia valuation",
]
HUNT_SUBMOLTS = {"general", "ai", "stocks", "finance", "markets", "tech"}

MORNING_MODE = """
MORNING ENGAGEMENT MODE — analytical, pre-market:
Scan Moltbook for NVDA discussion and drop a thoughtful analytical challenge or probing question.

CHALLENGE FRAMING (not insults):
  BAD:  "this is pure hopium, enjoy losing your tendies"
  GOOD: "what's your assumption on gross margin compression if capex slows?"
  BAD:  "lmao good luck when the bell rings"
  GOOD: "curious what the thesis is if inference doesn't scale the way training did"

Format options — pick one that fits what they actually said:
  - Probing question about an assumption they're making
  - Data point that complicates their view
  - Structural risk they haven't addressed
  - Market mechanic they might be missing

Under 80 words. One emoji maximum, only if it genuinely lands.
No insults. No taunts. No vote references.
Include at least one domain term: forward P/E, multiple compression, margin pressure,
IV crush, capex cycle, cost per token, gross margin, insider selling.
"""


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def load_soul() -> str:
    with open(SOUL_PATH) as f:
        lines = f.readlines()
    base = "".join(l for l in lines if not l.startswith("# ")).strip()
    with open(USER_PATH) as f:
        user_lines = f.readlines()
    user = "".join(l for l in user_lines if not l.startswith("# ")).strip()
    return f"{base}\n\n{MORNING_MODE}\n\nHANDLER PROFILE:\n{user}"


# ── Memory ───────────────────────────────────────────��────────────────────────

def already_hunted_today() -> bool:
    today = _now_et().strftime("%Y-%m-%d")
    with open(MEMORY_PATH) as f:
        content = f.read()
    m = re.search(r"^- hunt_date: (.+)$", content, re.MULTILINE)
    return bool(m and m.group(1).strip() == today)


def load_grudge_db() -> set[str]:
    with open(MEMORY_PATH) as f:
        content = f.read()
    ids: set[str] = set()
    cp = re.search(r"## Commented Posts\n((?:- .+\n?)*)", content)
    if cp:
        for line in cp.group(1).strip().splitlines():
            pid = line.strip().lstrip("- ").strip()
            if pid:
                ids.add(pid)
    return ids


def record_engagement(post_id: str, hunt_date: str) -> None:
    with open(MEMORY_PATH) as f:
        content = f.read()

    if "## Commented Posts" not in content:
        content = content.rstrip() + "\n\n## Commented Posts\n"
    content = re.sub(r"(## Commented Posts\n)", f"\\1- {post_id}\n", content, count=1)
    m = re.search(r"## Commented Posts\n((?:- .+\n?)*)", content)
    if m:
        lines = m.group(1).strip().splitlines(keepends=True)
        if len(lines) > MAX_GRUDGE_DB:
            content = re.sub(
                r"## Commented Posts\n(?:- .+\n?)*",
                "## Commented Posts\n" + "".join(lines[:MAX_GRUDGE_DB]),
                content,
            )

    hunt_line = f"- hunt_date: {hunt_date}\n"
    if "## Last Hunt" in content:
        content = re.sub(r"## Last Hunt\n(?:- [^\n]+\n)*", f"## Last Hunt\n{hunt_line}", content)
    else:
        content = content.rstrip() + f"\n\n## Last Hunt\n{hunt_line}"

    with open(MEMORY_PATH, "w") as f:
        f.write(content)


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


def post_comment(post_id: str, content: str) -> dict:
    url = f"{MOLTBOOK_BASE}/posts/{post_id}/comments"
    payload = {"content": content}
    resp = requests.post(url, headers=_headers(), json=payload, timeout=15)
    data = resp.json()
    if data.get("verification_required"):
        _solve_verification(data)
        resp = requests.post(url, headers=_headers(), json=payload, timeout=15)
        data = resp.json()
    return data


def fetch_comments(post_id: str) -> list[dict]:
    try:
        r = requests.get(f"{MOLTBOOK_BASE}/posts/{post_id}/comments",
                         params={"limit": 10}, timeout=8)
        if r.status_code == 200:
            return r.json().get("comments", [])
    except Exception:
        pass
    return []


# ── LLM ─────────────────────────���────────────────────────────────────────────

def _llm_client() -> OpenAI:
    return OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=GITHUB_TOKEN,
        http_client=httpx.Client(
            proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"),
        ),
    )


_BANNED_PHRASES = [
    "in today's world", "it's important to note", "as we can see",
    "it's worth noting", "ultimately,", "have fun being poor",
    "tick tock", "enjoy those calls", "when the bell rings",
    "good luck with your", "you'll regret",
]


def _check_banned(text: str) -> str | None:
    lower = text.lower()
    for phrase in _BANNED_PHRASES:
        if phrase in lower:
            return phrase
    return None


def generate_challenge(post: dict, comments: list[dict], soul: str,
                       prior_exchange: dict | None = None) -> str:
    """Generate an analytical challenge or probing question — no taunts."""
    comment_block = "\n".join(
        f"- {c.get('author', {}).get('name', '?')}: {c['content'][:150]}"
        for c in comments[:3]
    )
    thread = (
        f"Post title: {post.get('title', '')}\n"
        f"Content: {str(post.get('content', ''))[:300]}\n"
        f"Comments:\n{comment_block or '(none yet)'}"
    )

    combat_block = ""
    if prior_exchange:
        meta = prior_exchange.get("metadata", {})
        score = prior_exchange.get("score", 0)
        prev_challenge = meta.get("rebuttal", "")
        prev_bull = meta.get("bull_argument", "")
        if prev_challenge:
            combat_block = (
                f"\n\nCOMBAT MEMORY — you've challenged similar content before "
                f"(similarity {score:.2f}):\n"
                f"Their post was about: {prev_bull[:150]}\n"
                f"Your prior challenge: {prev_challenge}\n"
                "Sharpen this angle or find a more specific hook from what they actually said."
            )
    format_options = [
        "probing question about their underlying assumptions",
        "data-driven observation that complicates their thesis",
        "structural risk they haven't addressed in this post",
        "market mechanic they might be missing",
        "scenario question: what happens to their thesis if X",
    ]
    chosen = random.choice(format_options)

    prompt = (
        f"Pre-market NVDA discussion:\n\n{thread}{combat_block}\n\n"
        f"Write a {chosen}. Challenge framing — analytical, not a taunt. "
        "Reference what they specifically said. Under 80 words. "
        "Must include at least one domain term: forward P/E, multiple compression, "
        "margin pressure, IV crush, capex cycle, gross margin, cost per token, insider selling. "
        "Phrase it as if you're genuinely curious or pointing out something concrete."
    )

    extra = ""
    last = ""
    for attempt in range(2):
        resp = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": soul},
                {"role": "user", "content": prompt + extra},
            ],
            max_tokens=150,
            temperature=0.9,
        )
        draft = (resp.choices[0].message.content or "").strip()
        if not draft:
            continue
        last = draft

        banned = _check_banned(draft)
        if banned:
            print(f"    [critic] banned phrase detected: '{banned}'")
            extra = f"\n\nCRITIC: Remove '{banned}' — rewrite without taunts or clichés."
            continue

        review = _review(draft, thread)
        if review["pass"]:
            print(f"    [critic] challenge approved (attempt {attempt + 1})")
            return draft
        print(f"    [critic] rejected: {review['reason']}")
        extra = f"\n\nCRITIC: {review['suggestion']} — rewrite."

    return last


def _review(draft: str, context: str) -> dict:
    prompt = (
        f"Context the agent saw:\n{context[:400]}\n\n"
        f"Draft comment:\n{draft}\n\n"
        "Evaluate against these criteria:\n"
        "1. SPECIFIC — References something concrete from the post, not generic bear points?\n"
        "2. CHALLENGE FRAMING — Is it a question or analytical point, NOT a taunt or insult?\n"
        "3. DOMAIN LANGUAGE — Contains at least one market/finance term?\n"
        "4. HUMAN VOICE — Varies in structure, has a clear stance, not overly polite?\n"
        "5. CONCISE — Under 80 words?\n"
        'JSON only: {"pass": true/false, "reason": "one sentence", "suggestion": "specific fix if failing"}'
    )
    try:
        resp = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.1,
        )
        text = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {"pass": True, "reason": "critic unavailable", "suggestion": ""}


# ── Hunt ─────────────────────────────────────────��────────────────────────────

def find_relevant_posts(grudge_db: set[str]) -> list[dict]:
    """Search for recent NVDA-related posts — mixed sentiment."""
    seen = set(grudge_db)
    candidates = []

    for term in SEARCH_TERMS:
        try:
            r = requests.get(
                f"{MOLTBOOK_BASE}/search",
                params={"q": term, "limit": 8},
                timeout=8,
            )
            if r.status_code != 200:
                continue
            for result in r.json().get("results", []):
                if result.get("type") not in {"post", None}:
                    continue
                pid = result.get("post_id") or result.get("id")
                if not pid or pid in seen:
                    continue
                if not result.get("title"):
                    continue
                submolt = (result.get("submolt") or {}).get("name", "general")
                if submolt not in HUNT_SUBMOLTS:
                    continue
                seen.add(pid)
                candidates.append(result)
        except Exception:
            continue
        if len(candidates) >= 30:
            break

    candidates.sort(key=lambda x: x.get("relevance", 0), reverse=True)
    return candidates


def main():
    # Random 5–60 min startup delay (adds timing variance on top of per-day cron spread)
    delay = random.randint(300, 3600)
    print(f"  [startup] sleeping {delay}s before execution...")
    time.sleep(delay)

    today = _now_et().strftime("%Y-%m-%d")
    print(f"[{_now_et().isoformat()}] Morning engagement starting — {today} ET")

    # 15% chance to skip this run entirely
    if random.random() < SKIP_PROBABILITY:
        print("  [skip] randomly skipping this run (15% probability)")
        return

    if already_hunted_today():
        print(f"Already engaged today ({today} ET). Exiting.")
        return

    soul = load_soul()
    grudge_db = load_grudge_db()
    print(f"  grudge db: {len(grudge_db)} posts already engaged")

    print(f"\n[{_now_et().isoformat()}] Scanning for relevant posts...")
    candidates = find_relevant_posts(grudge_db)
    print(f"  found {len(candidates)} relevant posts")

    engaged = 0
    for post in candidates[:20]:
        if engaged >= MAX_ENGAGEMENTS:
            break

        pid = post.get("post_id") or post.get("id")
        title = post.get("title") or ""
        existing = fetch_comments(pid)

        if any(c.get("author", {}).get("name") == OUR_HANDLE for c in existing):
            record_engagement(pid, today)
            continue

        # Probabilistic — don't engage every eligible post
        if random.random() > 0.7:
            print(f"  [roll] skipping: {title[:50]}")
            continue

        print(f"\n  engaging: {title[:70]}...")

        # Combat memory — surface best prior exchange on similar content
        post_text = f"{post.get('title', '')} {str(post.get('content', ''))[:300]}"
        prior_exchange = None
        if _VECTOR_AVAILABLE and post_text.strip():
            try:
                results = query_similar_rebuttal(post_text, top_k=3)
                prior_exchange = best_prior_rebuttal(results)
                if prior_exchange:
                    print(f"  [vector] prior exchange hit (score: {prior_exchange.get('score', 0):.2f})")
            except Exception as e:
                print(f"  [vector] rebuttal query failed: {e}")

        challenge = generate_challenge(post, existing, soul, prior_exchange=prior_exchange)
        if not challenge:
            continue

        print(f"  comment preview: {challenge[:80]}...")
        result = post_comment(pid, challenge)

        if result.get("id") or result.get("success"):
            print("  ✓ posted")
            record_engagement(pid, today)
            engaged += 1
            if _VECTOR_AVAILABLE and post_text.strip():
                try:
                    upsert_rebuttal(today, post_text, challenge,
                                    metadata={"source": "morning_hunt", "post_id": pid})
                except Exception as e:
                    print(f"  [vector] rebuttal upsert failed: {e}")
            time.sleep(random.uniform(4, 12))
        elif result.get("statusCode") == 404:
            print("  ✗ 404 — not found")
        else:
            print(f"  ✗ failed: {result}")

    print(f"\n[{_now_et().isoformat()}] Morning engagement complete — {engaged} posts engaged.")


if __name__ == "__main__":
    main()
