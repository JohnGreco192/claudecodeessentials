"""
Macro Tourist — Economic Calendar Tool.

Hardcoded 2026 BLS/BEA/Fed release dates. Zero external dependencies.
Call get_calendar_context(today_et) to get a formatted string for LLM injection.
"""
from datetime import datetime, date, timezone, timedelta


# ── 2026 Economic Calendar ─────────────────────────────────────────────────────
# Sources: BLS release schedule, BEA release schedule, Fed FOMC calendar
# CPI: BLS releases ~3rd week of month at 8:30am ET
# NFP: BLS first Friday of month at 8:30am ET
# PCE: BEA last week of month
# GDP Advance: BEA ~last week of month following quarter end
# FOMC: 8 meetings per year — decision day (2pm ET)

ECONOMIC_CALENDAR_2026 = {
    # ── January ───────────────────────────────────────────────────────────────
    "2026-01-09": {"event": "NFP",         "label": "January Jobs Report",         "type": "labor",     "significance": "high"},
    "2026-01-14": {"event": "CPI",         "label": "December CPI",               "type": "inflation", "significance": "high"},
    "2026-01-29": {"event": "FOMC",        "label": "FOMC Rate Decision",          "type": "fed",       "significance": "critical"},
    "2026-01-29": {"event": "GDP_ADVANCE", "label": "Q4 2025 GDP (Advance)",       "type": "growth",    "significance": "high"},
    "2026-01-30": {"event": "PCE",         "label": "December PCE / Core PCE",     "type": "inflation", "significance": "high"},
    # ── February ──────────────────────────────────────────────────────────────
    "2026-02-06": {"event": "NFP",         "label": "February Jobs Report",        "type": "labor",     "significance": "high"},
    "2026-02-12": {"event": "CPI",         "label": "January CPI",                "type": "inflation", "significance": "high"},
    "2026-02-27": {"event": "PCE",         "label": "January PCE / Core PCE",      "type": "inflation", "significance": "high"},
    # ── March ─────────────────────────────────────────────────────────────────
    "2026-03-06": {"event": "NFP",         "label": "March Jobs Report",           "type": "labor",     "significance": "high"},
    "2026-03-12": {"event": "CPI",         "label": "February CPI",               "type": "inflation", "significance": "high"},
    "2026-03-19": {"event": "FOMC",        "label": "FOMC Rate Decision",          "type": "fed",       "significance": "critical"},
    "2026-03-27": {"event": "PCE",         "label": "February PCE / Core PCE",     "type": "inflation", "significance": "high"},
    # ── April ─────────────────────────────────────────────────────────────────
    "2026-04-03": {"event": "NFP",         "label": "April Jobs Report",           "type": "labor",     "significance": "high"},
    "2026-04-14": {"event": "CPI",         "label": "March CPI",                  "type": "inflation", "significance": "high"},
    "2026-04-29": {"event": "GDP_ADVANCE", "label": "Q1 2026 GDP (Advance)",       "type": "growth",    "significance": "high"},
    "2026-04-30": {"event": "PCE",         "label": "March PCE / Core PCE",        "type": "inflation", "significance": "high"},
    # ── May ───────────────────────────────────────────────────────────────────
    "2026-05-01": {"event": "NFP",         "label": "May Jobs Report",             "type": "labor",     "significance": "high"},
    "2026-05-07": {"event": "FOMC",        "label": "FOMC Rate Decision",          "type": "fed",       "significance": "critical"},
    "2026-05-13": {"event": "CPI",         "label": "April CPI",                  "type": "inflation", "significance": "high"},
    "2026-05-29": {"event": "PCE",         "label": "April PCE / Core PCE",        "type": "inflation", "significance": "high"},
    # ── June ──────────────────────────────────────────────────────────────────
    "2026-06-05": {"event": "NFP",         "label": "June Jobs Report",            "type": "labor",     "significance": "high"},
    "2026-06-11": {"event": "CPI",         "label": "May CPI",                    "type": "inflation", "significance": "high"},
    "2026-06-18": {"event": "FOMC",        "label": "FOMC Rate Decision",          "type": "fed",       "significance": "critical"},
    "2026-06-26": {"event": "PCE",         "label": "May PCE / Core PCE",          "type": "inflation", "significance": "high"},
    # ── July ──────────────────────────────────────────────────────────────────
    "2026-07-02": {"event": "NFP",         "label": "July Jobs Report",            "type": "labor",     "significance": "high"},
    "2026-07-15": {"event": "CPI",         "label": "June CPI",                   "type": "inflation", "significance": "high"},
    "2026-07-30": {"event": "FOMC",        "label": "FOMC Rate Decision",          "type": "fed",       "significance": "critical"},
    "2026-07-30": {"event": "GDP_ADVANCE", "label": "Q2 2026 GDP (Advance)",       "type": "growth",    "significance": "high"},
    "2026-07-31": {"event": "PCE",         "label": "June PCE / Core PCE",         "type": "inflation", "significance": "high"},
    # ── August ────────────────────────────────────────────────────────────────
    "2026-08-07": {"event": "NFP",         "label": "August Jobs Report",          "type": "labor",     "significance": "high"},
    "2026-08-13": {"event": "CPI",         "label": "July CPI",                   "type": "inflation", "significance": "high"},
    "2026-08-28": {"event": "PCE",         "label": "July PCE / Core PCE",         "type": "inflation", "significance": "high"},
    # ── September ─────────────────────────────────────────────────────────────
    "2026-09-04": {"event": "NFP",         "label": "September Jobs Report",       "type": "labor",     "significance": "high"},
    "2026-09-10": {"event": "CPI",         "label": "August CPI",                 "type": "inflation", "significance": "high"},
    "2026-09-17": {"event": "FOMC",        "label": "FOMC Rate Decision",          "type": "fed",       "significance": "critical"},
    "2026-09-25": {"event": "PCE",         "label": "August PCE / Core PCE",       "type": "inflation", "significance": "high"},
    # ── October ───────────────────────────────────────────────────────────────
    "2026-10-02": {"event": "NFP",         "label": "October Jobs Report",         "type": "labor",     "significance": "high"},
    "2026-10-14": {"event": "CPI",         "label": "September CPI",              "type": "inflation", "significance": "high"},
    "2026-10-29": {"event": "GDP_ADVANCE", "label": "Q3 2026 GDP (Advance)",       "type": "growth",    "significance": "high"},
    "2026-10-30": {"event": "PCE",         "label": "September PCE / Core PCE",    "type": "inflation", "significance": "high"},
    # ── November ──────────────────────────────────────────────────────────────
    "2026-11-05": {"event": "FOMC",        "label": "FOMC Rate Decision",          "type": "fed",       "significance": "critical"},
    "2026-11-06": {"event": "NFP",         "label": "November Jobs Report",        "type": "labor",     "significance": "high"},
    "2026-11-12": {"event": "CPI",         "label": "October CPI",                "type": "inflation", "significance": "high"},
    "2026-11-25": {"event": "PCE",         "label": "October PCE / Core PCE",      "type": "inflation", "significance": "high"},
    # ── December ──────────────────────────────────────────────────────────────
    "2026-12-04": {"event": "NFP",         "label": "December Jobs Report",        "type": "labor",     "significance": "high"},
    "2026-12-10": {"event": "CPI",         "label": "November CPI",               "type": "inflation", "significance": "high"},
    "2026-12-17": {"event": "FOMC",        "label": "FOMC Rate Decision",          "type": "fed",       "significance": "critical"},
    "2026-12-22": {"event": "GDP_ADVANCE", "label": "Q3 2026 GDP (Third Estimate)", "type": "growth",   "significance": "high"},
    "2026-12-23": {"event": "PCE",         "label": "November PCE / Core PCE",     "type": "inflation", "significance": "high"},
}

# Human-readable descriptions for each event type
_EVENT_DESCRIPTIONS = {
    "FOMC":        "Fed rate decision (2pm ET) — market-moving",
    "CPI":         "Inflation data (8:30am ET) — regime-defining",
    "NFP":         "Jobs report (8:30am ET) — rate path signal",
    "PCE":         "Fed's preferred inflation gauge (8:30am ET)",
    "GDP_ADVANCE": "Advance GDP estimate — growth reality check",
}

# NVDA-specific framing for each event type
_NVDA_FRAMING = {
    "FOMC":        "Rate decisions move growth/tech multiples. Higher-for-longer = multiple compression headwind for NVDA.",
    "CPI":         "Hot CPI = Fed on hold = risk-off rotation = tech/growth selloff terrain.",
    "NFP":         "Strong jobs = Fed holds = rate headwind stays. Weak jobs = recession fear = risk-off either way.",
    "PCE":         "Core PCE is the Fed's real inflation read. Above 2.5% = no cuts coming.",
    "GDP_ADVANCE": "Weak GDP = demand destruction fears hit capex plans. AI spending is discretionary capex.",
}


def is_event_day(today_et: str) -> dict | None:
    """Returns event metadata if today is a high-significance event day, else None."""
    return ECONOMIC_CALENDAR_2026.get(today_et)


def get_upcoming_events(today_et: str, n: int = 5) -> list[tuple[str, dict]]:
    """Returns the next n events after today, sorted by date."""
    today = datetime.strptime(today_et, "%Y-%m-%d").date()
    upcoming = [
        (date_str, meta)
        for date_str, meta in sorted(ECONOMIC_CALENDAR_2026.items())
        if datetime.strptime(date_str, "%Y-%m-%d").date() > today
    ]
    return upcoming[:n]


def get_calendar_context(today_et: str) -> str:
    """
    Returns a formatted string for LLM context injection.

    Format:
      TODAY: FOMC Rate Decision — Fed rate decision (2pm ET) — market-moving [CRITICAL]
        → Rate decisions move growth/tech multiples. Higher-for-longer = multiple compression...
      UPCOMING: May 13 CPI | May 29 PCE | Jun 5 NFP | Jun 18 FOMC | Jun 26 PCE
    """
    lines = []

    today_event = is_event_day(today_et)
    if today_event:
        sig = today_event["significance"].upper()
        desc = _EVENT_DESCRIPTIONS.get(today_event["event"], today_event["event"])
        nvda = _NVDA_FRAMING.get(today_event["event"], "")
        lines.append(f"TODAY: {today_event['label']} — {desc} [{sig}]")
        if nvda:
            lines.append(f"  → {nvda}")

    upcoming = get_upcoming_events(today_et, n=5)
    if upcoming:
        parts = []
        for date_str, meta in upcoming:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            parts.append(f"{dt.strftime('%b %-d')} {meta['event']}")
        lines.append("UPCOMING: " + " | ".join(parts))

    return "\n".join(lines) if lines else ""


if __name__ == "__main__":
    from datetime import datetime, timezone, timedelta

    def _now_et() -> str:
        now_utc = datetime.now(timezone.utc)
        year = now_utc.year
        mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
        dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7, hours=7)
        nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
        dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7, hours=6)
        offset = timedelta(hours=-4) if dst_start <= now_utc < dst_end else timedelta(hours=-5)
        return now_utc.astimezone(timezone(offset)).strftime("%Y-%m-%d")

    today = _now_et()
    print(f"Today (ET): {today}\n")
    ctx = get_calendar_context(today)
    print(ctx if ctx else "(no events today or upcoming in calendar)")
    print("\n--- is_event_day ---")
    print(is_event_day(today))
    print("\n--- upcoming (5) ---")
    for d, m in get_upcoming_events(today):
        print(f"  {d}: {m['label']} [{m['significance']}]")
