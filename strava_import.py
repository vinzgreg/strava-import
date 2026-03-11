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
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET


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
        if not existing.get("fit_path") and fit_dest:
            dest = fit_dest / gpx_src.name
            if not dest.exists() or overwrite:
                if not dry_run:
                    shutil.copy2(gpx_src, dest)
                updates["fit_path"] = str(dest)
                detail_parts.append(f"FIT → {dest.name}")
    else:
        if not existing.get("gpx_path") and gpx_dest:
            dest = gpx_dest / gpx_src.name
            if not dest.exists() or overwrite:
                if not dry_run:
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
        "SELECT garmin_activity_id, id, gpx_path, fit_path, start_time_local, start_time_utc "
        "FROM activities WHERE user_id = ?"
        if _has_fit_path else
        "SELECT garmin_activity_id, id, gpx_path, NULL, start_time_local, start_time_utc "
        "FROM activities WHERE user_id = ?"
    )
    existing_by_id: dict[str, dict] = {}
    existing_by_time: dict[str, dict] = {}
    for r in conn.execute(_select, (user_id,)):
        entry = {"id": r[1], "gpx_path": r[2], "fit_path": r[3]}
        existing_by_id[r[0]] = entry
        # Index by both local and UTC timestamps (normalised) so we match
        # regardless of which one Strava's date_local actually corresponds to.
        for raw_ts in (r[4], r[5]):
            norm = _normalize_ts(raw_ts)
            if norm and norm not in existing_by_time:
                existing_by_time[norm] = entry
    existing_ids: set[str] = set(existing_by_id.keys())

    if not dry_run and gpx_dest:
        gpx_dest.mkdir(parents=True, exist_ok=True)
    if not dry_run and fit_dest:
        fit_dest.mkdir(parents=True, exist_ok=True)

    # Counters
    n_new = n_skipped_complete = n_completed = n_date_filtered = n_gpx_missing = n_parse_error = n_gpx_skipped = 0
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

            # --- duplicate check (by ID, then by normalised start time) ---
            existing = existing_by_id.get(d["garmin_activity_id"])
            if existing is None:
                for ts in (d["start_local"], d["start_utc"]):
                    norm = _normalize_ts(ts)
                    if norm and norm in existing_by_time:
                        existing = existing_by_time[norm]
                        existing_ids.add(d["garmin_activity_id"])
                        break

            if d["garmin_activity_id"] in existing_ids:
                if existing is not None:
                    gpx_rel_dup = d["gpx_rel"]
                    gpx_src_dup = dump_dir / gpx_rel_dup if gpx_rel_dup else None
                    is_fit_dup = (
                        gpx_rel_dup.endswith(".fit") or gpx_rel_dup.endswith(".fit.gz")
                    ) if gpx_rel_dup else False
                    status, detail = _resolve_duplicate(
                        conn, existing, gpx_src_dup, is_fit_dup,
                        gpx_dest, fit_dest, overwrite_gpx, dry_run,
                    )
                    if status == "completed":
                        n_completed += 1
                        msg = (f"COMPLETED    {d['activity_name']!r:50s} {d['start_local']}"
                               f"  ({detail})")
                        print(f"  {msg}")
                        issues.append(msg)
                    else:
                        n_skipped_complete += 1
                        if dry_run:
                            issues.append(
                                f"DUPLICATE    {d['activity_name']!r:50s} {d['start_local']}"
                            )
                else:
                    # inserted during this run — can't look up row, just skip
                    n_skipped_complete += 1
                continue

            # --- activity file (GPX or FIT) ---
            gpx_rel = d["gpx_rel"]
            gpx_src = dump_dir / gpx_rel if gpx_rel else None
            gpx_dest_path: str | None = None
            fit_dest_path: str | None = None
            gpx_will_collide = False
            is_fit = gpx_rel.endswith(".fit") or gpx_rel.endswith(".fit.gz") if gpx_rel else False

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
        print(f"  Would import         : {n_new}")
        print(f"  Skipped (complete)   : {n_skipped_complete}  (already in DB with all files)")
        print(f"  Completed (file added): {n_completed}  (was in DB, missing GPX/FIT added)")
        print(f"  Outside dates        : {n_date_filtered}  (filtered out)")
        print(f"  File missing         : {n_gpx_missing}  (activity imported, no file to copy)")
        print(f"  File name conflict   : {n_gpx_skipped}  (existing file preserved; use --overwrite-gpx to replace)")
        print(f"  Parse errors         : {n_parse_error}  (would skip)")
    else:
        print(f"\nDone.")
        print(f"  Imported             : {n_new}")
        print(f"  Skipped (complete)   : {n_skipped_complete}  (already in DB with all files)")
        print(f"  Completed (file added): {n_completed}  (was in DB, missing GPX/FIT added)")
        print(f"  Outside dates        : {n_date_filtered}  (filtered out)")
        print(f"  File missing         : {n_gpx_missing}")
        print(f"  File name conflict   : {n_gpx_skipped}  (existing file preserved)")
        print(f"  Errors               : {n_parse_error}")


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
        description="Import a Strava data export into the garmin-sync SQLite database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Import everything
  python strava_import.py --dump "Strava Dump 20260310" --db garmin.db --gpx-dest data/gpx

  # Import only 2022–2023, dry-run first
  python strava_import.py --dump "Strava Dump 20260310" --db garmin.db \\
      --gpx-dest data/gpx --start-date 2022-01-01 --end-date 2023-12-31 --dry-run
""",
    )
    parser.add_argument(
        "--dump", required=True, metavar="DIR",
        help="Path to the Strava export folder (contains activities.csv and activities/).",
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

    dump_dir = Path(args.dump)
    if not dump_dir.is_dir():
        sys.exit(f"ERROR: dump directory not found: {dump_dir}")

    db_path  = Path(args.db)
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

    # --- DB backup ---
    if args.backup and not args.dry_run:
        if db_path.exists():
            ts = datetime.now().strftime("%Y%m%dT%H%M%S")
            backup_path = db_path.with_suffix(f".db.bak-{ts}")
            shutil.copy2(db_path, backup_path)
            print(f"Backup written to: {backup_path}\n")
        else:
            print("--backup: DB does not exist yet, skipping backup.\n")

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
