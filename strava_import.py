#!/usr/bin/env python3
"""
strava_import.py — Import a Strava data export into the garmin-sync SQLite database.

Reads activities.csv from a Strava export folder, maps fields to the
garmin-sync DB schema, copies GPX files to a destination directory, and
inserts rows into the activities table.
"""

import argparse
import csv
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    _BERLIN_TZ = _ZoneInfo("Europe/Berlin")
except Exception:
    _BERLIN_TZ = None


# ---------------------------------------------------------------------------
# Column indices in activities.csv (0-based, duplicate column names resolved
# by position rather than header name).
# ---------------------------------------------------------------------------
COL = {
    "activity_id":  0,   # Aktivitäts-ID
    "date_local":   1,   # Aktivitätsdatum  (DD.MM.YYYY, HH:MM:SS, local TZ)
    "name":         2,   # Name der Aktivität
    "type":         3,   # Aktivitätsart
    "description":  4,   # Aktivitätsbeschreibung
    # col 5:  Verstrichene Zeit (human-readable summary, skip)
    # col 6:  Distanz (km, comma decimal — skip, use col 17)
    # col 7:  Max. Herzfrequenz (summary, skip)
    "filename":     12,  # Dateiname  → relative path to GPX
    # col 13: Sportlergewicht
    # col 14: Fahrradgewicht
    "elapsed_s":    15,  # Verstrichene Zeit (seconds, 2nd occurrence)
    "moving_s":     16,  # Bewegungszeit (seconds)
    "dist_m":       17,  # Distanz (meters, 2nd occurrence)
    "max_speed":    18,  # Höchstgeschw. (m/s)
    "avg_speed":    19,  # Durchschnittliche Geschwindigkeit (m/s)
    "elev_gain":    20,  # Höhenzunahme (m)
    "elev_loss":    21,  # Höhenunterschied (m)
    "min_ele":      22,  # Min. Höhe (m)
    "max_ele":      23,  # Max. Höhe (m)
    "max_cad":      28,  # Max. Tritt-/Schrittfrequenz
    "avg_cad":      29,  # Durchschnittliche Trittfrequenz
    "max_hr":       30,  # Max. Herzfrequenz (2nd occurrence, detailed)
    "avg_hr":       31,  # Durchschnittliche Herzfrequenz
    "max_pwr":      32,  # Max. Watt
    "avg_pwr":      33,  # Durchschnittliche Watt
    "calories":     34,  # Kalorien
    "max_temp":     35,  # Max. Temperatur (°C)
    "avg_temp":     36,  # Durchschnittliche Temperatur (°C)
    "start_utc":    45,  # Startzeit (UTC ISO 8601)
    "norm_pwr":     46,  # Gewichtete durchschnittliche Leistung (W)
    "intensity_f":  47,  # Leistungszahl (intensity factor)
    "steps":        85,  # Schritte insgesamt
    "tss":          88,  # Trainingsbelastung (TSS)
}

GPX_NS = "http://www.topografix.com/GPX/1/1"

# ---------------------------------------------------------------------------
# Runmeter CSV column indices (0-based, semicolon-separated)
# ---------------------------------------------------------------------------
RUNMETER_COL = {
    "date":          0,   # Tag (YYYY-MM-DD)
    "type":          1,   # Aktivität (e.g. Lauf, Fahrrad)
    "count":         2,   # Anzahl (1 = single activity; >1 = aggregated)
    "dist_km":       3,   # Wegstrecke (km)  — decimal comma
    # col 4: Laufzeit (H:MM:SS) — redundant, use col 5
    "duration_s":    5,   # Laufzeit (Sek)
    "elev_gain":     6,   # Aufstieg (Meter)
    "elev_loss":     7,   # Abstieg (Meter)
    "calories":      8,   # Kalorien
    # cols 9-14: per-activity averages — skip
    "avg_speed_kmh": 15,  # Durchschnittsgeschwindigkeit (km/h) — decimal comma
    # col 16: Durchschnittstempo (M:SS) — redundant
    # col 17: Durchschnittstempo (Sek) — redundant
    "max_speed_kmh": 18,  # Schnellste Geschwindigkeit (km/h) — decimal comma
    # col 19: Schnellstes Tempo (M:SS) — redundant
    # col 20: Schnellstes Tempo (Sek) — redundant
    "steps":         21,  # Schritte
    "max_step_cad":  22,  # Schrittfrequenz Maximum (running, steps/min)
    "avg_step_cad":  23,  # Schrittfrequenz Durchschnitt (running)
    "max_hr":        24,  # Pulsfrequenz Maximum (bpm)
    "avg_hr":        25,  # Pulsfrequenz Durchschnitt (bpm)
    "max_ped_cad":   26,  # Trittfrequenz Maximum (cycling, rpm)
    "avg_ped_cad":   27,  # Trittfrequenz Durchschnitt (cycling)
    "max_power":     28,  # Leistung Maximum (Watt)
    "avg_power":     29,  # Leistung Durchschnitt (Watt)
    "norm_power":    30,  # Normierte Leistung (Watt)
}

_RUNMETER_CYCLING_KEYWORDS = {"fahrrad", "rad", "bike", "cycl", "ride", "velo", "gravel"}

# ---------------------------------------------------------------------------
# Cyclemeter CSV column indices (0-based, semicolon-separated)
# Per-activity records with full datetime; Route name in col 0.
# ---------------------------------------------------------------------------
CYCLEMETER_COL = {
    "route":         0,   # Route name → activity_name
    "type":          1,   # Aktivität (e.g. Fahrrad)
    "startzeit":     2,   # Startzeit (YYYY-MM-DD HH:MM:SS)
    # col 3: Zeit (H:MM:SS) — redundant, use col 4
    "moving_s":      4,   # Zeit (Sek) — moving time
    # col 5: Pausenzeit (H:MM:SS) — redundant, use col 6
    "pause_s":       6,   # Pausenzeit (Sek) — pause time; elapsed = moving + pause
    "dist_km":       7,   # Wegstrecke (km) — decimal comma
    "avg_speed_kmh": 8,   # Durchschnittsgeschwindigkeit (km/h) — decimal comma
    # col 9/10: Durchschnittstempo — redundant
    "elev_gain":     11,  # Aufstieg (Meter)
    "elev_loss":     12,  # Abstieg (Meter)
    "calories":      13,  # Kalorien
    "max_speed_kmh": 14,  # Schnellste Geschwindigkeit (km/h) — decimal comma
    # cols 15/16: Schnellstes Tempo — redundant
    # cols 17-19: Schritte / Schrittfrequenz — always 0 in cycling export
    "max_hr":        20,  # Pulsfrequenz Maximum (bpm)
    "avg_hr":        21,  # Pulsfrequenz Durchschnitt (bpm)
    "max_cad":       22,  # Trittfrequenz Maximum (rpm)
    "avg_cad":       23,  # Trittfrequenz Durchschnitt (rpm)
    "max_power":     24,  # Leistung Maximum (Watt)
    "avg_power":     25,  # Leistung Durchschnitt (Watt)
    "norm_power":    26,  # Normierte Leistung (Watt)
    # col 27: Variabilitätsindex — skip
    "intensity_f":   28,  # Intensitätsfaktor
    "tss":           29,  # Wertung der Trainingsbelastung (TSS)
    # cols 30-35: peak power intervals — skip
    "bike":          36,  # Fahrrad (bike name) → raw_json
    # col 37: Schuhe — skip
    "notes":         38,  # Notizen → raw_json
}

# DailyMile JSON activity_type.name → DB activity_type
_DAILYMILE_TYPE_MAP = {
    "Running": "Lauf",
    "Cycling": "Fahrrad",
    "Walking": "Walk",
    "Fitness": "Fitness",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_ts(ts: str | None) -> str | None:
    """Normalise a timestamp to 'YYYY-MM-DDTHH:MM:SS' for comparison.

    Handles both ISO 8601 ('T') and SQLite space-separated formats, and
    strips any trailing timezone suffix so we compare bare wall-clock time.
    """
    if not ts:
        return None
    s = ts.strip().replace(" ", "T")
    # drop trailing Z or +00:00 / -HH:MM style offsets
    for sep in ("Z", "+", "-"):
        idx = s.find(sep, 10)  # skip the date part
        if idx != -1:
            s = s[:idx]
    return s


def _parse_ts(ts: str | None) -> datetime | None:
    """Parse a timestamp string to a naive datetime for time-delta arithmetic."""
    if not ts:
        return None
    s = ts.strip().replace(" ", "T").rstrip("Z")
    for sep in ("+", "-"):
        idx = s.find(sep, 10)
        if idx != -1:
            s = s[:idx]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _fuzzy_time_match(
    existing_list: list[dict],
    claimed_ids: set[int],
    strava_local: str | None,
    strava_utc: str | None,
    strava_name: str,
    strava_duration_s: float | None,
    window_h: float = 2.0,
) -> dict | None:
    """Return the best-matching existing activity using a fuzzy time window.

    Acceptance criteria (to avoid false positives):
      - time delta ≤ 1 h  →  accepted on time alone (covers DST ± UTC±1)
      - time delta ≤ 2 h  →  accepted only when duration OR name also match

    Among accepted candidates the one with the highest score is returned.
    Already-claimed entries (fuzzy-matched earlier in this run) are skipped.
    """
    strava_dts = [dt for dt in (_parse_ts(strava_local), _parse_ts(strava_utc)) if dt is not None]
    if not strava_dts:
        return None

    best: dict | None = None
    best_score = -1.0

    s_words = {w.lower() for w in strava_name.split() if len(w) >= 4}

    for ex in existing_list:
        if ex["id"] in claimed_ids:
            continue

        ex_dts = [
            dt for dt in (_parse_ts(ex["start_time_local"]), _parse_ts(ex["start_time_utc"]))
            if dt is not None
        ]

        # Minimum time delta across all (strava_ts, existing_ts) pairs
        min_delta_h: float | None = None
        for s_dt in strava_dts:
            for e_dt in ex_dts:
                dh = abs((s_dt - e_dt).total_seconds()) / 3600.0
                if min_delta_h is None or dh < min_delta_h:
                    min_delta_h = dh

        if min_delta_h is None or min_delta_h > window_h:
            continue

        # Duration corroboration
        ex_dur = ex.get("elapsed_s")
        duration_match = False
        if strava_duration_s and ex_dur:
            rel = abs(strava_duration_s - ex_dur) / max(strava_duration_s, ex_dur)
            duration_match = rel < 0.10  # within 10 %

        # Name corroboration (shared words of ≥ 4 chars)
        e_words = {w.lower() for w in (ex.get("activity_name") or "").split() if len(w) >= 4}
        name_match = bool(s_words & e_words)

        # Acceptance gate
        if min_delta_h > 1.0 and not duration_match and not name_match:
            continue

        score = (2.0 - min_delta_h) + (3.0 if duration_match else 0.0) + (2.0 if name_match else 0.0)
        if score > best_score:
            best_score = score
            best = ex

    return best


def _find_missing_file_candidates(
    existing_list: list[dict],
    exclude_ids: set[int],
    strava_local: str | None,
    strava_utc: str | None,
    strava_name: str,
    strava_duration_s: float | None,
    is_fit: bool,
    window_h: float = 2.0,
) -> list[dict]:
    """Return all existing entries within window_h that are missing the relevant file path.

    Uses the same ±2 h acceptance criteria as _fuzzy_time_match.
    Entries in exclude_ids (already matched / already backfilled) are skipped.
    """
    strava_dts = [dt for dt in (_parse_ts(strava_local), _parse_ts(strava_utc)) if dt is not None]
    if not strava_dts:
        return []

    file_key = "fit_path" if is_fit else "gpx_path"
    s_words = {w.lower() for w in strava_name.split() if len(w) >= 4}
    results = []

    for ex in existing_list:
        if ex["id"] in exclude_ids:
            continue
        if ex.get(file_key):
            continue  # already has the file

        ex_dts = [
            dt for dt in (_parse_ts(ex["start_time_local"]), _parse_ts(ex["start_time_utc"]))
            if dt is not None
        ]

        min_delta_h: float | None = None
        for s_dt in strava_dts:
            for e_dt in ex_dts:
                dh = abs((s_dt - e_dt).total_seconds()) / 3600.0
                if min_delta_h is None or dh < min_delta_h:
                    min_delta_h = dh

        if min_delta_h is None or min_delta_h > window_h:
            continue

        ex_dur = ex.get("elapsed_s")
        duration_match = (
            bool(strava_duration_s and ex_dur and
                 abs(strava_duration_s - ex_dur) / max(strava_duration_s, ex_dur) < 0.10)
        )
        e_words = {w.lower() for w in (ex.get("activity_name") or "").split() if len(w) >= 4}
        name_match = bool(s_words & e_words)

        if min_delta_h > 1.0 and not duration_match and not name_match:
            continue

        results.append(ex)

    return results


def _find_cross_source_duplicate(
    existing_list: list[dict],
    claimed_ids: set[int],
    start_local: str | None,
    dist_m: float | None,
    date_only: bool = False,
    window_h: float = 2.0,
    dist_tol: float = 0.05,
) -> dict | None:
    """Find an existing activity likely representing the same real-world event.

    Used for cross-source duplicate detection (e.g. Strava/Garmin vs Runmeter/Cyclemeter).

    date_only=True  (Runmeter): only a date is available; matches on same calendar
                                day + distance within dist_tol.
    date_only=False (Cyclemeter): full timestamp available; matches within ±window_h
                                  and distance within dist_tol.
    dist_tol: maximum fractional distance difference accepted (default 5 %).
    """
    if not start_local or not dist_m:
        return None

    if date_only:
        date_str = start_local[:10]  # YYYY-MM-DD
        src_dt = None
    else:
        src_dt = _parse_ts(start_local)
        if src_dt is None:
            return None
        date_str = None

    best: dict | None = None
    best_dist_diff = float("inf")

    for ex in existing_list:
        if ex["id"] in claimed_ids:
            continue

        ex_dist = ex.get("distance_m")
        if not ex_dist:
            continue

        # --- Distance check (primary signal for cross-source matching) ---
        dist_diff = abs(dist_m - ex_dist) / max(dist_m, ex_dist)
        if dist_diff > dist_tol:
            continue

        # --- Time / date check ---
        if date_only:
            # Source is date-only (Runmeter): compare calendar dates
            ex_local = ex.get("start_time_local") or ""
            if ex_local[:10] != date_str:
                continue
        elif ex.get("date_only"):
            # DB entry is date-only (Runmeter stored as midnight): compare
            # source local date against DB date — covers DailyMile UTC→local
            ex_dt_local = ex.get("dt_local")
            if not src_dt or not ex_dt_local:
                continue
            if src_dt.date() != ex_dt_local.date():
                continue
        else:
            # Both have full timestamps: compare within ±window_h
            ex_dts = [
                dt for dt in (
                    _parse_ts(ex.get("start_time_local")),
                    _parse_ts(ex.get("start_time_utc")),
                )
                if dt is not None
            ]
            min_delta_h = min(
                (abs((src_dt - e_dt).total_seconds()) / 3600.0 for e_dt in ex_dts),
                default=None,
            )
            if min_delta_h is None or min_delta_h > window_h:
                continue

        if dist_diff < best_dist_diff:
            best_dist_diff = dist_diff
            best = ex

    return best


def _float(s: str) -> float | None:
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int(s: str) -> int | None:
    f = _float(s)
    return int(round(f)) if f is not None else None


def _float_de(s: str) -> float | None:
    """Parse a German decimal-comma float string (e.g. '8,80' → 8.8)."""
    s = s.strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _nz_int(s: str) -> int | None:
    """Parse integer; return None for zero or empty (means 'not recorded')."""
    v = _int(s)
    return v if v else None


def _nz_float_de(s: str) -> float | None:
    """Parse German decimal float; return None for zero or empty."""
    v = _float_de(s)
    return v if v else None


def rcol(row: list[str], key: str) -> str:
    """Safe column accessor for Runmeter rows."""
    idx = RUNMETER_COL[key]
    return row[idx].strip() if idx < len(row) else ""


def ccol(row: list[str], key: str) -> str:
    """Safe column accessor for Cyclemeter rows."""
    idx = CYCLEMETER_COL[key]
    return row[idx].strip() if idx < len(row) else ""


def _parse_cyclemeter_startzeit(s: str) -> str | None:
    """Convert 'YYYY-MM-DD HH:MM:SS' → ISO 8601 'YYYY-MM-DDTHH:MM:SS'."""
    s = s.strip()
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.isoformat()
    except ValueError:
        return None


def parse_local_date(s: str) -> str | None:
    """Convert 'DD.MM.YYYY, HH:MM:SS' → ISO 8601 string."""
    s = s.strip()
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%d.%m.%Y, %H:%M:%S")
        return dt.isoformat()
    except ValueError:
        return None


def parse_utc_date(s: str) -> str | None:
    """Accept ISO 8601 UTC string as-is, or return None."""
    s = s.strip()
    if not s:
        return None
    # Strava sometimes provides e.g. "2018-06-02 10:16:58 UTC"
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return s  # return as-is if already correct


def extract_gpx_start(gpx_path: Path) -> tuple[float | None, float | None]:
    """Return (lat, lon) of the first trackpoint in a GPX file."""
    try:
        tree = ET.parse(gpx_path)
        root = tree.getroot()
        ns = {"g": GPX_NS}
        trkpt = root.find(".//g:trkpt", ns)
        if trkpt is not None:
            return float(trkpt.get("lat")), float(trkpt.get("lon"))
    except Exception:
        pass
    return None, None


def col(row: list[str], key: str) -> str:
    """Safe column accessor."""
    idx = COL[key]
    return row[idx].strip() if idx < len(row) else ""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and insert a default user row. Only called with --init-db."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT 'default'
        );
        CREATE TABLE IF NOT EXISTS activities (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id                 INTEGER NOT NULL REFERENCES users(id),
            garmin_activity_id      TEXT    NOT NULL,
            activity_name           TEXT,
            activity_type           TEXT,
            sport_type              TEXT,
            start_time_utc          TEXT,
            start_time_local        TEXT,
            timezone                TEXT,
            duration_s              REAL,
            elapsed_time_s          REAL,
            moving_time_s           REAL,
            distance_m              REAL,
            elevation_gain_m        REAL,
            elevation_loss_m        REAL,
            min_elevation_m         REAL,
            max_elevation_m         REAL,
            avg_speed_ms            REAL,
            max_speed_ms            REAL,
            avg_hr                  INTEGER,
            max_hr                  INTEGER,
            resting_hr              INTEGER,
            avg_power_w             REAL,
            max_power_w             REAL,
            normalized_power_w      REAL,
            avg_cadence             INTEGER,
            max_cadence             INTEGER,
            avg_stride_length_m     REAL,
            avg_vertical_osc_cm     REAL,
            avg_ground_contact_ms   REAL,
            aerobic_training_effect REAL,
            training_stress_score   REAL,
            vo2max_estimate         REAL,
            intensity_factor        REAL,
            calories                INTEGER,
            steps                   INTEGER,
            avg_temperature_c       REAL,
            max_temperature_c       REAL,
            start_lat               REAL,
            start_lon               REAL,
            raw_json                TEXT,
            gpx_path                TEXT,
            fit_path                TEXT,
            caldav_pushed           INTEGER NOT NULL DEFAULT 0,
            mastodon_posted         INTEGER NOT NULL DEFAULT 0,
            source                  TEXT,
            synced_at               TEXT    NOT NULL,
            UNIQUE(user_id, garmin_activity_id)
        );
    """)
    conn.commit()


def check_prerequisites(conn: sqlite3.Connection, user_id: int) -> None:
    """Abort with a clear error if the required tables or user row are missing."""
    for table in ("users", "activities"):
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if row is None:
            sys.exit(
                f"ERROR: table '{table}' does not exist in the database.\n"
                f"       Run with --init-db to create the schema on first use."
            )

    row = conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        sys.exit(
            f"ERROR: user_id {user_id} not found in the 'users' table.\n"
            f"       Run with --init-db to insert a default user row, or add the\n"
            f"       user manually before importing."
        )


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

def migrate_schema(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema version, if missing."""
    for stmt in (
        "ALTER TABLE activities ADD COLUMN fit_path TEXT",
        "ALTER TABLE activities ADD COLUMN source TEXT",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


# ---------------------------------------------------------------------------
# Core import logic
# ---------------------------------------------------------------------------


def _resolve_duplicate(
    conn: sqlite3.Connection,
    existing: dict,
    gpx_src: Path | None,
    is_fit: bool,
    gpx_dest: Path | None,
    fit_dest: Path | None,
    overwrite: bool,
    dry_run: bool,
) -> tuple[str, str | None]:
    """Check whether a duplicate activity is missing a file path and fill it in.

    Returns (status, detail):
      status  — 'complete'  (nothing to add)
              — 'completed' (file path(s) added / would be added in dry-run)
      detail  — human-readable description of what was added, or None.
    """
    if gpx_src is None or not gpx_src.exists():
        return "complete", None

    updates: dict[str, str] = {}
    detail_parts: list[str] = []

    if is_fit:
        if not existing.get("fit_path"):
            dest = (fit_dest / gpx_src.name) if fit_dest else gpx_src
            if dest == gpx_src or not dest.exists() or overwrite:
                if not dry_run and dest != gpx_src:
                    shutil.copy2(gpx_src, dest)
                updates["fit_path"] = str(dest)
                detail_parts.append(f"FIT → {dest.name}")
    else:
        if not existing.get("gpx_path"):
            dest = (gpx_dest / gpx_src.name) if gpx_dest else gpx_src
            if dest == gpx_src or not dest.exists() or overwrite:
                if not dry_run and dest != gpx_src:
                    shutil.copy2(gpx_src, dest)
                updates["gpx_path"] = str(dest)
                detail_parts.append(f"GPX → {dest.name}")

    if updates and not dry_run:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE activities SET {set_clause} WHERE id = ?",
            (*updates.values(), existing["id"]),
        )
        conn.commit()

    if updates:
        return "completed", " + ".join(detail_parts)
    return "complete", None

def _parse_row(row: list[str]) -> dict:
    """Extract and type-convert all fields from a CSV row. Returns a dict."""
    name_raw = col(row, "name")
    activity_id = row[COL["activity_id"]].strip()
    return {
        "activity_id":      activity_id,
        "garmin_activity_id": f"strava_{activity_id}",
        "activity_name":    name_raw if name_raw else "(no name)",
        "activity_type":    col(row, "type"),
        "description":      col(row, "description"),
        "start_local":      parse_local_date(col(row, "date_local")),
        "start_utc":        parse_utc_date(col(row, "start_utc")),
        "gpx_rel":          col(row, "filename"),
        "elapsed_s":        _float(col(row, "elapsed_s")),
        "moving_s":         _float(col(row, "moving_s")),
        "dist_m":           _float(col(row, "dist_m")),
        "max_speed":        _float(col(row, "max_speed")),
        "avg_speed":        _float(col(row, "avg_speed")),
        "elev_gain":        _float(col(row, "elev_gain")),
        "elev_loss":        _float(col(row, "elev_loss")),
        "min_ele":          _float(col(row, "min_ele")),
        "max_ele":          _float(col(row, "max_ele")),
        "max_cad":          _int(col(row, "max_cad")),
        "avg_cad":          _int(col(row, "avg_cad")),
        "max_hr":           _int(col(row, "max_hr")),
        "avg_hr":           _int(col(row, "avg_hr")),
        "max_pwr":          _float(col(row, "max_pwr")),
        "avg_pwr":          _float(col(row, "avg_pwr")),
        "norm_pwr":         _float(col(row, "norm_pwr")),
        "calories":         _int(col(row, "calories")),
        "max_temp":         _float(col(row, "max_temp")),
        "avg_temp":         _float(col(row, "avg_temp")),
        "intensity_f":      _float(col(row, "intensity_f")),
        "steps":            _int(col(row, "steps")),
        "tss":              _float(col(row, "tss")),
    }


def _parse_runmeter_row(row: list[str]) -> dict:
    """Extract and convert all fields from a Runmeter CSV row."""
    date = rcol(row, "date")
    activity_type = rcol(row, "type")
    count = _int(rcol(row, "count")) or 1

    dist_km = _float_de(rcol(row, "dist_km"))
    dist_m = dist_km * 1000.0 if dist_km is not None else None

    duration_s = _float(rcol(row, "duration_s"))

    elev_gain = _float_de(rcol(row, "elev_gain"))
    elev_loss = _float_de(rcol(row, "elev_loss"))
    calories = _int(rcol(row, "calories")) or None

    avg_speed_kmh = _float_de(rcol(row, "avg_speed_kmh"))
    avg_speed_ms = avg_speed_kmh / 3.6 if avg_speed_kmh else None

    max_speed_kmh = _float_de(rcol(row, "max_speed_kmh"))
    max_speed_ms = max_speed_kmh / 3.6 if max_speed_kmh else None

    steps = _nz_int(rcol(row, "steps"))
    max_hr = _nz_int(rcol(row, "max_hr"))
    avg_hr = _nz_int(rcol(row, "avg_hr"))

    # Cadence: step frequency for running, pedal frequency for cycling
    is_cycling = any(kw in activity_type.lower() for kw in _RUNMETER_CYCLING_KEYWORDS)
    if is_cycling:
        max_cad = _nz_int(rcol(row, "max_ped_cad"))
        avg_cad = _nz_int(rcol(row, "avg_ped_cad"))
    else:
        max_cad = _nz_int(rcol(row, "max_step_cad"))
        avg_cad = _nz_int(rcol(row, "avg_step_cad"))

    max_power = _nz_float_de(rcol(row, "max_power"))
    avg_power = _nz_float_de(rcol(row, "avg_power"))
    norm_power = _nz_float_de(rcol(row, "norm_power"))

    type_slug = activity_type.lower().replace(" ", "_")
    garmin_activity_id = f"runmeter_{date}_{type_slug}"

    return {
        "garmin_activity_id": garmin_activity_id,
        "activity_name":      f"{activity_type} {date}",
        "activity_type":      activity_type,
        "start_local":        f"{date}T00:00:00",
        "duration_s":         duration_s,
        "dist_m":             dist_m,
        "elev_gain":          elev_gain,
        "elev_loss":          elev_loss,
        "avg_speed_ms":       avg_speed_ms,
        "max_speed_ms":       max_speed_ms,
        "avg_hr":             avg_hr,
        "max_hr":             max_hr,
        "avg_cad":            avg_cad,
        "max_cad":            max_cad,
        "avg_power":          avg_power,
        "max_power":          max_power,
        "norm_power":         norm_power,
        "calories":           calories,
        "steps":              steps,
        "count":              count,
    }


def _parse_cyclemeter_row(row: list[str]) -> dict | None:
    """Extract and convert all fields from a Cyclemeter CSV row.

    Returns None for rows that must be skipped:
      - empty or unparseable Startzeit
      - zero or missing distance
    """
    raw_startzeit = ccol(row, "startzeit")
    start_local = _parse_cyclemeter_startzeit(raw_startzeit)
    if start_local is None:
        return None

    dist_km = _float_de(ccol(row, "dist_km"))
    if not dist_km:
        return None  # zero-distance recording error
    dist_m = dist_km * 1000.0

    route = ccol(row, "route")
    activity_type_raw = ccol(row, "type")
    activity_type = activity_type_raw if activity_type_raw else "Fahrrad"

    moving_s = _float(ccol(row, "moving_s"))
    pause_s = _float(ccol(row, "pause_s")) or 0.0
    elapsed_s = (moving_s or 0.0) + pause_s

    elev_gain = _float_de(ccol(row, "elev_gain"))
    elev_loss = _float_de(ccol(row, "elev_loss"))
    calories = _int(ccol(row, "calories")) or None

    avg_speed_kmh = _float_de(ccol(row, "avg_speed_kmh"))
    avg_speed_ms = avg_speed_kmh / 3.6 if avg_speed_kmh else None

    max_speed_kmh = _float_de(ccol(row, "max_speed_kmh"))
    max_speed_ms = max_speed_kmh / 3.6 if max_speed_kmh else None

    max_hr    = _nz_int(ccol(row, "max_hr"))
    avg_hr    = _nz_int(ccol(row, "avg_hr"))
    max_cad   = _nz_int(ccol(row, "max_cad"))
    avg_cad   = _nz_int(ccol(row, "avg_cad"))
    max_power = _nz_float_de(ccol(row, "max_power"))
    avg_power = _nz_float_de(ccol(row, "avg_power"))
    norm_power = _nz_float_de(ccol(row, "norm_power"))
    intensity_f = _nz_float_de(ccol(row, "intensity_f"))
    tss = _nz_float_de(ccol(row, "tss"))

    bike  = ccol(row, "bike")
    notes = ccol(row, "notes")

    # Synthetic unique ID from timestamp (colons replaced to be filename-safe)
    ts_slug = raw_startzeit.replace(" ", "T").replace(":", "-")
    garmin_activity_id = f"cyclemeter_{ts_slug}"

    activity_name = route if route else f"{activity_type} {raw_startzeit[:10]}"

    return {
        "garmin_activity_id": garmin_activity_id,
        "activity_name":      activity_name,
        "activity_type":      activity_type,
        "start_local":        start_local,
        "moving_s":           moving_s,
        "elapsed_s":          elapsed_s or None,
        "dist_m":             dist_m,
        "elev_gain":          elev_gain,
        "elev_loss":          elev_loss,
        "avg_speed_ms":       avg_speed_ms,
        "max_speed_ms":       max_speed_ms,
        "avg_hr":             avg_hr,
        "max_hr":             max_hr,
        "avg_cad":            avg_cad,
        "max_cad":            max_cad,
        "avg_power":          avg_power,
        "max_power":          max_power,
        "norm_power":         norm_power,
        "intensity_f":        intensity_f,
        "tss":                tss,
        "calories":           calories,
        "bike":               bike if bike and bike.lower() != "keine" else None,
        "notes":              notes if notes else None,
    }


def import_activities(
    dump_dir: Path,
    db_path: Path,
    gpx_dest: Path | None,
    fit_dest: Path | None,
    start_date: datetime | None,
    end_date: datetime | None,
    user_id: int,
    dry_run: bool,
    overwrite_gpx: bool,
    init_db: bool,
) -> None:
    activities_csv = dump_dir / "activities.csv"
    if not activities_csv.exists():
        sys.exit(f"ERROR: {activities_csv} not found.")

    if dry_run:
        # Open read-only — a dry-run must never touch the DB.
        if db_path.exists():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            check_prerequisites(conn, user_id)
        else:
            if not init_db:
                sys.exit(
                    f"ERROR: database file not found: {db_path}\n"
                    f"       Run with --init-db to create it on first use."
                )
            # New DB: nothing to check against; use empty in-memory DB for the duplicate scan.
            conn = sqlite3.connect(":memory:")
            init_schema(conn)
            conn.execute("INSERT OR IGNORE INTO users (id, name) VALUES (?, 'default')", (user_id,))
            conn.commit()
    else:
        if not db_path.exists() and not init_db:
            sys.exit(
                f"ERROR: database file not found: {db_path}\n"
                f"       Run with --init-db to create it on first use."
            )
        conn = sqlite3.connect(db_path)
        if init_db:
            init_schema(conn)
            conn.execute("INSERT OR IGNORE INTO users (id, name) VALUES (?, 'default')", (user_id,))
            conn.commit()
        check_prerequisites(conn, user_id)
        migrate_schema(conn)

    # Pre-load existing activities for duplicate detection (by ID and by time).
    # Gracefully handle DBs that predate the fit_path column.
    _has_fit_path = bool(conn.execute(
        "SELECT 1 FROM pragma_table_info('activities') WHERE name='fit_path'"
    ).fetchone())
    _select = (
        "SELECT garmin_activity_id, id, gpx_path, fit_path, start_time_local, start_time_utc,"
        "       activity_name, elapsed_time_s "
        "FROM activities WHERE user_id = ?"
        if _has_fit_path else
        "SELECT garmin_activity_id, id, gpx_path, NULL, start_time_local, start_time_utc,"
        "       activity_name, elapsed_time_s "
        "FROM activities WHERE user_id = ?"
    )
    existing_by_id: dict[str, dict] = {}
    existing_by_time: dict[str, dict] = {}
    existing_list: list[dict] = []          # for fuzzy time matching
    for r in conn.execute(_select, (user_id,)):
        entry = {
            "id": r[1], "gpx_path": r[2], "fit_path": r[3],
            "start_time_local": r[4], "start_time_utc": r[5],
            "activity_name": r[6], "elapsed_s": r[7],
        }
        existing_by_id[r[0]] = entry
        existing_list.append(entry)
        # Index by both local and UTC timestamps (normalised) so we match
        # regardless of which one Strava's date_local actually corresponds to.
        for raw_ts in (r[4], r[5]):
            norm = _normalize_ts(raw_ts)
            if norm and norm not in existing_by_time:
                existing_by_time[norm] = entry
    existing_ids: set[str] = set(existing_by_id.keys())
    fuzzy_claimed_ids: set[int] = set()     # DB row ids claimed by a fuzzy match this run

    if not dry_run and gpx_dest:
        gpx_dest.mkdir(parents=True, exist_ok=True)
    if not dry_run and fit_dest:
        fit_dest.mkdir(parents=True, exist_ok=True)

    # Counters
    n_new = n_skipped_complete = n_completed = n_fuzzy = n_date_filtered = n_gpx_missing = n_parse_error = n_gpx_skipped = 0
    # Collect issues for dry-run report
    issues: list[str] = []

    with open(activities_csv, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        next(reader)  # skip header

        for lineno, row in enumerate(reader, start=2):
            if not row or not row[0].strip():
                continue

            # --- parse ---
            try:
                d = _parse_row(row)
            except Exception as exc:
                msg = f"line {lineno}: parse error — {exc}"
                print(f"  ERROR {msg}", file=sys.stderr)
                issues.append(f"PARSE ERROR  {msg}")
                n_parse_error += 1
                continue

            # --- date filter ---
            if d["start_local"]:
                dt = datetime.fromisoformat(d["start_local"])
                if start_date and dt < start_date:
                    n_date_filtered += 1
                    continue
                if end_date and dt > end_date:
                    n_date_filtered += 1
                    continue

            # --- activity file info (shared by duplicate and new-insert paths) ---
            gpx_rel = d["gpx_rel"]
            gpx_src = dump_dir / gpx_rel if gpx_rel else None
            is_fit = (gpx_rel.endswith(".fit") or gpx_rel.endswith(".fit.gz")) if gpx_rel else False

            # --- duplicate check (by ID, then exact time, then fuzzy time) ---
            is_fuzzy_match = False
            existing = existing_by_id.get(d["garmin_activity_id"])
            if existing is None:
                for ts in (d["start_local"], d["start_utc"]):
                    norm = _normalize_ts(ts)
                    if norm and norm in existing_by_time:
                        existing = existing_by_time[norm]
                        existing_ids.add(d["garmin_activity_id"])
                        break
            if existing is None:
                existing = _fuzzy_time_match(
                    existing_list, fuzzy_claimed_ids,
                    d["start_local"], d["start_utc"],
                    d["activity_name"], d["elapsed_s"],
                )
                if existing is not None:
                    is_fuzzy_match = True
                    existing_ids.add(d["garmin_activity_id"])
                    fuzzy_claimed_ids.add(existing["id"])

            if d["garmin_activity_id"] in existing_ids:
                if existing is not None:
                    status, detail = _resolve_duplicate(
                        conn, existing, gpx_src, is_fit,
                        gpx_dest, fit_dest, overwrite_gpx, dry_run,
                    )
                    tag = "FUZZY MATCH " if is_fuzzy_match else "COMPLETED   "
                    if status == "completed":
                        n_completed += 1
                        if is_fuzzy_match:
                            n_fuzzy += 1
                        msg = (f"{tag} {d['activity_name']!r:50s} {d['start_local']}"
                               f"  ({detail})")
                        print(f"  {msg}")
                        issues.append(msg)
                    else:
                        n_skipped_complete += 1
                        if is_fuzzy_match:
                            n_fuzzy += 1
                        if dry_run:
                            tag2 = "FUZZY DUP   " if is_fuzzy_match else "DUPLICATE   "
                            issues.append(
                                f"{tag2} {d['activity_name']!r:50s} {d['start_local']}"
                            )

                    # --- secondary backfill: other DB entries at same time missing file ---
                    # This catches Garmin-native entries (different ID, no file) that
                    # represent the same real-world activity as this Strava record.
                    if gpx_src and gpx_src.exists():
                        exclude = fuzzy_claimed_ids | {existing["id"]}
                        for cand in _find_missing_file_candidates(
                            existing_list, exclude,
                            d["start_local"], d["start_utc"],
                            d["activity_name"], d["elapsed_s"],
                            is_fit,
                        ):
                            sec_status, sec_detail = _resolve_duplicate(
                                conn, cand, gpx_src, is_fit,
                                gpx_dest, fit_dest, overwrite_gpx, dry_run,
                            )
                            if sec_status == "completed":
                                n_completed += 1
                                fuzzy_claimed_ids.add(cand["id"])
                                msg = (
                                    f"BACKFILLED   {cand.get('activity_name')!r:50s}"
                                    f"  via:{d['activity_name']!r} {d['start_local']}"
                                    f"  ({sec_detail})"
                                )
                                print(f"  {msg}")
                                issues.append(msg)
                else:
                    # inserted during this run — can't look up row, just skip
                    n_skipped_complete += 1
                continue

            # --- new-activity file handling ---
            gpx_dest_path: str | None = None
            fit_dest_path: str | None = None
            gpx_will_collide = False

            if gpx_src and gpx_src.exists():
                target_dir = fit_dest if is_fit else gpx_dest
                if target_dir:
                    dest_file = target_dir / gpx_src.name
                    if is_fit:
                        fit_dest_path = str(dest_file)
                    else:
                        gpx_dest_path = str(dest_file)
                    if dest_file.exists() and not overwrite_gpx:
                        gpx_will_collide = True
                        n_gpx_skipped += 1
                        issues.append(
                            f"FILE EXISTS  {d['activity_name']!r:50s} {d['start_local']}  "
                            f"({dest_file.name} already in dest — use --overwrite-gpx to replace)"
                        )
                else:
                    if is_fit:
                        fit_dest_path = str(gpx_src)
                    else:
                        gpx_dest_path = str(gpx_src)
            elif gpx_rel:
                n_gpx_missing += 1
                issues.append(
                    f"FILE MISSING {d['activity_name']!r:50s} {d['start_local']}  "
                    f"({gpx_src})"
                )

            # --- dry-run: stop here, collect stats ---
            if dry_run:
                n_new += 1
                continue

            # --- real insert ---
            start_lat, start_lon = None, None
            if gpx_src and gpx_src.exists():
                if not is_fit:
                    start_lat, start_lon = extract_gpx_start(gpx_src)
                if not gpx_will_collide:
                    target_dir = fit_dest if is_fit else gpx_dest
                    if target_dir:
                        shutil.copy2(gpx_src, target_dir / gpx_src.name)

            raw_json = json.dumps(
                {"strava_description": d["description"], "strava_activity_id": d["activity_id"]},
                ensure_ascii=False,
            )
            synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            try:
                conn.execute(
                    """
                    INSERT INTO activities (
                        user_id, garmin_activity_id,
                        activity_name, activity_type, sport_type,
                        start_time_utc, start_time_local,
                        duration_s, elapsed_time_s, moving_time_s,
                        distance_m, elevation_gain_m, elevation_loss_m,
                        min_elevation_m, max_elevation_m,
                        avg_speed_ms, max_speed_ms,
                        avg_hr, max_hr,
                        avg_power_w, max_power_w, normalized_power_w,
                        avg_cadence, max_cadence,
                        training_stress_score, intensity_factor,
                        calories, steps,
                        avg_temperature_c, max_temperature_c,
                        start_lat, start_lon,
                        raw_json, gpx_path, fit_path, source,
                        synced_at
                    ) VALUES (
                        ?,?,  ?,?,?,  ?,?,  ?,?,?,  ?,?,?,  ?,?,  ?,?,
                        ?,?,  ?,?,?,  ?,?,  ?,?,  ?,?,  ?,?,  ?,?,  ?,?,?,?,  ?
                    )
                    """,
                    (
                        user_id, d["garmin_activity_id"],
                        d["activity_name"], d["activity_type"], d["activity_type"],
                        d["start_utc"], d["start_local"],
                        d["elapsed_s"], d["elapsed_s"], d["moving_s"],
                        d["dist_m"], d["elev_gain"], d["elev_loss"],
                        d["min_ele"], d["max_ele"],
                        d["avg_speed"], d["max_speed"],
                        d["avg_hr"], d["max_hr"],
                        d["avg_pwr"], d["max_pwr"], d["norm_pwr"],
                        d["avg_cad"], d["max_cad"],
                        d["tss"], d["intensity_f"],
                        d["calories"], d["steps"],
                        d["avg_temp"], d["max_temp"],
                        start_lat, start_lon,
                        raw_json, gpx_dest_path, fit_dest_path, "Strava-Import",
                        synced_at,
                    ),
                )
                conn.commit()
                existing_ids.add(d["garmin_activity_id"])
                n_new += 1
            except Exception as exc:
                print(
                    f"  ERROR line {lineno} activity {d['activity_id']}: {exc}",
                    file=sys.stderr,
                )
                n_parse_error += 1

    conn.close()

    # --- summary ---
    if dry_run:
        print("DRY-RUN — nothing was written.\n")
        if issues:
            print("Issues found:")
            for issue in issues:
                print(f"  {issue}")
            print()
        print("Summary:")
        print(f"  Would import          : {n_new}")
        print(f"  Skipped (complete)    : {n_skipped_complete}  (already in DB with all files)")
        print(f"  Completed (file added): {n_completed}  (was in DB, missing GPX/FIT added)")
        print(f"    of which fuzzy match: {n_fuzzy}  (matched via ±2 h time window)")
        print(f"  Outside dates         : {n_date_filtered}  (filtered out)")
        print(f"  File missing          : {n_gpx_missing}  (activity imported, no file to copy)")
        print(f"  File name conflict    : {n_gpx_skipped}  (existing file preserved; use --overwrite-gpx to replace)")
        print(f"  Parse errors          : {n_parse_error}  (would skip)")
    else:
        print(f"\nDone.")
        print(f"  Imported              : {n_new}")
        print(f"  Skipped (complete)    : {n_skipped_complete}  (already in DB with all files)")
        print(f"  Completed (file added): {n_completed}  (was in DB, missing GPX/FIT added)")
        print(f"    of which fuzzy match: {n_fuzzy}  (matched via ±2 h time window)")
        print(f"  Outside dates         : {n_date_filtered}  (filtered out)")
        print(f"  File missing          : {n_gpx_missing}")
        print(f"  File name conflict    : {n_gpx_skipped}  (existing file preserved)")
        print(f"  Errors                : {n_parse_error}")


# ---------------------------------------------------------------------------
# Runmeter import
# ---------------------------------------------------------------------------

def import_runmeter_activities(
    csv_path: Path,
    db_path: Path,
    start_date: datetime | None,
    end_date: datetime | None,
    user_id: int,
    dry_run: bool,
    init_db: bool,
) -> None:
    if not csv_path.exists():
        sys.exit(f"ERROR: {csv_path} not found.")

    # --- DB connection (same logic as import_activities) ---
    if dry_run:
        if db_path.exists():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            check_prerequisites(conn, user_id)
        else:
            if not init_db:
                sys.exit(
                    f"ERROR: database file not found: {db_path}\n"
                    f"       Run with --init-db to create it on first use."
                )
            conn = sqlite3.connect(":memory:")
            init_schema(conn)
            conn.execute("INSERT OR IGNORE INTO users (id, name) VALUES (?, 'default')", (user_id,))
            conn.commit()
    else:
        if not db_path.exists() and not init_db:
            sys.exit(
                f"ERROR: database file not found: {db_path}\n"
                f"       Run with --init-db to create it on first use."
            )
        conn = sqlite3.connect(db_path)
        if init_db:
            init_schema(conn)
            conn.execute("INSERT OR IGNORE INTO users (id, name) VALUES (?, 'default')", (user_id,))
            conn.commit()
        check_prerequisites(conn, user_id)
        migrate_schema(conn)

    # Pre-load existing activities for ID and cross-source duplicate detection
    existing_ids: set[str] = set()
    existing_list: list[dict] = []
    cross_claimed_ids: set[int] = set()
    for r in conn.execute(
        "SELECT garmin_activity_id, id, start_time_local, start_time_utc, distance_m, activity_name "
        "FROM activities WHERE user_id = ?",
        (user_id,),
    ):
        existing_ids.add(r[0])
        _dt_local = _parse_ts(r[2])
        existing_list.append({
            "id": r[1], "start_time_local": r[2], "start_time_utc": r[3],
            "distance_m": r[4], "activity_name": r[5],
            "dt_local": _dt_local,
            "date_only": bool(_dt_local and _dt_local.hour == 0 and _dt_local.minute == 0 and _dt_local.second == 0),
        })

    n_new = n_skipped = n_cross_dup = n_date_filtered = n_parse_error = 0
    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter=";")
        next(reader)  # skip header

        for lineno, row in enumerate(reader, start=2):
            if not row or not row[0].strip():
                continue

            try:
                d = _parse_runmeter_row(row)
            except Exception as exc:
                print(f"  ERROR line {lineno}: parse error — {exc}", file=sys.stderr)
                n_parse_error += 1
                continue

            # --- date filter ---
            try:
                dt = datetime.fromisoformat(d["start_local"])
            except ValueError:
                print(f"  ERROR line {lineno}: bad date {d['start_local']!r}", file=sys.stderr)
                n_parse_error += 1
                continue
            if start_date and dt < start_date:
                n_date_filtered += 1
                continue
            if end_date and dt > end_date:
                n_date_filtered += 1
                continue

            # --- exact duplicate check ---
            if d["garmin_activity_id"] in existing_ids:
                n_skipped += 1
                continue

            # --- cross-source duplicate check (same calendar day + distance) ---
            cross_dup = _find_cross_source_duplicate(
                existing_list, cross_claimed_ids,
                d["start_local"], d["dist_m"],
                date_only=True,
            )
            if cross_dup is not None:
                cross_claimed_ids.add(cross_dup["id"])
                n_cross_dup += 1
                print(
                    f"  CROSS-DUP    {d['activity_name']!r:45s} {d['start_local'][:10]}"
                    f"  ≈ {cross_dup.get('activity_name')!r}"
                    f"  dist {d['dist_m']:.0f}m ≈ {cross_dup['distance_m']:.0f}m"
                )
                continue

            if dry_run:
                n_new += 1
                continue

            # --- insert ---
            raw_json = json.dumps({"runmeter_count": d["count"]}, ensure_ascii=False)
            try:
                conn.execute(
                    """
                    INSERT INTO activities (
                        user_id, garmin_activity_id,
                        activity_name, activity_type, sport_type,
                        start_time_local,
                        duration_s, elapsed_time_s, moving_time_s,
                        distance_m, elevation_gain_m, elevation_loss_m,
                        avg_speed_ms, max_speed_ms,
                        avg_hr, max_hr,
                        avg_power_w, max_power_w, normalized_power_w,
                        avg_cadence, max_cadence,
                        calories, steps,
                        raw_json, source, synced_at
                    ) VALUES (
                        ?,?,  ?,?,?,  ?,  ?,?,?,  ?,?,?,  ?,?,  ?,?,  ?,?,?,  ?,?,  ?,?,  ?,?,?
                    )
                    """,
                    (
                        user_id, d["garmin_activity_id"],
                        d["activity_name"], d["activity_type"], d["activity_type"],
                        d["start_local"],
                        d["duration_s"], d["duration_s"], d["duration_s"],
                        d["dist_m"], d["elev_gain"], d["elev_loss"],
                        d["avg_speed_ms"], d["max_speed_ms"],
                        d["avg_hr"], d["max_hr"],
                        d["avg_power"], d["max_power"], d["norm_power"],
                        d["avg_cad"], d["max_cad"],
                        d["calories"], d["steps"],
                        raw_json, "Runmeter-Import", synced_at,
                    ),
                )
                conn.commit()
                existing_ids.add(d["garmin_activity_id"])
                n_new += 1
            except Exception as exc:
                print(
                    f"  ERROR line {lineno} {d['garmin_activity_id']}: {exc}",
                    file=sys.stderr,
                )
                n_parse_error += 1

    conn.close()

    # --- summary ---
    if dry_run:
        print("DRY-RUN — nothing was written.\n")
    print(f"\nDone.")
    print(f"  Imported              : {n_new}")
    print(f"  Skipped (duplicate)   : {n_skipped}  (already in DB, same source)")
    print(f"  Skipped (cross-source): {n_cross_dup}  (same day + distance already in DB)")
    print(f"  Outside dates         : {n_date_filtered}  (filtered out)")
    print(f"  Errors                : {n_parse_error}")


# ---------------------------------------------------------------------------
# Cyclemeter import
# ---------------------------------------------------------------------------

def import_cyclemeter_activities(
    csv_path: Path,
    db_path: Path,
    start_date: datetime | None,
    end_date: datetime | None,
    user_id: int,
    dry_run: bool,
    init_db: bool,
) -> None:
    if not csv_path.exists():
        sys.exit(f"ERROR: {csv_path} not found.")

    # --- DB connection ---
    if dry_run:
        if db_path.exists():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            check_prerequisites(conn, user_id)
        else:
            if not init_db:
                sys.exit(
                    f"ERROR: database file not found: {db_path}\n"
                    f"       Run with --init-db to create it on first use."
                )
            conn = sqlite3.connect(":memory:")
            init_schema(conn)
            conn.execute("INSERT OR IGNORE INTO users (id, name) VALUES (?, 'default')", (user_id,))
            conn.commit()
    else:
        if not db_path.exists() and not init_db:
            sys.exit(
                f"ERROR: database file not found: {db_path}\n"
                f"       Run with --init-db to create it on first use."
            )
        conn = sqlite3.connect(db_path)
        if init_db:
            init_schema(conn)
            conn.execute("INSERT OR IGNORE INTO users (id, name) VALUES (?, 'default')", (user_id,))
            conn.commit()
        check_prerequisites(conn, user_id)
        migrate_schema(conn)

    existing_ids: set[str] = set()
    existing_list: list[dict] = []
    cross_claimed_ids: set[int] = set()
    for r in conn.execute(
        "SELECT garmin_activity_id, id, start_time_local, start_time_utc, distance_m, activity_name "
        "FROM activities WHERE user_id = ?",
        (user_id,),
    ):
        existing_ids.add(r[0])
        _dt_local = _parse_ts(r[2])
        existing_list.append({
            "id": r[1], "start_time_local": r[2], "start_time_utc": r[3],
            "distance_m": r[4], "activity_name": r[5],
            "dt_local": _dt_local,
            "date_only": bool(_dt_local and _dt_local.hour == 0 and _dt_local.minute == 0 and _dt_local.second == 0),
        })

    n_new = n_skipped = n_cross_dup = n_date_filtered = n_skipped_invalid = n_parse_error = 0
    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter=";")
        next(reader)  # skip header

        for lineno, row in enumerate(reader, start=2):
            if not row or not row[0].strip():
                continue

            try:
                d = _parse_cyclemeter_row(row)
            except Exception as exc:
                print(f"  ERROR line {lineno}: parse error — {exc}", file=sys.stderr)
                n_parse_error += 1
                continue

            if d is None:
                # empty startzeit or zero-distance — skip silently
                n_skipped_invalid += 1
                continue

            # --- date filter ---
            try:
                dt = datetime.fromisoformat(d["start_local"])
            except ValueError:
                print(f"  ERROR line {lineno}: bad date {d['start_local']!r}", file=sys.stderr)
                n_parse_error += 1
                continue
            if start_date and dt < start_date:
                n_date_filtered += 1
                continue
            if end_date and dt > end_date:
                n_date_filtered += 1
                continue

            # --- exact duplicate check ---
            if d["garmin_activity_id"] in existing_ids:
                n_skipped += 1
                continue

            # --- cross-source duplicate check (±2 h window + distance) ---
            cross_dup = _find_cross_source_duplicate(
                existing_list, cross_claimed_ids,
                d["start_local"], d["dist_m"],
                date_only=False,
                window_h=2.0,
            )
            if cross_dup is not None:
                cross_claimed_ids.add(cross_dup["id"])
                n_cross_dup += 1
                print(
                    f"  CROSS-DUP    {d['activity_name']!r:45s} {d['start_local']}"
                    f"  ≈ {cross_dup.get('activity_name')!r}"
                    f"  dist {d['dist_m']:.0f}m ≈ {cross_dup['distance_m']:.0f}m"
                )
                continue

            if dry_run:
                n_new += 1
                continue

            # --- insert ---
            raw_json = json.dumps(
                {k: v for k, v in {"cyclemeter_bike": d["bike"], "cyclemeter_notes": d["notes"]}.items() if v},
                ensure_ascii=False,
            )
            try:
                conn.execute(
                    """
                    INSERT INTO activities (
                        user_id, garmin_activity_id,
                        activity_name, activity_type, sport_type,
                        start_time_local,
                        duration_s, elapsed_time_s, moving_time_s,
                        distance_m, elevation_gain_m, elevation_loss_m,
                        avg_speed_ms, max_speed_ms,
                        avg_hr, max_hr,
                        avg_power_w, max_power_w, normalized_power_w,
                        avg_cadence, max_cadence,
                        intensity_factor, training_stress_score,
                        calories,
                        raw_json, source, synced_at
                    ) VALUES (
                        ?,?,  ?,?,?,  ?,  ?,?,?,  ?,?,?,  ?,?,  ?,?,  ?,?,?,  ?,?,  ?,?,  ?,  ?,?,?
                    )
                    """,
                    (
                        user_id, d["garmin_activity_id"],
                        d["activity_name"], d["activity_type"], d["activity_type"],
                        d["start_local"],
                        d["elapsed_s"], d["elapsed_s"], d["moving_s"],
                        d["dist_m"], d["elev_gain"], d["elev_loss"],
                        d["avg_speed_ms"], d["max_speed_ms"],
                        d["avg_hr"], d["max_hr"],
                        d["avg_power"], d["max_power"], d["norm_power"],
                        d["avg_cad"], d["max_cad"],
                        d["intensity_f"], d["tss"],
                        d["calories"],
                        raw_json, "Cyclemeter-Import", synced_at,
                    ),
                )
                conn.commit()
                existing_ids.add(d["garmin_activity_id"])
                n_new += 1
            except Exception as exc:
                print(
                    f"  ERROR line {lineno} {d['garmin_activity_id']}: {exc}",
                    file=sys.stderr,
                )
                n_parse_error += 1

    conn.close()

    # --- summary ---
    if dry_run:
        print("DRY-RUN — nothing was written.\n")
    print(f"\nDone.")
    print(f"  Imported              : {n_new}")
    print(f"  Skipped (duplicate)   : {n_skipped}  (already in DB, same source)")
    print(f"  Skipped (cross-source): {n_cross_dup}  (same time ±2h + distance already in DB)")
    print(f"  Skipped (invalid)     : {n_skipped_invalid}  (empty timestamp or zero distance)")
    print(f"  Outside dates         : {n_date_filtered}  (filtered out)")
    print(f"  Errors                : {n_parse_error}")


# ---------------------------------------------------------------------------
# DailyMile import
# ---------------------------------------------------------------------------

def _parse_dailymile_row(row: list[str], base_dir: Path) -> dict | None:
    """Parse a DailyMile CSV row and its companion JSON file.

    Returns None for rows that must be skipped (zero/noise distance,
    unparseable date).
    """
    if len(row) < 5:
        return None
    rel_path = row[0].strip()
    title    = row[1].strip()
    date_str = row[2].strip()   # "YYYY-MM-DD HH:MM:SS UTC"
    text     = row[3].strip() if len(row) > 3 else ""
    dist_str = row[4].strip() if len(row) > 4 else ""
    dur_str  = row[5].strip() if len(row) > 5 else ""

    # Parse UTC start time
    try:
        start_utc_dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return None

    # Distance — skip zero / GPS noise (≤ 9 m)
    dist_m = _float(dist_str) or 0.0
    if dist_m <= 9:
        return None

    # Duration — discard clearly bogus values > 24 h
    duration_s = _float(dur_str)
    if duration_s and duration_s > 86400:
        duration_s = None

    # UTC → Europe/Berlin local time
    if _BERLIN_TZ is not None:
        local_dt = (
            start_utc_dt.replace(tzinfo=timezone.utc)
            .astimezone(_BERLIN_TZ)
            .replace(tzinfo=None)
        )
    else:
        # Rough fallback: +2 in summer (Apr–Oct), +1 in winter
        offset_h = 2 if 3 < start_utc_dt.month < 10 else 1
        local_dt = start_utc_dt + timedelta(hours=offset_h)

    start_utc_str   = start_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    start_local_str = local_dt.strftime("%Y-%m-%dT%H:%M:%S")

    # Load companion JSON for activity type and calories
    activity_type = "Lauf"
    calories = None
    json_path = base_dir / rel_path
    if json_path.exists():
        try:
            with open(json_path, encoding="utf-8") as jf:
                j = json.load(jf)
            raw_type = j.get("activity_type", {}).get("name", "Running")
            activity_type = _DAILYMILE_TYPE_MAP.get(raw_type, raw_type)
            calories = _int(str(j.get("calories") or "")) or None
        except Exception:
            pass

    # Synthetic ID from filename  e.g. "activities/activity_9242514.json" → "dailymile_9242514"
    stem = Path(rel_path).stem          # "activity_9242514"
    activity_num = stem.replace("activity_", "")
    garmin_activity_id = f"dailymile_{activity_num}"

    activity_name = title if title else f"{activity_type} {start_local_str[:10]}"

    return {
        "garmin_activity_id": garmin_activity_id,
        "activity_name":      activity_name,
        "activity_type":      activity_type,
        "start_utc":          start_utc_str,
        "start_local":        start_local_str,
        "duration_s":         duration_s,
        "dist_m":             dist_m,
        "calories":           calories,
        "text":               text if text else None,
    }


def import_dailymile_activities(
    dump_dir: Path,
    db_path: Path,
    start_date: datetime | None,
    end_date: datetime | None,
    user_id: int,
    dry_run: bool,
    init_db: bool,
) -> None:
    activities_csv = dump_dir / "activities.csv"
    if not activities_csv.exists():
        sys.exit(f"ERROR: {activities_csv} not found.")

    # --- DB connection ---
    if dry_run:
        if db_path.exists():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            check_prerequisites(conn, user_id)
        else:
            if not init_db:
                sys.exit(
                    f"ERROR: database file not found: {db_path}\n"
                    f"       Run with --init-db to create it on first use."
                )
            conn = sqlite3.connect(":memory:")
            init_schema(conn)
            conn.execute("INSERT OR IGNORE INTO users (id, name) VALUES (?, 'default')", (user_id,))
            conn.commit()
    else:
        if not db_path.exists() and not init_db:
            sys.exit(
                f"ERROR: database file not found: {db_path}\n"
                f"       Run with --init-db to create it on first use."
            )
        conn = sqlite3.connect(db_path)
        if init_db:
            init_schema(conn)
            conn.execute("INSERT OR IGNORE INTO users (id, name) VALUES (?, 'default')", (user_id,))
            conn.commit()
        check_prerequisites(conn, user_id)
        migrate_schema(conn)

    existing_ids: set[str] = set()
    existing_list: list[dict] = []
    cross_claimed_ids: set[int] = set()
    for r in conn.execute(
        "SELECT garmin_activity_id, id, start_time_local, start_time_utc, distance_m, activity_name "
        "FROM activities WHERE user_id = ?",
        (user_id,),
    ):
        existing_ids.add(r[0])
        _dt_local = _parse_ts(r[2])
        existing_list.append({
            "id": r[1], "start_time_local": r[2], "start_time_utc": r[3],
            "distance_m": r[4], "activity_name": r[5],
            "dt_local": _dt_local,
            "date_only": bool(_dt_local and _dt_local.hour == 0 and _dt_local.minute == 0 and _dt_local.second == 0),
        })

    n_new = n_skipped = n_cross_dup = n_date_filtered = n_skipped_invalid = n_parse_error = 0
    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(activities_csv, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        next(reader)  # skip header

        for lineno, row in enumerate(reader, start=2):
            if not row or not row[0].strip():
                continue

            try:
                d = _parse_dailymile_row(row, dump_dir)
            except Exception as exc:
                print(f"  ERROR line {lineno}: parse error — {exc}", file=sys.stderr)
                n_parse_error += 1
                continue

            if d is None:
                n_skipped_invalid += 1
                continue

            # --- date filter ---
            try:
                dt = datetime.fromisoformat(d["start_local"])
            except ValueError:
                print(f"  ERROR line {lineno}: bad date {d['start_local']!r}", file=sys.stderr)
                n_parse_error += 1
                continue
            if start_date and dt < start_date:
                n_date_filtered += 1
                continue
            if end_date and dt > end_date:
                n_date_filtered += 1
                continue

            # --- exact duplicate check ---
            if d["garmin_activity_id"] in existing_ids:
                n_skipped += 1
                continue

            # --- cross-source duplicate check (±2 h local time + distance) ---
            cross_dup = _find_cross_source_duplicate(
                existing_list, cross_claimed_ids,
                d["start_local"], d["dist_m"],
                date_only=False,
                window_h=2.0,
            )
            if cross_dup is not None:
                cross_claimed_ids.add(cross_dup["id"])
                n_cross_dup += 1
                print(
                    f"  CROSS-DUP    {d['activity_name']!r:45s} {d['start_local']}"
                    f"  ≈ {cross_dup.get('activity_name')!r}"
                    f"  dist {d['dist_m']:.0f}m ≈ {cross_dup['distance_m']:.0f}m"
                )
                continue

            if dry_run:
                n_new += 1
                continue

            # --- insert ---
            raw_json = json.dumps(
                {k: v for k, v in {"dailymile_text": d["text"]}.items() if v},
                ensure_ascii=False,
            )
            try:
                conn.execute(
                    """
                    INSERT INTO activities (
                        user_id, garmin_activity_id,
                        activity_name, activity_type, sport_type,
                        start_time_utc, start_time_local,
                        duration_s, elapsed_time_s, moving_time_s,
                        distance_m,
                        calories,
                        raw_json, source, synced_at
                    ) VALUES (
                        ?,?,  ?,?,?,  ?,?,  ?,?,?,  ?,  ?,  ?,?,?
                    )
                    """,
                    (
                        user_id, d["garmin_activity_id"],
                        d["activity_name"], d["activity_type"], d["activity_type"],
                        d["start_utc"], d["start_local"],
                        d["duration_s"], d["duration_s"], d["duration_s"],
                        d["dist_m"],
                        d["calories"],
                        raw_json, "DailyMile-Import", synced_at,
                    ),
                )
                conn.commit()
                existing_ids.add(d["garmin_activity_id"])
                n_new += 1
            except Exception as exc:
                print(
                    f"  ERROR line {lineno} {d['garmin_activity_id']}: {exc}",
                    file=sys.stderr,
                )
                n_parse_error += 1

    conn.close()

    # --- summary ---
    if dry_run:
        print("DRY-RUN — nothing was written.\n")
    print(f"\nDone.")
    print(f"  Imported              : {n_new}")
    print(f"  Skipped (duplicate)   : {n_skipped}  (already in DB, same source)")
    print(f"  Skipped (cross-source): {n_cross_dup}  (same time ±2h + distance already in DB)")
    print(f"  Skipped (invalid)     : {n_skipped_invalid}  (zero/noise distance or bad date)")
    print(f"  Outside dates         : {n_date_filtered}  (filtered out)")
    print(f"  Errors                : {n_parse_error}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_date(s: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"Invalid date '{s}'. Use YYYY-MM-DD or DD.MM.YYYY.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a Strava or Runmeter export into the garmin-sync SQLite database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Import Strava export
  python strava_import.py --dump "Strava Dump 20260310" --db garmin.db --gpx-dest data/gpx

  # Import Runmeter CSV
  python strava_import.py --runmeter runmeter_data/Runmeter_Import.csv --db garmin.db

  # Import Cyclemeter CSV
  python strava_import.py --cyclemeter runmeter_data/Cyclemeter_Import.csv --db garmin.db

  # Import DailyMile export folder
  python strava_import.py --dailymile dailymile_export/dailymile_export_NTQtMjkwODM5 --db garmin.db

  # Dry-run any import
  python strava_import.py --dump "Strava Dump 20260310" --db garmin.db --dry-run
  python strava_import.py --runmeter runmeter_data/Runmeter_Import.csv --db garmin.db --dry-run
  python strava_import.py --cyclemeter runmeter_data/Cyclemeter_Import.csv --db garmin.db --dry-run
  python strava_import.py --dailymile dailymile_export/dailymile_export_NTQtMjkwODM5 --db garmin.db --dry-run
""",
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--dump", metavar="DIR",
        help="Path to the Strava export folder (contains activities.csv and activities/).",
    )
    source_group.add_argument(
        "--runmeter", metavar="FILE",
        help="Path to a Runmeter CSV export file.",
    )
    source_group.add_argument(
        "--cyclemeter", metavar="FILE",
        help="Path to a Cyclemeter CSV export file.",
    )
    source_group.add_argument(
        "--dailymile", metavar="DIR",
        help="Path to a DailyMile export folder (contains activities.csv and activities/).",
    )
    parser.add_argument(
        "--db", required=True, metavar="FILE",
        help="Path to the SQLite database file (created if it does not exist).",
    )
    parser.add_argument(
        "--gpx-dest", metavar="DIR", default=None,
        help="Destination directory for GPX files. If omitted, GPX files are not copied.",
    )
    parser.add_argument(
        "--fit-dest", metavar="DIR", default=None,
        help="Destination directory for FIT/FIT.GZ files. If omitted, FIT files are not copied.",
    )
    parser.add_argument(
        "--start-date", metavar="DATE", type=parse_date, default=None,
        help="Import activities on or after this date (YYYY-MM-DD or DD.MM.YYYY).",
    )
    parser.add_argument(
        "--end-date", metavar="DATE", type=parse_date, default=None,
        help="Import activities on or before this date (YYYY-MM-DD or DD.MM.YYYY).",
    )
    parser.add_argument(
        "--user-id", type=int, default=1, metavar="N",
        help="user_id to assign in the DB (default: 1).",
    )
    parser.add_argument(
        "--init-db", action="store_true",
        help=(
            "Create the 'users' and 'activities' tables if they do not exist, and "
            "insert a default user row for the given --user-id. "
            "By default the script refuses to run if the schema is missing."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Simulate the import without writing anything. Opens the DB read-only "
            "to detect duplicates and GPX collisions, then prints a full summary."
        ),
    )
    parser.add_argument(
        "--backup", action="store_true",
        help=(
            "Create a timestamped backup of the DB file (e.g. garmin.db.bak-20260310T120000) "
            "before writing. Ignored when --dry-run is set."
        ),
    )
    parser.add_argument(
        "--overwrite-gpx", action="store_true",
        help=(
            "Overwrite GPX files that already exist in --gpx-dest. "
            "By default existing files are left untouched and a warning is printed."
        ),
    )

    args = parser.parse_args()

    db_path = Path(args.db)

    # --- DB backup ---
    if args.backup and not args.dry_run:
        if db_path.exists():
            ts = datetime.now().strftime("%Y%m%dT%H%M%S")
            backup_path = db_path.with_suffix(f".db.bak-{ts}")
            shutil.copy2(db_path, backup_path)
            print(f"Backup written to: {backup_path}\n")
        else:
            print("--backup: DB does not exist yet, skipping backup.\n")

    if args.dailymile:
        dump_dir = Path(args.dailymile)
        if not dump_dir.is_dir():
            sys.exit(f"ERROR: DailyMile directory not found: {dump_dir}")
        print(f"DailyMile import")
        print(f"  dir          : {dump_dir}")
        print(f"  db           : {db_path}")
        print(f"  dates        : {args.start_date or 'any'} → {args.end_date or 'any'}")
        print(f"  user_id      : {args.user_id}")
        print(f"  dry-run      : {args.dry_run}")
        print(f"  init-db      : {args.init_db}")
        print()
        import_dailymile_activities(
            dump_dir=dump_dir,
            db_path=db_path,
            start_date=args.start_date,
            end_date=args.end_date,
            user_id=args.user_id,
            dry_run=args.dry_run,
            init_db=args.init_db,
        )
    elif args.runmeter or args.cyclemeter:
        is_cyclemeter = bool(args.cyclemeter)
        csv_path = Path(args.cyclemeter if is_cyclemeter else args.runmeter)
        label = "Cyclemeter" if is_cyclemeter else "Runmeter"
        print(f"{label} import")
        print(f"  csv          : {csv_path}")
        print(f"  db           : {db_path}")
        print(f"  dates        : {args.start_date or 'any'} → {args.end_date or 'any'}")
        print(f"  user_id      : {args.user_id}")
        print(f"  dry-run      : {args.dry_run}")
        print(f"  init-db      : {args.init_db}")
        print()
        fn = import_cyclemeter_activities if is_cyclemeter else import_runmeter_activities
        fn(
            csv_path=csv_path,
            db_path=db_path,
            start_date=args.start_date,
            end_date=args.end_date,
            user_id=args.user_id,
            dry_run=args.dry_run,
            init_db=args.init_db,
        )
    else:
        dump_dir = Path(args.dump)
        if not dump_dir.is_dir():
            sys.exit(f"ERROR: dump directory not found: {dump_dir}")

        gpx_dest = Path(args.gpx_dest) if args.gpx_dest else None
        fit_dest = Path(args.fit_dest) if args.fit_dest else None

        print(f"Strava import")
        print(f"  dump         : {dump_dir}")
        print(f"  db           : {db_path}")
        print(f"  gpx-dest     : {gpx_dest or '(not copying)'}")
        print(f"  fit-dest     : {fit_dest or '(not copying)'}")
        print(f"  dates        : {args.start_date or 'any'} → {args.end_date or 'any'}")
        print(f"  user_id      : {args.user_id}")
        print(f"  dry-run      : {args.dry_run}")
        print(f"  init-db      : {args.init_db}")
        print(f"  backup       : {args.backup}")
        print(f"  overwrite-gpx: {args.overwrite_gpx}")
        print()
        import_activities(
            dump_dir=dump_dir,
            db_path=db_path,
            gpx_dest=gpx_dest,
            fit_dest=fit_dest,
            start_date=args.start_date,
            end_date=args.end_date,
            user_id=args.user_id,
            dry_run=args.dry_run,
            overwrite_gpx=args.overwrite_gpx,
            init_db=args.init_db,
        )


if __name__ == "__main__":
    main()
