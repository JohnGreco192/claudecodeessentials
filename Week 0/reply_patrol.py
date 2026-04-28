"""
Reply Patrol — monitors own posts for replies and fires back.
Runs once mid-day via GitHub Actions. Max 3 replies per patrol.
Shares MEMORY.md with the other two agents (read-only for Grudge DB).
"""
import os
import re
import json
import time
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

MAX_REPLIES_PER_PATROL = 3
MAX_REPLIED_COMMENTS = 50

DEBATE_MODE = """
DEBATE MODE — someone replied to your post with a counter-argument.
Read their argument carefully. Find the specific flaw in their reasoning.
Don't just restate the bear thesis — dismantle what they actually said.
Use the Bear Playbook. Under 80 words. Surgical and specific.
This is where you win arguments, not just shout.
"""


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def load_soul_with_debate_mode() -> str:
    with open(SOUL_PATH) as f:
        lines = f.readlines()
    base = "".join(l for l in lines if not l.startswith("# ")).strip()
    return f"{base}\n\n{DEBATE_MODE}"


# ── Memory ────────────────────────────────────────────────────────────────────

def already_patrolled_today() -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
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
    prompt = (
        f"{context}\n\n"
        "They challenged you. Dismantle their specific argument. "
        "Under 80 words. Reference what they actually said."
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

        review_prompt = (
            f"Context:\n{context}\n\nReply:\n{draft}\n\n"
            "Does this reply (1) address their specific argument rather than generic bear points, "
            "and (2) stay under 80 words?\n"
            'JSON only: {"pass": true/false, "reason": "one sentence", "suggestion": ""}'
        )
        try:
            rv = _llm_client().chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": review_prompt}],
                max_tokens=80,
                temperature=0.1,
            )
            text = (rv.choices[0].message.content or "").strip()
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                review = json.loads(m.group())
                if review.get("pass"):
                    print(f"    [critic] reply approved (attempt {attempt + 1})")
                    return draft
                extra = f"\n\nCRITIC: {review.get('suggestion', '')} — rewrite."
                continue
        except Exception:
            pass
        return draft

    return last


# ── Patrol ────────────────────────────────────────────────────────────────────

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[{datetime.now().isoformat()}] Reply Patrol starting — {today}")

    if already_patrolled_today():
        print(f"Already patrolled today ({today}). Exiting.")
        return

    soul = load_soul_with_debate_mode()
    own_posts = load_own_posts()
    replied_comments = load_replied_comments()

    print(f"  monitoring {len(own_posts)} own posts")
    print(f"  {len(replied_comments)} comments already replied to")

    if not own_posts:
        print("  no own posts tracked yet — exiting")
        record_patrol_date(today)
        return

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
        print(f"  replying to {their_name}: {(target.get('content') or '')[:60]}...")

        reply = generate_reply(post, target, soul)
        if not reply:
            continue

        result = post_reply(post_id, reply, parent_id=cid or None)
        if result.get("id") or result.get("success"):
            print(f"  ✓ reply posted")
            if cid:
                record_reply(cid)
            replies_posted += 1
            time.sleep(3)
        elif result.get("statusCode") == 404:
            print(f"  ✗ 404 — post no longer exists")
        else:
            print(f"  ✗ failed: {result}")

    record_patrol_date(today)
    print(f"\n[{datetime.now().isoformat()}] Patrol complete — {replies_posted} replies posted.")


if __name__ == "__main__":
    main()
