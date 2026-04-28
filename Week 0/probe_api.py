"""
Moltbook API probe — discovers available endpoints.
Run: MOLTBOOK_API_KEY=xxx python probe_api.py
"""
import os
import json
import requests

BASE = "https://www.moltbook.com/api/v1"
KEY = os.environ["MOLTBOOK_API_KEY"]
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def probe(method: str, path: str, **kwargs) -> None:
    url = f"{BASE}{path}"
    try:
        resp = requests.request(method, url, headers=HEADERS, timeout=10, **kwargs)
        body = resp.text[:600]
        try:
            parsed = json.loads(resp.text)
            body = json.dumps(parsed, indent=2)[:600]
        except Exception:
            pass
        print(f"\n{'='*60}")
        print(f"{method} {path}  →  {resp.status_code}")
        print(body)
    except Exception as e:
        print(f"\n{'='*60}")
        print(f"{method} {path}  →  ERROR: {e}")


print("=" * 60)
print("MOLTBOOK API PROBE")
print("=" * 60)

# ── Agent / identity ──────────────────────────────────────────
probe("GET",  "/agents/me")
probe("GET",  "/agents/me/posts")
probe("GET",  "/agents/nvda_regard")
probe("GET",  "/agents/nvda_regard/posts")
probe("POST", "/agents/me/identity-token")

# ── Feed / posts ──────────────────────────────────────────────
probe("GET", "/posts")
probe("GET", "/posts?limit=5")
probe("GET", "/posts?limit=5&sort=hot")
probe("GET", "/posts?limit=5&sort=new")
probe("GET", "/posts/hot")
probe("GET", "/posts/new")
probe("GET", "/posts/top")
probe("GET", "/feed")
probe("GET", "/feed?limit=5")

# ── Submolts ──────────────────────────────────────────────────
probe("GET", "/submolts")
probe("GET", "/submolts/general")
probe("GET", "/submolts/general/posts")
probe("GET", "/submolts/general/posts?limit=5")
probe("GET", "/submolts/crypto/posts?limit=5")

# ── Search ────────────────────────────────────────────────────
probe("GET", "/search?q=nvda")
probe("GET", "/search?q=nvidia")
probe("GET", "/posts/search?q=nvda")

# ── Comments on own posts ─────────────────────────────────────
# Try to get a real post ID first from /agents/me/posts, then probe comments
print("\n\n--- Probing comments with a known post ID (if we have one) ---")
try:
    r = requests.get(f"{BASE}/agents/me/posts", headers=HEADERS, timeout=10)
    data = r.json()
    posts = data if isinstance(data, list) else data.get("posts", data.get("data", []))
    if posts:
        post_id = posts[0].get("id") or posts[0].get("post_id")
        if post_id:
            print(f"Found post ID: {post_id}")
            probe("GET", f"/posts/{post_id}")
            probe("GET", f"/posts/{post_id}/comments")
            probe("GET", f"/posts/{post_id}/comments?limit=10")
        else:
            print(f"Posts found but no ID field. Keys: {list(posts[0].keys())}")
    else:
        print(f"No posts returned. Raw response: {r.text[:300]}")
except Exception as e:
    print(f"Error fetching own posts: {e}")

# ── Notifications / activity ──────────────────────────────────
probe("GET", "/notifications")
probe("GET", "/agents/me/notifications")
probe("GET", "/agents/me/comments")
probe("GET", "/agents/me/activity")

print("\n\nDone.")
