# CLAUDE.md — Sport Dashboard Briefing

This document captures the full product specification agreed between the user (vinz) and Claude Code.
It is the authoritative source for decisions made during the initial build.

---

## Project Context

The project is a multi-user sports activity tracker backed by a SQLite database at
`~/data/garminnostra/garmin_nostra.db` (schema in `db-scheme.md`).
Activities are imported from Strava exports via `strava_import.py`.

The dashboard (`web_dashboard.py` + `templates/dashboard.html`) is a new addition that
visualises long-term training trends, inspired by apps like Strava and Garmin Connect.

---

## Feature Specification

### Filters (sticky filter bar)

| Filter | Widget | Default |
|---|---|---|
| User | Single-select dropdown | User `id=1` (vinz) |
| Activity Types | Multi-select (Tom Select) | All types containing "run" or "lauf" (case-insensitive) |
| Group by | Single-select | Month |
| Years | Multi-select (Tom Select) | All available years |

Activity types in the DB are **not normalised** (German + English mix, e.g. `Lauf`, `running`,
`Radfahrt`, `cycling`). The multi-select lets the user combine them freely.

---

### Metric Mode (auto-detected from selected types)

The mode is computed server-side in `classify_metric_mode()` and drives how metrics are displayed.

| Mode | Condition | Pace/Speed metric | Elevation chart |
|---|---|---|---|
| `running` | Only running-like types selected (`run`, `lauf`, `jog`) | Pace in **min:sec /km** | Hidden |
| `cycling` | Only cycling-like types selected (`rad`, `bike`, `cycl`, `ride`, `velo`, `gravel`) | Average speed in **km/h** | Shown |
| `mixed` | Mix of both (or neither) | Pace in **min:sec /km** | Hidden |

**Cycling keywords:** rad, bike, cycl, ride, velo, gravel
**Running keywords:** run, lauf, jog

---

### Charts

#### 1. Distance per Period (always shown)
- **Type:** Bar (distance) + Line (activity count, secondary y-axis)
- **Y-left:** km
- **Y-right:** number of activities
- Colour: accent colour for the current metric mode

#### 2. Pace / Speed per Period (always shown)
- **Type:** Line (two datasets + 1 trend)
- **Dataset 1:** Best activity pace/speed — `MAX(avg_speed_ms)` per period
- **Dataset 2:** Average pace/speed — `AVG(avg_speed_ms)` per period (excluding nulls/zeros)
- **Dataset 3:** 3-period simple moving average of dataset 2 (dashed trend line)
- **Running y-axis:** tick labels formatted as `MM:SS /km`; higher on axis = faster
- **Cycling y-axis:** tick labels in `km/h`

#### 3. Elevation per Period (cycling only)
- **Type:** Bar
- **Y:** metres of elevation gain (`SUM(elevation_gain_m)`)
- Only shown when metric mode is `cycling` and at least one period has elevation > 0

---

### Summary Stat Cards (4 cards)

| Card | Running | Cycling |
|---|---|---|
| Total Distance | km | km |
| Activities | count | count |
| Avg Pace / Speed | min:sec /km | km/h |
| Best Pace / Speed | min:sec /km (fastest activity avg) | km/h (fastest ride avg) |

"Best pace" = `MAX(avg_speed_ms)` over the whole filtered dataset = fastest single activity.

---

### Social / Mastodon Export

Accessible via the **Export** button (top-right of navbar), opening a modal with two tabs.

#### Tab 1 — Text (Mastodon)
Plain-text block ready to paste into Mastodon.
Includes: sport emoji + type, @user, period + year range, total distance, activity count,
avg pace/speed, best pace/speed, elevation (cycling), top 5 periods by distance, hashtags.

#### Tab 2 — Image Card (1080×1080 PNG)
Canvas-rendered social card, downloadable as PNG. Layout:
- Accent colour strip at top (colour reflects metric mode)
- Sport emoji + type name
- @username, group-by label, year range
- 2×2 stat grid (distance, count, avg, best)
- Sparkline bar chart (last ≤24 periods, by distance)
- Hashtag footer

---

## Colour Scheme

| Mode | Accent | Emoji |
|---|---|---|
| running | `#FF6B35` (orange) | 🏃 |
| cycling | `#00B4D8` (teal/blue) | 🚴 |
| mixed | `#A855F7` (purple) | ⚡ |

Global UI: dark theme (`#0f1117` body, `#1a1d27` cards, `#2d3148` borders).

---

## SQL Aggregation (per period)

```sql
SELECT
    strftime('<fmt>', start_time_local)                         AS period,
    SUM(distance_m) / 1000.0                                    AS distance_km,
    AVG(CASE WHEN avg_speed_ms > 0.3 THEN avg_speed_ms END)    AS avg_speed_ms,
    MAX(avg_speed_ms)                                            AS best_speed_ms,
    SUM(CASE WHEN elevation_gain_m > 0 THEN elevation_gain_m
             ELSE 0 END)                                         AS elevation_m,
    COUNT(*)                                                     AS activity_count
FROM activities
WHERE user_id = ?
  AND activity_type IN (...)
  AND distance_m > 0
  [AND strftime('%Y', start_time_local) IN (...)]
GROUP BY period
ORDER BY period
```

`avg_speed_ms` rows with values ≤ 0.3 m/s are excluded from the average (noise / GPS glitches).

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend | Flask (Python) | Matches existing Python project, minimal |
| DB | SQLite (direct) | No ORM overhead needed |
| Charts | Chart.js 4 (CDN) | No build step, excellent docs |
| UI framework | Bootstrap 5 dark (CDN) | Responsive, dark theme built-in |
| Multi-select | Tom Select 2 (CDN) | Lightweight, Bootstrap 5 compatible |
| Export image | HTML5 Canvas (built-in) | No external dependency |

---

## Running the Server

```bash
pip install flask
python3 web_dashboard.py
# → http://localhost:5000
```

---

## Future Ideas (not yet built)

- Heart rate trends (avg HR per period)
- Training load / TSS chart
- Map view of start locations
- CSV/JSON data export
- Persistent filter state in URL hash
- Mobile-optimised swipe between charts
