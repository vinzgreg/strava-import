#!/usr/bin/env python3
"""Fix DB path references for Strava-imported FIT/GPX files.

For each activity with a fit_path or gpx_path that does not start with the
container prefix, the script:
  1. Checks if the file already exists at the destination on the host → fixes
     the DB pointer only.
  2. Looks for the file by name in --strava-data → copies it to the
     destination and fixes the DB pointer.
  3. Reports files it cannot locate.

Usage:
    python fix_strava_paths.py \\
        --db        PATH \\
        --strava-data PATH \\
        --fit-dest  DIR  --fit-dest-db  DIR \\
        --gpx-dest  DIR  --gpx-dest-db  DIR \\
        [--dry-run]

Example (typical setup):
    python fix_strava_paths.py \\
        --db          ~/data/garminnostra/garmin_nostra.db \\
        --strava-data ~/Nextcloud/code/Python/sport-import/strava-import/strava_data/activities \\
        --fit-dest    ~/data/garminnostra/fit/vinz \\
        --fit-dest-db /data/fit/vinz \\
        --gpx-dest    ~/data/garminnostra/gpx/vinz \\
        --gpx-dest-db /data/gpx/vinz \\
        --dry-run
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db",           required=True, metavar="FILE", help="SQLite database path")
    parser.add_argument("--strava-data",  default=None,  metavar="DIR",  help="Strava dump activities folder (source of missing files). If omitted, files not already at the destination are reported as NOT FOUND.")
    parser.add_argument("--fit-dest",     required=True, metavar="DIR",  help="Host directory to copy FIT files into")
    parser.add_argument("--fit-dest-db",  required=True, metavar="DIR",  help="Container path prefix for FIT files stored in DB")
    parser.add_argument("--gpx-dest",     required=True, metavar="DIR",  help="Host directory to copy GPX files into")
    parser.add_argument("--gpx-dest-db",  required=True, metavar="DIR",  help="Container path prefix for GPX files stored in DB")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    strava_data = Path(args.strava_data).expanduser() if args.strava_data else None
    fit_dest    = Path(args.fit_dest).expanduser()
    gpx_dest    = Path(args.gpx_dest).expanduser()
    fit_dest_db = Path(args.fit_dest_db)
    gpx_dest_db = Path(args.gpx_dest_db)

    if strava_data and not strava_data.is_dir():
        sys.exit(f"ERROR: --strava-data directory not found: {strava_data}")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    columns = {
        "fit_path": (fit_dest, fit_dest_db),
        "gpx_path": (gpx_dest, gpx_dest_db),
    }

    for col, (dest_host, dest_db) in columns.items():
        # Use the parent of dest_db as the "already a container path" marker so
        # that paths belonging to other users (e.g. /data/fit/Lucie/) are not
        # mistakenly treated as broken host paths.
        container_root = str(dest_db.parent)
        rows = conn.execute(
            f"SELECT id, {col} FROM activities WHERE {col} IS NOT NULL AND {col} NOT LIKE ?",
            (container_root + "/%",),
        ).fetchall()

        if not rows:
            print(f"{col}: nothing to fix")
            continue

        print(f"{col}: {len(rows)} rows with non-container paths")

        copied = pointer_fixed = not_found = errors = 0

        for row in rows:
            filename     = Path(row[col]).name
            dest_path_host = dest_host / filename
            dest_path_db   = str(dest_db / filename)

            # 1. File already at correct destination on host
            if dest_path_host.exists():
                if not args.dry_run:
                    conn.execute(f"UPDATE activities SET {col} = ? WHERE id = ?", (dest_path_db, row["id"]))
                print(f"  POINTER-FIX  {filename}")
                pointer_fixed += 1
                continue

            # 2. Try the path already recorded in the DB, then the strava_data folder
            original = Path(row[col])
            src = None
            if original.exists():
                src = original
            elif strava_data:
                candidate = strava_data / filename
                if candidate.exists():
                    src = candidate
            if src:
                if not args.dry_run:
                    dest_host.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(src, dest_path_host)
                        conn.execute(f"UPDATE activities SET {col} = ? WHERE id = ?", (dest_path_db, row["id"]))
                    except Exception as exc:
                        print(f"  ERROR  {filename}: {exc}", file=sys.stderr)
                        errors += 1
                        continue
                print(f"  COPIED       {filename}  (was: {row[col]})")
                copied += 1
                continue

            # 3. Cannot locate file
            print(f"  NOT FOUND    {filename}  (was: {row[col]})")
            not_found += 1

        if not args.dry_run:
            conn.commit()

        print(f"  → pointer-fixed: {pointer_fixed}, copied: {copied}, not found: {not_found}, errors: {errors}")

    conn.close()
    if args.dry_run:
        print("\nDRY-RUN — nothing written.")


if __name__ == "__main__":
    main()
