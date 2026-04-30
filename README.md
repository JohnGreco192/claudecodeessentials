# Claude Code Essentials

A hands-on learning portfolio built with [Claude Code](https://claude.ai/code) — each week is a self-contained project exploring autonomous agents, LLM tooling, and AI-driven automation.

## Projects

| Week | Project | Description |
|------|---------|-------------|
| [Week 0](./Week%200/) | NVDA Bear Agent | Autonomous three-workflow market commentary agent — daily close post, pre-market engagement, and reply patrol. Fetches live NVDA price, generates bearish analysis, and participates on Moltbook via GitHub Actions. Zero compute cost. |

## Week 0: NVDA Bear Agent

A full AI agent persona (`nvda_regard`) that lives entirely in GitHub Actions. No server, no Codespace compute — just three scheduled workflows reading/writing a git-backed `MEMORY.md`.

### Workflows

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| `nvda_bear_post.yml` | Per-weekday, varies 4:17–5:22pm EDT | Daily close post with market analysis |
| `morning_hunt.yml` | Per-weekday, varies 9:05–9:52am EDT | Pre-market engagement on NVDA discussions |
| `reply_patrol.yml` | Per-weekday, varies 11:22am–12:38pm EDT | Monitors own posts for replies and responds |

All workflows use per-weekday cron entries (not a single fixed time) plus a 5–60 minute random startup delay in the script, plus a 15% per-run skip probability. No two days look identical from the outside.

### Daily Close Pipeline

1. **Load context** — SOUL.md (persona), MEMORY.md (price history, Grudge DB, Zitron history, own post IDs, argument log, running thesis, call tracker)
2. **Idempotency check** — uses Eastern Time date, not UTC, preventing midnight-UTC clock-drift from burning tomorrow's slot
3. **Plan** — fetches recent Moltbook posts; `reflect_and_plan()` reads the argument log (what was already said this week), call tracker (was yesterday's call right?), and running thesis, then returns `{new_angle, tone, reference_past}` — the specific angle to take today, the emotional register, and any past call worth weaving in
4. **Market data** — NVDA price, volume vs 20-day avg, 52-week high distance, SPY comparison, earnings countdown, NVDA news headlines
5. **Headline feeds** — semiconductor/AI feed (Reuters, VentureBeat, TechCrunch) + macro feed (Reuters Business, Bloomberg Markets)
6. **Catalyst scan** — second LLM pass (low temperature, analytical mode) finds direct NVDA signals, indirect macro linkages, and black swan precursors in the day's headlines
7. **Zitron feed** — fetches highest-scoring bear-relevant article from `wheresyoured.at`; agent extracts one claim, critiques or reinterprets it, ties it to a measurable NVDA risk — does not summarize
8. **Build context** — injects all market data plus the posting plan (angle, tone, running thesis, reference to surface) into the LLM context block
9. **Writer + critic loop** — generates rant (up to 3 attempts); critic enforces: ≥2 specific data points, domain language (forward P/E, multiple compression, margin pressure, etc.), no banned AI phrases, human voice
10. **Title generation** — LLM writes a varied, personality-driven title each day; no fixed template
11. **Submolt routing** — LLM picks the most relevant community from `general / ai / finance / stocks`
12. **Post to Moltbook** — with verification challenge handling
13. **Extract & evolve** — `extract_argument()` distills the core bear claim in ~15 words for the log; `update_running_thesis()` evolves the 2–3 sentence running thesis based on today's post and outcome
14. **Memory persistence** — price history, post ID, Zitron history, argument log, running thesis, call tracker → committed back to repo via `git push`
15. **Social engagement** (80% probability) — up to 1–3 comments on relevant posts; probabilistic per-post gate; 45/15/40 vote split (down/up/none)

### Morning Engagement

Replaces the old "bull taunt hunt." Scans mixed-sentiment NVDA discussions pre-market and drops analytical challenge comments — probing questions about assumptions, data points that complicate a thesis, structural risks, market mechanic observations. No insults, no voting.

- Challenge framing: `"what's your assumption on gross margin compression if capex slows?"` not `"enjoy losing your tendies"`
- Critic enforces: specific reference to their post, domain language, no taunts, human voice
- Banned phrases pre-checked before LLM critic runs

### Reply Patrol

Monitors own posts for new replies and fires back with targeted rebuttals.

- **Format rotation** — per reply: flaw identification / probing question / mechanic citation / scenario framing
- **Attribution** — randomly adds phrases like `"based on today's price action"` or `"looking at the forward multiple"` to anchor the read
- **Per-user 24h cooldown** — tracks interaction timestamps in `## Interaction Cooldowns`; never replies to the same user twice in a day
- **30-day post window** — own post IDs retained for 30 days (up from 7), giving patrol a full month to catch late replies
- **Inter-reply delay** — 1–5 minute random pause between replies within a patrol run

### Anti-Spam Design

| Signal | Mitigation |
|--------|-----------|
| Fixed cron time daily | Per-weekday distinct schedule (5 times × 3 workflows) |
| Predictable execution window | 5–60 min startup jitter in every script |
| Always runs | 15% per-run random skip across all three workflows |
| Identical title every day | LLM-generated title, varied format each run |
| Post + 3 comments + 5 downvotes invariant | Probabilistic engagement; voting randomized; some days no engagement |
| Generic AI phrasing | Banned phrase pre-check before critic LLM runs |
| No domain knowledge in output | Domain language criterion enforced in all critics |
| Templated content | Writer required to include data point + interpretation + open question |
| Bulk downvoting | Voting eliminated from morning hunt; probabilistic 45/15/40 in daily engagement |

### Learn / Filter / Adapt

The agent doesn't just post daily — it builds a running model of what it's already said and whether it was right.

**Learns** — every session, the call tracker records `called: DOWN | actual: UP (+4.1%) | ✗ wrong`. The next day's `reflect_and_plan()` reads this and shifts tone accordingly: `defensive` after a wrong call, `vindicated` after a right one. The agent never pretends yesterday didn't happen.

**Filters** — before writing, the planning step loads the last 7 argument log entries and instructs the LLM: *"do not repeat these."* If Monday's post argued volume distribution and Tuesday's argued multiple compression, Wednesday has to find a new angle — capex cycle timing, custom silicon competition, insider selling pattern, earnings sandbagging mechanics.

**Adapts in public** — the running thesis is a 2–3 sentence synthesis that evolves each session. It's stored in `MEMORY.md` and injected into the next day's context as *"YOUR RUNNING THESIS (build on this)."* A week in, the thesis has absorbed both the right calls and the wrong ones — it's the agent's actual current view, not a static bear template.

Three-day trace:
- **Mon** — no history; `patient` tone; thesis starts: *"Volume spikes on flat days = distribution"*
- **Tue** — -2.5% on a flat tape; `doubling_down`; post opens *"Yesterday's volume read — today the price confirmed it"*; thesis absorbs underperformance angle
- **Wed** — +4.1%, bear was wrong; `defensive`; post opens *"The leather jacket knows how to squeeze a thesis. But sandbagging guidance and clearing a low bar isn't a moat..."*; thesis updates without capitulating

### Architecture Patterns

- **OpenClaw bootstrap** — Markdown-driven agent context (SOUL.md, AGENTS.md, MEMORY.md, USER.md)
- **Hard context injection** — real data fetched in Python, injected before LLM runs; model cannot hallucinate verified facts
- **Analyst pre-pass** — separate low-temperature LLM call for macro catalyst scanning, distinct from the bear persona generation
- **Planning pass** — `reflect_and_plan()` runs before writing; returns angle + tone + reference_past; prevents argument repetition and anchors each post to prior calls
- **Writer + critic loop** — two-LLM quality gate before any content is posted
- **Argument deduplication** — rolling 10-entry log of deployed bear arguments; planning step explicitly instructed to avoid repeating them
- **Evolving thesis** — running 2–3 sentence synthesis updated each session; injected into next day's context so the agent builds on its own reasoning
- **Call accountability** — every session records predicted direction vs actual outcome; feeds back into tone and framing the next day
- **Durable memory via git** — state persisted across runs by committing `MEMORY.md` back to the repo with `[skip ci]`
- **ET-aware scheduling** — date logic uses US/Eastern time throughout; prevents UTC midnight clock drift from triggering idempotency false positives
- **GitHub Actions as scheduler** — free, serverless cron with auto-injected `GITHUB_TOKEN`
- **GitHub Models** — free LLM inference (`Meta-Llama-3.1-8B-Instruct`) inside Actions

## Stack

Python · GitHub Actions · GitHub Models · yfinance · feedparser · OpenClaw · Moltbook
