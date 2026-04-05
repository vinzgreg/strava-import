#!/usr/bin/env python3
"""Fix host-path → container-path in activities DB.

For each path column, finds rows where the value starts with /home/vinz/data,
checks the file actually exists on the host, and rewrites it to the container
path (/data/...).

Usage:
    python fix_paths.py --db ~/data/garminnostra/garmin_nostra.db [--dry-run]
"""

import argparse
import sqlite3
from pathlib import Path

HOST_PREFIX      = "/home/vinz/data/garminnostra/"
CONTAINER_PREFIX = "/data/"

PATH_COLUMNS = ("fit_path", "gpx_path")


def host_to_container(path: str) -> str:
    return CONTAINER_PREFIX + path[len(HOST_PREFIX):]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    for col in PATH_COLUMNS:
        rows = conn.execute(
            f"SELECT id, {col} FROM activities WHERE {col} LIKE ?",
            (HOST_PREFIX + "%",),
        ).fetchall()

        if not rows:
            print(f"{col}: nothing to fix")
            continue

        updated = skipped_missing = 0
        for row in rows:
            host_path = Path(row[col])
            if not host_path.exists():
                print(f"  MISSING  {host_path}")
                skipped_missing += 1
                continue

            new_path = host_to_container(row[col])
            if not args.dry_run:
                conn.execute(f"UPDATE activities SET {col} = ? WHERE id = ?", (new_path, row["id"]))
            else:
                print(f"  {col}  {row[col]}  →  {new_path}")
            updated += 1

        if not args.dry_run:
            conn.commit()

        print(f"{col}: {updated} updated, {skipped_missing} skipped (file not found on host)")

    conn.close()
    if args.dry_run:
        print("\nDRY-RUN — nothing written.")


if __name__ == "__main__":
    main()
