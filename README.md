# Claude Code Essentials

A hands-on learning portfolio built with [Claude Code](https://claude.ai/code) — each week is a self-contained project exploring autonomous agents, LLM tooling, and AI-driven automation.

## Projects

| Week | Project | Description |
|------|---------|-------------|
| [Week 0](./Week%200/) | NVDA Bear Agent | Autonomous daily market commentary bot — fetches live NVDA price, generates bearish analysis, posts to Moltbook via GitHub Actions. Zero compute cost. |

## Architecture Patterns Explored

- **OpenClaw bootstrap** — Markdown-driven agent context (SOUL.md, AGENTS.md, MEMORY.md, USER.md)
- **Hard context injection** — real data fetched in Python and injected before the LLM runs; model cannot hallucinate verified facts
- **Durable memory via git** — state persisted across runs by committing back to the repo
- **GitHub Actions as scheduler** — free, serverless cron with auto-injected `GITHUB_TOKEN`
- **GitHub Models** — free LLM inference (`Meta-Llama-3.1-8B-Instruct`) inside Codespaces

## Stack

Python · GitHub Actions · GitHub Models · yfinance · OpenClaw · Moltbook
