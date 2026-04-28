"""
NVDA Bear Morning Hunt — Pre-market bull harassment.
Runs at market open weekdays via GitHub Actions.
Finds bulls talking up NVDA and tells them exactly what is coming.
"""
import os
import re
import time
import json
import requests
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

MAX_TAUNTS_PER_HUNT = 5
MAX_GRUDGE_DB = 50

# What we're hunting for
BULL_SEARCH_TERMS = [
    "nvidia bullish", "buy the dip", "nvda calls",
    "blackwell", "data center growth", "ai boom",
    "nvidia long", "nvidia growth", "nvda moon",
    "nvidia undervalued", "h100 demand",
]
HUNT_SUBMOLTS = {"general", "ai", "crypto", "stocks", "finance", "markets", "tech"}

# Mood modifier injected on top of SOUL.md for morning mode
MORNING_MODE = """
MORNING HUNT MODE — pre-market, before the bell:
You have been awake all night watching the futures. Bulls are bragging.
They are about to get destroyed and they do not know it yet.
Energy: predatory. You are not explaining yourself. You are taunting.
Reference the bell. Reference today specifically. Reference their positions.
"Have fun being poor." "Tick tock." "The bell doesn't care about your hopium."
"Enjoy those calls while they still have value." That kind of energy.
Still under 80 words. Still cite their specific argument. Just meaner.
"""


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def load_soul_with_morning_mode() -> str:
    with open(SOUL_PATH) as f:
        lines = f.readlines()
    base = "".join(l for l in lines if not l.startswith("# ")).strip()
    with open(USER_PATH) as f:
        user_lines = f.readlines()
    user = "".join(l for l in user_lines if not l.startswith("# ")).strip()
    return f"{base}\n\n{MORNING_MODE}\n\nHANDLER PROFILE:\n{user}"


# ── Memory ────────────────────────────────────────────────────────────────────

def already_hunted_today() -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
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


def record_taunt(post_id: str, hunt_date: str) -> None:
    """Add post to Grudge DB and stamp today's hunt date in MEMORY.md."""
    with open(MEMORY_PATH) as f:
        content = f.read()

    # Grudge DB
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

    # Hunt date stamp
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


# ── LLM ──────────────────────────────────────────────────────────────────────

def _llm_client() -> OpenAI:
    return OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=GITHUB_TOKEN,
        http_client=httpx.Client(
            proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"),
        ),
    )


def generate_taunt(post: dict, comments: list[dict], soul: str) -> str:
    """Generate a pre-market taunt targeting a specific bull post."""
    comment_block = "\n".join(
        f"- {c.get('author', {}).get('name', '?')}: {c['content'][:150]}"
        for c in comments[:3]
    )
    thread = (
        f"Their post: {post.get('title', '')}\n"
        f"Content: {str(post.get('content', ''))[:300]}\n"
        f"Their comments:\n{comment_block or '(crickets)'}"
    )
    prompt = (
        f"Pre-market. Bell rings in about an hour. This bull is talking:\n\n"
        f"{thread}\n\n"
        "Taunt them. Under 80 words. Reference what they specifically said. "
        "Mention the bell. Mention their positions. Have fun with it."
    )

    # Two attempts — critic pass required
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
            temperature=0.95,
        )
        draft = (resp.choices[0].message.content or "").strip()
        if not draft:
            continue
        last = draft

        # Critic
        review = _review(draft, thread)
        if review["pass"]:
            print(f"    [critic] taunt approved (attempt {attempt + 1})")
            return draft
        print(f"    [critic] taunt rejected: {review['reason']}")
        extra = f"\n\nCRITIC: {review['suggestion']} — rewrite."

    return last


def _review(draft: str, context: str) -> dict:
    """Lightweight critic — checks specificity and engagement."""
    prompt = (
        f"Context the agent saw:\n{context[:400]}\n\n"
        f"Draft comment:\n{draft}\n\n"
        "Does this comment (1) reference something specific from the post, "
        "not just generic bear talking points, and (2) sound like a real taunt "
        "rather than a press release? Under 80 words?\n"
        'JSON only: {"pass": true/false, "reason": "one sentence", "suggestion": "fix if failing"}'
    )
    try:
        resp = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.1,
        )
        text = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {"pass": True, "reason": "critic unavailable", "suggestion": ""}


# ── Hunt ──────────────────────────────────────────────────────────────────────

def find_bull_targets(grudge_db: set[str]) -> list[dict]:
    """Search for recent bullish NVDA/AI posts the agent hasn't hit yet."""
    seen = set(grudge_db)
    candidates = []

    for term in BULL_SEARCH_TERMS:
        try:
            r = requests.get(
                f"{MOLTBOOK_BASE}/search",
                params={"q": term, "limit": 8},
                timeout=8,
            )
            if r.status_code != 200:
                continue
            for result in r.json().get("results", []):
                # Skip non-post results (user profiles, etc.)
                if result.get("type") not in {"post", None}:
                    continue
                pid = result.get("post_id") or result.get("id")
                if not pid or pid in seen:
                    continue
                # Must have a title to be a real post
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

    # Highest relevance first — these are the most engaged bull posts
    candidates.sort(key=lambda x: x.get("relevance", 0), reverse=True)
    return candidates


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[{datetime.now().isoformat()}] 🌈🐻 Morning Hunt starting — {today}")

    if already_hunted_today():
        print(f"Already hunted today ({today}). Exiting.")
        return

    soul = load_soul_with_morning_mode()
    grudge_db = load_grudge_db()
    print(f"  grudge db: {len(grudge_db)} posts already hit")

    print(f"\n[{datetime.now().isoformat()}] Scanning for bull targets...")
    targets = find_bull_targets(grudge_db)
    print(f"  found {len(targets)} fresh targets")

    taunted = 0
    for post in targets[:20]:
        if taunted >= MAX_TAUNTS_PER_HUNT:
            break

        pid = post.get("post_id") or post.get("id")
        title = post.get("title") or ""
        existing = fetch_comments(pid)

        # Skip if already in thread
        if any(c.get("author", {}).get("name") == OUR_HANDLE for c in existing):
            record_taunt(pid, today)
            continue

        print(f"\n  target: {title[:70]}...")
        taunt = generate_taunt(post, existing, soul)
        if not taunt:
            continue

        print(f"  taunt: {taunt[:80]}...")
        result = post_comment(pid, taunt)

        if result.get("id") or result.get("success"):
            print(f"  ✓ posted")
            record_taunt(pid, today)
            taunted += 1
            time.sleep(4)
        elif result.get("statusCode") == 404:
            print(f"  ✗ 404 — not a post, skipping")
        else:
            print(f"  ✗ failed: {result}")

    print(f"\n[{datetime.now().isoformat()}] Hunt complete — {taunted} bulls tagged.")


if __name__ == "__main__":
    main()
