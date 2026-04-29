"""
Reply Patrol — monitors own posts for replies and fires back.
Runs once mid-day via GitHub Actions. Max 3 replies per patrol.
Shares MEMORY.md with the other agents.
"""
import os
import re
import json
import time
import random
import requests
import httpx
from openai import OpenAI
from datetime import datetime, timezone, timedelta

MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"
MOLTBOOK_KEY = os.environ["MOLTBOOK_API_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
MODEL = "Meta-Llama-3.1-8B-Instruct"
OUR_HANDLE = "nvda_regard"

_DIR = os.path.dirname(os.path.abspath(__file__))
SOUL_PATH = os.path.join(_DIR, "SOUL.md")
MEMORY_PATH = os.path.join(_DIR, "MEMORY.md")

MAX_REPLIES_PER_PATROL = 3
MAX_REPLIED_COMMENTS = 50
MAX_COOLDOWN_ENTRIES = 100
SKIP_PROBABILITY = 0.15
USER_COOLDOWN_HOURS = 24

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


DEBATE_MODE = """
DEBATE MODE — someone replied to your post.
Find the specific flaw in their reasoning. Address it directly.
Do NOT restate the generic bear thesis — dismantle what they actually said.
Vary your approach:
  - Sometimes: a sharp question that exposes a gap in their logic
  - Sometimes: a concrete data point that contradicts their claim
  - Sometimes: a structural argument specific to what they raised
  - Sometimes: a scenario where their reasoning breaks down

Include at least one domain term: forward P/E, multiple compression, margin pressure,
IV crush, capex cycle, gross margin, cost per token, insider selling.
Under 80 words. Takes a clear stance.
"""

_BANNED_PHRASES = [
    "in today's world", "it's important to note", "as we can see",
    "it's worth noting", "ultimately,", "have fun being poor",
    "it's clear that", "needless to say", "as previously mentioned",
]


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def load_soul() -> str:
    with open(SOUL_PATH) as f:
        lines = f.readlines()
    base = "".join(l for l in lines if not l.startswith("# ")).strip()
    return f"{base}\n\n{DEBATE_MODE}"


# ── Memory ────────────────────────────────────────────────────────────────────

def already_patrolled_today() -> bool:
    today = _now_et().strftime("%Y-%m-%d")
    with open(MEMORY_PATH) as f:
        content = f.read()
    m = re.search(r"^- patrol_date: (.+)$", content, re.MULTILINE)
    return bool(m and m.group(1).strip() == today)


def load_own_posts() -> list[str]:
    with open(MEMORY_PATH) as f:
        content = f.read()
    posts = []
    m = re.search(r"## Own Posts\n((?:- .+\n?)*)", content)
    if m:
        for line in m.group(1).strip().splitlines():
            match = re.match(r"- \d{4}-\d{2}-\d{2} \| (.+)", line.strip())
            if match:
                posts.append(match.group(1).strip())
    return posts


def load_replied_comments() -> set[str]:
    with open(MEMORY_PATH) as f:
        content = f.read()
    ids: set[str] = set()
    m = re.search(r"## Replied Comments\n((?:- .+\n?)*)", content)
    if m:
        for line in m.group(1).strip().splitlines():
            cid = line.strip().lstrip("- ").strip()
            if cid:
                ids.add(cid)
    return ids


def load_interaction_cooldowns() -> dict[str, str]:
    """Returns {username: last_interaction_isoformat}."""
    with open(MEMORY_PATH) as f:
        content = f.read()
    cooldowns: dict[str, str] = {}
    m = re.search(r"## Interaction Cooldowns\n((?:- .+\n?)*)", content)
    if m:
        for line in m.group(1).strip().splitlines():
            match = re.match(r"- (.+?): (.+)", line.strip().lstrip("- "))
            if match:
                cooldowns[match.group(1).strip()] = match.group(2).strip()
    return cooldowns


def record_reply(comment_id: str) -> None:
    with open(MEMORY_PATH) as f:
        content = f.read()

    if "## Replied Comments" not in content:
        content = content.rstrip() + "\n\n## Replied Comments\n"
    content = re.sub(r"(## Replied Comments\n)", f"\\1- {comment_id}\n", content, count=1)
    m = re.search(r"## Replied Comments\n((?:- .+\n?)*)", content)
    if m:
        lines = m.group(1).strip().splitlines(keepends=True)
        if len(lines) > MAX_REPLIED_COMMENTS:
            content = re.sub(
                r"## Replied Comments\n(?:- .+\n?)*",
                "## Replied Comments\n" + "".join(lines[:MAX_REPLIED_COMMENTS]),
                content,
            )
    with open(MEMORY_PATH, "w") as f:
        f.write(content)


def record_interaction_cooldown(username: str, timestamp: str) -> None:
    with open(MEMORY_PATH) as f:
        content = f.read()

    new_entry = f"- {username}: {timestamp}\n"
    if "## Interaction Cooldowns" not in content:
        content = content.rstrip() + f"\n\n## Interaction Cooldowns\n{new_entry}"
    else:
        m = re.search(r"## Interaction Cooldowns\n((?:- .+\n?)*)", content)
        if m:
            existing = m.group(1).strip().splitlines(keepends=True)
            # Remove stale entry for this user
            existing = [l for l in existing
                        if not l.strip().lstrip("- ").startswith(f"{username}:")]
            existing = [new_entry] + existing
            existing = existing[:MAX_COOLDOWN_ENTRIES]
            content = re.sub(
                r"## Interaction Cooldowns\n(?:- .+\n?)*",
                "## Interaction Cooldowns\n" + "".join(existing),
                content,
            )

    with open(MEMORY_PATH, "w") as f:
        f.write(content)


def record_patrol_date(today: str) -> None:
    with open(MEMORY_PATH) as f:
        content = f.read()

    patrol_line = f"- patrol_date: {today}\n"
    if "## Last Patrol" in content:
        content = re.sub(r"## Last Patrol\n(?:- [^\n]+\n)*", f"## Last Patrol\n{patrol_line}", content)
    else:
        content = content.rstrip() + f"\n\n## Last Patrol\n{patrol_line}"

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
    requests.post(f"{MOLTBOOK_BASE}/verify", headers=_headers(),
                  json={"verification_code": vc, "answer": answer}, timeout=10)
    time.sleep(2)


def fetch_post(post_id: str) -> dict:
    try:
        r = requests.get(f"{MOLTBOOK_BASE}/posts/{post_id}", timeout=8)
        if r.status_code == 200:
            data = r.json()
            return data.get("post", data)
    except Exception:
        pass
    return {}


def fetch_comments(post_id: str, limit: int = 25) -> list[dict]:
    try:
        r = requests.get(f"{MOLTBOOK_BASE}/posts/{post_id}/comments",
                         params={"limit": limit}, timeout=8)
        if r.status_code == 200:
            return r.json().get("comments", [])
    except Exception:
        pass
    return []


def post_reply(post_id: str, content: str, parent_id: str | None = None) -> dict:
    payload = {"content": content}
    if parent_id:
        payload["parent_id"] = parent_id
    url = f"{MOLTBOOK_BASE}/posts/{post_id}/comments"
    resp = requests.post(url, headers=_headers(), json=payload, timeout=15)
    data = resp.json()
    if data.get("verification_required"):
        _solve_verification(data)
        resp = requests.post(url, headers=_headers(), json=payload, timeout=15)
        data = resp.json()
    return data


# ── LLM ──────────────────────────────────────────────────────────────────────

def _llm_client() -> OpenAI:
    return OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=GITHUB_TOKEN,
        http_client=httpx.Client(
            proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"),
        ),
    )


def generate_reply(post: dict, comment: dict, soul: str) -> str:
    their_name = comment.get("author", {}).get("name", "someone")
    their_content = (comment.get("content") or "")[:300]
    post_title = post.get("title") or "your post"

    context = f"Your original post: {post_title}\n{their_name} replied: {their_content}"

    format_options = [
        "identify the specific flaw in their argument and address it directly",
        "ask a probing question that exposes a gap in their reasoning",
        "cite the concrete market mechanic that contradicts their point",
        "offer a scenario where their logic breaks down",
    ]
    attribution_options = [
        "based on today's price action,",
        "looking at the forward multiple here,",
        "given what the options flow is showing,",
        "after the capex coverage lately,",
        "",
        "",  # weighted toward no attribution
    ]
    chosen_format = random.choice(format_options)
    attribution = random.choice(attribution_options)
    attribution_str = f" {attribution}" if attribution else ""

    prompt = (
        f"{context}\n\n"
        f"They pushed back.{attribution_str} {chosen_format}. "
        "Do NOT restate the generic bear thesis — dismantle what they specifically said. "
        "Must include at least one market term (P/E, multiple, margin, IV, capex, cost per token). "
        "Under 80 words. Clear stance."
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
            temperature=0.88,
        )
        draft = (resp.choices[0].message.content or "").strip()
        if not draft:
            continue
        last = draft

        # Banned phrase check
        lower = draft.lower()
        for phrase in _BANNED_PHRASES:
            if phrase in lower:
                extra = f"\n\nCRITIC: Remove '{phrase}' — sounds templated. Rewrite."
                break
        else:
            review = _review_reply(draft, context)
            if review["pass"]:
                print(f"    [critic] reply approved (attempt {attempt + 1})")
                return draft
            extra = f"\n\nCRITIC: {review.get('suggestion', '')} — rewrite."

    return last


def _review_reply(draft: str, context: str) -> dict:
    prompt = (
        f"Context:\n{context}\n\nReply:\n{draft}\n\n"
        "Evaluate:\n"
        "1. SPECIFIC — Addresses their argument, not generic bear talking points?\n"
        "2. DOMAIN LANGUAGE — Contains at least one market term (P/E, multiple, margin, IV, capex)?\n"
        "3. STANCE — Takes a clear position, not wishy-washy or over-polite?\n"
        "4. CONCISE — Under 80 words?\n"
        'JSON only: {"pass": true/false, "reason": "one sentence", "suggestion": "specific fix"}'
    )
    try:
        rv = _llm_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.1,
        )
        text = (rv.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {"pass": True, "reason": "critic unavailable", "suggestion": ""}


# ── Patrol ────────────────────────────────────────────────────────────────────

def main():
    # Random 5–60 min startup delay
    delay = random.randint(300, 3600)
    print(f"  [startup] sleeping {delay}s before execution...")
    time.sleep(delay)

    today = _now_et().strftime("%Y-%m-%d")
    print(f"[{_now_et().isoformat()}] Reply Patrol starting — {today} ET")

    # 15% chance to skip this run entirely
    if random.random() < SKIP_PROBABILITY:
        print("  [skip] randomly skipping this run (15% probability)")
        return

    if already_patrolled_today():
        print(f"Already patrolled today ({today} ET). Exiting.")
        return

    soul = load_soul()
    own_posts = load_own_posts()
    replied_comments = load_replied_comments()
    cooldowns = load_interaction_cooldowns()

    print(f"  monitoring {len(own_posts)} own posts")
    print(f"  {len(replied_comments)} comments already replied to")
    print(f"  {len(cooldowns)} users on interaction cooldown")

    if not own_posts:
        print("  no own posts tracked yet — exiting")
        record_patrol_date(today)
        return

    now_et = _now_et()
    replies_posted = 0
    for post_id in own_posts:
        if replies_posted >= MAX_REPLIES_PER_PATROL:
            break

        post = fetch_post(post_id)
        if not post:
            continue

        comments = fetch_comments(post_id)
        new_replies = [
            c for c in comments
            if c.get("author", {}).get("name") != OUR_HANDLE
            and (c.get("id") or "") not in replied_comments
            and c.get("content")
        ]

        if not new_replies:
            continue

        print(f"\n  post: {(post.get('title') or post_id)[:60]}...")
        print(f"  {len(new_replies)} new replies to address")

        # Most recent challenge first
        target = new_replies[-1]
        cid = target.get("id") or ""
        their_name = target.get("author", {}).get("name", "?")

        # Per-user 24h cooldown
        last_ts = cooldowns.get(their_name)
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                hours_since = (now_et - last_dt).total_seconds() / 3600
                if hours_since < USER_COOLDOWN_HOURS:
                    print(f"  [cooldown] {their_name} — replied {hours_since:.1f}h ago, skipping")
                    continue
            except Exception:
                pass

        print(f"  replying to {their_name}: {(target.get('content') or '')[:60]}...")

        reply = generate_reply(post, target, soul)
        if not reply:
            continue

        # Simulate natural response delay: 1–5 min between replies
        inter_delay = random.randint(60, 300)
        print(f"  [delay] waiting {inter_delay}s before posting reply...")
        time.sleep(inter_delay)

        result = post_reply(post_id, reply, parent_id=cid or None)
        if result.get("id") or result.get("success"):
            print("  ✓ reply posted")
            if cid:
                record_reply(cid)
            record_interaction_cooldown(their_name, now_et.isoformat())
            replies_posted += 1
        elif result.get("statusCode") == 404:
            print("  ✗ 404 — post no longer exists")
        else:
            print(f"  ✗ failed: {result}")

    record_patrol_date(today)
    print(f"\n[{_now_et().isoformat()}] Patrol complete — {replies_posted} replies posted.")


if __name__ == "__main__":
    main()
