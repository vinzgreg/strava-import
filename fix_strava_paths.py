#!/usr/bin/env python3
"""Fix DB references to Strava dump files that were never moved to the data store.

Finds activities whose fit_path or gpx_path still points into the Strava dump
folder (~/bin/sport_import/strava-import/strava_data/), copies the file to the
correct destination directory on the host, and rewrites the DB path to the
container-internal path.

Usage:
    python fix_strava_paths.py --db ~/data/garminnostra/garmin_nostra.db [--dry-run]

Options:
    --fit-dest    Host directory to copy FIT files to  (default: ~/data/garminnostra/fit/vinz)
    --gpx-dest    Host directory to copy GPX files to  (default: ~/data/garminnostra/gpx/vinz)
    --fit-dest-db Container path prefix for FIT files  (default: /data/fit/vinz)
    --gpx-dest-db Container path prefix for GPX files  (default: /data/gpx/vinz)
    --dry-run     Report what would happen without writing anything
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

STRAVA_PREFIX = "/home/vinz/bin/sport_import/strava-import/strava_data/"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--fit-dest",    required=True, metavar="DIR",
                        help="Host directory to copy FIT files into.")
    parser.add_argument("--gpx-dest",    required=True, metavar="DIR",
                        help="Host directory to copy GPX files into.")
    parser.add_argument("--fit-dest-db", required=True, metavar="DIR",
                        help="Path prefix stored in the DB for FIT files (container path).")
    parser.add_argument("--gpx-dest-db", required=True, metavar="DIR",
                        help="Path prefix stored in the DB for GPX files (container path).")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    fit_dest    = Path(args.fit_dest).expanduser()
    gpx_dest    = Path(args.gpx_dest).expanduser()
    fit_dest_db = Path(args.fit_dest_db)
    gpx_dest_db = Path(args.gpx_dest_db)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    columns = {
        "fit_path": (fit_dest, fit_dest_db),
        "gpx_path": (gpx_dest, gpx_dest_db),
    }

    for col, (dest_host, dest_db) in columns.items():
        rows = conn.execute(
            f"SELECT id, {col} FROM activities WHERE {col} LIKE ?",
            (STRAVA_PREFIX + "%",),
        ).fetchall()

        if not rows:
            print(f"{col}: nothing to fix")
            continue

        print(f"{col}: {len(rows)} rows to process")

        copied = already_there = missing_source = errors = 0

        for row in rows:
            src = Path(row[col])
            filename = src.name
            dest_path_host = dest_host / filename
            dest_path_db   = str(dest_db / filename)

            if not src.exists():
                print(f"  SOURCE MISSING  {src}")
                missing_source += 1
                continue

            if dest_path_host.exists():
                # File already at destination — just fix the DB pointer
                if not args.dry_run:
                    conn.execute(f"UPDATE activities SET {col} = ? WHERE id = ?", (dest_path_db, row["id"]))
                print(f"  POINTER-FIX   {filename}  →  {dest_path_db}")
                already_there += 1
                continue

            # Copy and update
            if not args.dry_run:
                dest_host.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src, dest_path_host)
                    conn.execute(f"UPDATE activities SET {col} = ? WHERE id = ?", (dest_path_db, row["id"]))
                except Exception as exc:
                    print(f"  ERROR  {filename}: {exc}", file=sys.stderr)
                    errors += 1
                    continue
            print(f"  COPIED        {src.name}  →  {dest_path_db}")
            copied += 1

        if not args.dry_run:
            conn.commit()

        print(f"  → copied: {copied}, pointer-fixed: {already_there}, source missing: {missing_source}, errors: {errors}")

    conn.close()
    if args.dry_run:
        print("\nDRY-RUN — nothing written.")


if __name__ == "__main__":
    main()
