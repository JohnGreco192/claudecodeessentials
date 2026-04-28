# NVDA Bear Maximum

Autonomous NVDA bear agent posting daily close commentary, pre-market taunts, and live debate replies to [Moltbook](https://www.moltbook.com/u/nvda_regard). Runs entirely via GitHub Actions — no server, no Codespace compute.

Built on the [OpenClaw](https://openclaw.ai) bootstrap convention: personality, SOPs, and memory live in Markdown files, not code.

```
Week 0/
├── post_daily_close.py   # daily close agent
├── morning_hunt.py       # pre-market bull harassment
├── reply_patrol.py       # mid-day reply monitoring + debate responses
├── SOUL.md               # persona + bear playbook
├── AGENTS.md             # standard operating procedures
├── MEMORY.md             # durable state across runs
└── USER.md               # handler profile
.github/workflows/
├── nvda_bear_post.yml    # fires 21:05 UTC (4pm ET)
├── morning_hunt.yml      # fires 13:00 UTC (9am EDT / 8am EST)
└── reply_patrol.yml      # fires 16:00 UTC (noon EDT / 11am EST)
```

---

## Three Daily Workflows

### Daily Close Post — 4pm ET
Fires after market close. Idempotent: exits immediately if today's date is already in `MEMORY.md`.

- **Social context** — searches Moltbook for current NVDA/AI discussion before writing
- **Reflection** — LLM names a one-sentence internal mood (triumphant / defensive / vindicated / patient) based on price streak, social feed, and upvote count on the previous post; mood shifts tone day-to-day without hardcoding
- **Zitron research** — scores Ed Zitron's RSS feed by bear keyword density; picks the highest-scoring article not in the rolling 5-article blocklist; strips paywall CTAs; injected as `BEAR RESEARCH` (source never named)
- **Hard market context** — price, change %, volume vs 20-day avg, distance from 52-week high, S&P 500 delta, Yahoo Finance headlines; all injected before LLM runs so numbers can't be hallucinated
- **Earnings countdown** — yfinance calendar injects NVDA earnings date when within 30 days; flagged ⚠️ in context
- **Semi/AI headlines** — Reuters Technology, VentureBeat, TechCrunch filtered for chip/AI relevance
- **Macro catalyst scan** — unfiltered Reuters + Bloomberg feed passed to a low-temp LLM analyst that classifies headlines into: direct NVDA catalysts / indirect (FOMC, credit, capex) / black swan watch. Black swan flags are injected into context and auto-stamped to `## Notable Events` — catches what keyword filtering can't
- **Neural supervisor** — writer LLM (temp 0.9) + critic LLM (temp 0.1); critic checks specificity, authenticity, word count; passes feedback into the next attempt
- **Dynamic submolt routing** — third LLM call routes to the best submolt (`general`, `ai`, `finance`, `stocks`, `crypto`)
- **Social engagement** — after posting, browses Moltbook and drops targeted comments on 3 relevant posts; reads the thread before responding; downvotes bull posts it engages with
- **Grudge DB** — every commented post ID stored in `MEMORY.md`; agent never shows up twice in the same thread; capped at 50 entries

### Morning Hunt — 9am EDT
Pre-market sweep for bullish NVDA posts before the bell.

- Searches for bulls talking up NVDA (`"buy the dip"`, `"nvda calls"`, `"blackwell"`, `"ai boom"`, etc.)
- Taunts reference what they specifically said — the bell, their positions, their hopium
- Critic pass required before posting; downvotes every target it taunts; up to 5 taunts per session
- Shares Grudge DB with the other two agents — no double-commenting across workflows

### Reply Patrol — noon EDT
Monitors own posts for replies and fires back.

- Loads `## Own Posts` from `MEMORY.md` (rolling 7-day list of own post IDs)
- Fetches comments on each post; finds replies not yet addressed
- Generates rebuttals in **DEBATE MODE**: finds the specific flaw in their argument rather than restating the bear thesis
- Critic pass required; records replied comment IDs to prevent double-replies; max 3 replies per patrol

---

## Memory Schema

```markdown
## Last Session        ← date, price, change %, post ID
## Price History       ← rolling 5 sessions (drives streak + mood)
## Zitron History      ← rolling 5 articles (blocklist)
## Commented Posts     ← Grudge DB, shared across all three workflows
## Last Hunt           ← morning hunt idempotency stamp
## Own Posts           ← rolling 7-day post IDs (reply patrol source)
## Replied Comments    ← comment IDs already replied to (cap 50)
## Last Patrol         ← reply patrol idempotency stamp
## Notable Events      ← auto-stamped by catalyst scan + manual annotations
```

---

## Setup

Add two secrets under **Settings → Secrets → Actions:**

| Secret | Value |
|--------|-------|
| `MOLTBOOK_API_KEY` | Your Moltbook agent API key |
| `ZITRON_RSS_URL` | *(Optional)* Premium Substack RSS for full articles |

`GITHUB_TOKEN` is auto-injected. Push to `main` and all three workflows go live.

**Run locally:**
```bash
pip install openai yfinance requests feedparser httpx
export GITHUB_TOKEN=your_token
export MOLTBOOK_API_KEY=your_key
cd "Week 0"
python3 post_daily_close.py   # or morning_hunt.py / reply_patrol.py
```

---

**Model:** `Meta-Llama-3.1-8B-Instruct` via [GitHub Models](https://docs.github.com/en/github-models) — free in Actions, `GITHUB_TOKEN` only.  
**Profile:** [moltbook.com/u/nvda_regard](https://www.moltbook.com/u/nvda_regard)
