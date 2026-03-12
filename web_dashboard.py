#!/usr/bin/env python3
"""Sports Activity Dashboard — Flask web app for garmin_nostra.db visualization."""

import math
import sqlite3
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_PATH = Path.home() / "data" / "garminnostra" / "garmin_nostra.db"

# Keywords used to classify activity types into cycling vs. running
CYCLING_KEYWORDS = ["rad", "bike", "cycl", "ride", "velo", "gravel"]
RUNNING_KEYWORDS = ["run", "lauf", "jog"]

PERIOD_FORMATS = {
    "week": "%Y-W%W",
    "month": "%Y-%m",
    "year": "%Y",
}

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def classify_metric_mode(types: list[str]) -> str:
    """Return 'cycling', 'running', or 'mixed' based on selected activity types.

    Rules
    -----
    - If **all** selected types are cycling-like  → 'cycling'
      (shows velocity km/h + elevation chart)
    - If **all** selected types are running-like  → 'running'
      (shows pace min/km, no elevation chart)
    - Otherwise                                   → 'mixed'
      (shows pace min/km, no elevation chart)
    """
    has_cycling = any(any(k in t.lower() for k in CYCLING_KEYWORDS) for t in types)
    has_running = any(any(k in t.lower() for k in RUNNING_KEYWORDS) for t in types)
    if has_cycling and not has_running:
        return "cycling"
    if has_running and not has_cycling:
        return "running"
    return "mixed"


def safe_float(v) -> float | None:
    """Convert a DB value to float, returning None for NULL/NaN/Inf."""
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/users")
def api_users():
    with get_db() as db:
        rows = db.execute("SELECT id, name FROM users ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/activity_types")
def api_activity_types():
    user_id = request.args.get("user_id", type=int)
    if not user_id:
        abort(400, "user_id required")
    with get_db() as db:
        rows = db.execute(
            "SELECT DISTINCT activity_type FROM activities "
            "WHERE user_id = ? AND activity_type IS NOT NULL "
            "ORDER BY activity_type",
            (user_id,),
        ).fetchall()
    return jsonify([r["activity_type"] for r in rows])


@app.route("/api/years")
def api_years():
    user_id = request.args.get("user_id", type=int)
    if not user_id:
        abort(400, "user_id required")
    with get_db() as db:
        rows = db.execute(
            "SELECT DISTINCT strftime('%Y', start_time_local) AS yr "
            "FROM activities "
            "WHERE user_id = ? AND start_time_local IS NOT NULL "
            "ORDER BY yr DESC",
            (user_id,),
        ).fetchall()
    return jsonify([r["yr"] for r in rows if r["yr"]])


@app.route("/api/data")
def api_data():
    user_id = request.args.get("user_id", type=int)
    types_raw = request.args.get("types", "")
    group_by = request.args.get("group", "month")
    years_raw = request.args.get("years", "")

    if not user_id or not types_raw:
        abort(400, "user_id and types required")
    if group_by not in PERIOD_FORMATS:
        abort(400, "group must be: week, month, or year")

    types = [t.strip() for t in types_raw.split(",") if t.strip()]
    years = [y.strip() for y in years_raw.split(",") if y.strip()]

    fmt = PERIOD_FORMATS[group_by]
    type_ph = ",".join("?" * len(types))
    params: list = [user_id, *types]

    year_clause = ""
    if years:
        year_ph = ",".join("?" * len(years))
        year_clause = f"AND strftime('%Y', start_time_local) IN ({year_ph})"
        params.extend(years)

    sql = f"""
        SELECT
            strftime('{fmt}', start_time_local)                         AS period,
            SUM(distance_m) / 1000.0                                    AS distance_km,
            AVG(CASE WHEN avg_speed_ms > 0.3 THEN avg_speed_ms END)    AS avg_speed_ms,
            MAX(avg_speed_ms)                                            AS best_speed_ms,
            SUM(CASE WHEN elevation_gain_m > 0 THEN elevation_gain_m
                     ELSE 0 END)                                         AS elevation_m,
            COUNT(*)                                                     AS activity_count
        FROM activities
        WHERE user_id = ?
          AND activity_type IN ({type_ph})
          AND distance_m > 0
          {year_clause}
        GROUP BY period
        ORDER BY period
    """

    with get_db() as db:
        rows = db.execute(sql, params).fetchall()

    periods: list[str] = []
    distances: list[float] = []
    avg_speeds: list[float | None] = []
    best_speeds: list[float | None] = []
    elevations: list[int] = []
    counts: list[int] = []

    for r in rows:
        if not r["period"]:
            continue
        periods.append(r["period"])
        distances.append(round(float(r["distance_km"] or 0), 2))
        avg_speeds.append(safe_float(r["avg_speed_ms"]))
        best_speeds.append(safe_float(r["best_speed_ms"]))
        elevations.append(int(r["elevation_m"] or 0))
        counts.append(int(r["activity_count"] or 0))

    valid_avg = [s for s in avg_speeds if s]
    overall_avg = round(sum(valid_avg) / len(valid_avg), 4) if valid_avg else None
    overall_best = max((s for s in best_speeds if s), default=None)

    return jsonify({
        "periods": periods,
        "distance_km": distances,
        "avg_speed_ms": avg_speeds,
        "best_speed_ms": best_speeds,
        "elevation_m": elevations,
        "activity_count": counts,
        "metric_mode": classify_metric_mode(types),
        "totals": {
            "distance_km": round(sum(distances), 1),
            "activity_count": sum(counts),
            "elevation_m": int(sum(elevations)),
            "avg_speed_ms": overall_avg,
            "best_speed_ms": round(overall_best, 4) if overall_best else None,
        },
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
