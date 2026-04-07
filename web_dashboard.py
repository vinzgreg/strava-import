#!/usr/bin/env python3
"""Sports Activity Dashboard — Flask web app for garmin_nostra.db visualization."""

import math
import os
import sqlite3
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_PATH = Path(os.environ.get("DB_PATH", Path.home() / "data" / "garminnostra" / "garmin_nostra.db"))

# Normalized type names used to classify metric mode
CYCLING_TYPES = {"cycling", "indoor_cycling", "mountain_biking", "gravel_cycling"}
RUNNING_TYPES = {"running"}

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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def classify_metric_mode(types: list[str]) -> str:
    """Return 'cycling', 'running', or 'mixed' based on selected normalized types."""
    type_set = {t.lower() for t in types}
    has_cycling = bool(type_set & CYCLING_TYPES)
    has_running = bool(type_set & RUNNING_TYPES)
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
            "SELECT COALESCE(n.activity_type_normalized, a.activity_type) AS norm_type, "
            "       COUNT(*) AS cnt "
            "FROM activities a "
            "LEFT JOIN activity_type_normalization n "
            "  ON a.activity_type = n.activity_type_original "
            "WHERE a.user_id = ? AND a.activity_type IS NOT NULL "
            "GROUP BY norm_type ORDER BY norm_type",
            (user_id,),
        ).fetchall()
    return jsonify([{"type": r["norm_type"], "count": r["cnt"]} for r in rows])


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
        year_clause = f"AND strftime('%Y', a.start_time_local) IN ({year_ph})"
        params.extend(years)

    norm_join = ("LEFT JOIN activity_type_normalization n "
                 "ON a.activity_type = n.activity_type_original")
    norm_filter = f"COALESCE(n.activity_type_normalized, a.activity_type) IN ({type_ph})"

    sql = f"""
        SELECT
            strftime('{fmt}', a.start_time_local)                       AS period,
            SUM(a.distance_m) / 1000.0                                  AS distance_km,
            AVG(CASE WHEN a.avg_speed_ms > 0.3 THEN a.avg_speed_ms END) AS avg_speed_ms,
            MAX(a.avg_speed_ms)                                          AS best_speed_ms,
            SUM(CASE WHEN a.elevation_gain_m > 0 THEN a.elevation_gain_m
                     ELSE 0 END)                                         AS elevation_m,
            COUNT(*)                                                     AS activity_count
        FROM activities a
        {norm_join}
        WHERE a.user_id = ?
          AND {norm_filter}
          AND a.distance_m > 0
          AND a.suppressed IS NULL
          {year_clause}
        GROUP BY period
        ORDER BY period
    """

    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
        r = db.execute(
            f"SELECT a.activity_name, a.start_time_local, a.avg_speed_ms "
            f"FROM activities a {norm_join} "
            f"WHERE a.user_id=? AND {norm_filter} "
            f"AND a.distance_m>0 AND a.avg_speed_ms>0.3 AND a.suppressed IS NULL {year_clause} "
            f"ORDER BY a.avg_speed_ms DESC LIMIT 1",
            params,
        ).fetchone()
        fastest = (
            {"name": r["activity_name"] or "—",
             "date": (r["start_time_local"] or "")[:10],
             "speed_ms": safe_float(r["avg_speed_ms"])}
            if r else None
        )
        r = db.execute(
            f"SELECT a.activity_name, a.start_time_local, a.distance_m "
            f"FROM activities a {norm_join} "
            f"WHERE a.user_id=? AND {norm_filter} "
            f"AND a.distance_m>0 AND a.suppressed IS NULL {year_clause} "
            f"ORDER BY a.distance_m DESC LIMIT 1",
            params,
        ).fetchone()
        longest = (
            {"name": r["activity_name"] or "—",
             "date": (r["start_time_local"] or "")[:10],
             "distance_km": round(float(r["distance_m"]) / 1000, 2)}
            if r else None
        )
        def _row_to_dict(row):
            d = {
                "name": row["activity_name"] or "—",
                "date": (row["start_time_local"] or "")[:10],
                "distance_km": round(float(row["distance_m"]) / 1000, 2),
                "speed_ms": safe_float(row["avg_speed_ms"]),
                "duration_s": int(row["duration_s"]) if row["duration_s"] else None,
            }
            try:
                d["elevation_m"] = int(row["elevation_gain_m"]) if row["elevation_gain_m"] else None
            except (IndexError, KeyError):
                pass
            return d
        top5_fastest = [
            _row_to_dict(r) for r in db.execute(
                f"SELECT a.activity_name, a.start_time_local, a.distance_m, a.avg_speed_ms, a.duration_s "
                f"FROM activities a {norm_join} "
                f"WHERE a.user_id=? AND {norm_filter} "
                f"AND a.distance_m>0 AND a.avg_speed_ms>0.3 AND a.suppressed IS NULL {year_clause} "
                f"ORDER BY a.avg_speed_ms DESC LIMIT 5",
                params,
            ).fetchall()
        ]
        top5_longest = [
            _row_to_dict(r) for r in db.execute(
                f"SELECT a.activity_name, a.start_time_local, a.distance_m, a.avg_speed_ms, a.duration_s "
                f"FROM activities a {norm_join} "
                f"WHERE a.user_id=? AND {norm_filter} "
                f"AND a.distance_m>0 AND a.suppressed IS NULL {year_clause} "
                f"ORDER BY a.distance_m DESC LIMIT 5",
                params,
            ).fetchall()
        ]
        top_by_distance = [
            _row_to_dict(r) for r in db.execute(
                f"SELECT a.activity_name, a.start_time_local, a.distance_m, a.avg_speed_ms, a.duration_s, a.elevation_gain_m "
                f"FROM activities a {norm_join} "
                f"WHERE a.user_id=? AND {norm_filter} "
                f"AND a.distance_m>0 AND a.suppressed IS NULL {year_clause} "
                f"ORDER BY a.distance_m DESC LIMIT 20",
                params,
            ).fetchall()
        ]
        top_by_elevation = [
            _row_to_dict(r) for r in db.execute(
                f"SELECT a.activity_name, a.start_time_local, a.distance_m, a.avg_speed_ms, a.duration_s, a.elevation_gain_m "
                f"FROM activities a {norm_join} "
                f"WHERE a.user_id=? AND {norm_filter} "
                f"AND a.elevation_gain_m>0 AND a.suppressed IS NULL {year_clause} "
                f"ORDER BY a.elevation_gain_m DESC LIMIT 20",
                params,
            ).fetchall()
        ]

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
        "records": {
            "fastest": fastest,
            "longest": longest,
            "top5_fastest": top5_fastest,
            "top5_longest": top5_longest,
            "top_by_distance": top_by_distance,
            "top_by_elevation": top_by_elevation,
        },
    })


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true"),
            host="0.0.0.0", port=5000)
