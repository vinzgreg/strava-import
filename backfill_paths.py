#!/usr/bin/env python3
"""Backfill missing fit_path / gpx_path pointers in the activities DB.

For each activity (per user) with a NULL fit_path or gpx_path, the script
builds an index of files in the user's subdirectory and tries to match by
garmin_activity_id or DB id.  If a match is found the DB pointer is updated.

Filenames are matched by stem only; extensions tried for FIT: .fit, .fit.gz;
for GPX: .gpx.

Usage:
    python backfill_paths.py \\
        --db        PATH \\
        --fit-dir   HOST_DIR  --fit-dir-db  CONTAINER_DIR \\
        --gpx-dir   HOST_DIR  --gpx-dir-db  CONTAINER_DIR \\
        [--user     NAME]     \\
        [--dry-run]

Example:
    python backfill_paths.py \\
        --db          ~/data/garminnostra/garmin_nostra.db \\
        --fit-dir     ~/data/garminnostra/fit \\
        --fit-dir-db  /data/fit \\
        --gpx-dir     ~/data/garminnostra/gpx \\
        --gpx-dir-db  /data/gpx \\
        --user        vinz \\
        --dry-run
"""

import argparse
import sqlite3
import sys
from pathlib import Path


FIT_SUFFIXES = (".fit.gz", ".fit")
GPX_SUFFIXES = (".gpx",)


def stem_of(filename: str, suffixes: tuple[str, ...]) -> str:
    """Strip the longest matching suffix and return the bare stem."""
    for s in suffixes:
        if filename.endswith(s):
            return filename[: -len(s)]
    return filename


def build_index(directory: Path, suffixes: tuple[str, ...]) -> dict[str, Path]:
    """Map bare stem → Path for every file with a matching suffix."""
    index: dict[str, Path] = {}
    if not directory.is_dir():
        return index
    for f in directory.iterdir():
        if not f.is_file():
            continue
        for s in suffixes:
            if f.name.endswith(s):
                index[stem_of(f.name, suffixes)] = f
                break
    return index


def process_column(
    conn: sqlite3.Connection,
    col: str,
    suffixes: tuple[str, ...],
    host_base: Path,
    db_base: Path,
    users: list[tuple[int, str]],
    dry_run: bool,
) -> None:
    found = updated = 0

    for user_id, user_name in users:
        user_host_dir = host_base / user_name
        index = build_index(user_host_dir, suffixes)
        if not index:
            print(f"  {col}: no files found in {user_host_dir}")
            continue

        rows = conn.execute(
            f"SELECT id, garmin_activity_id FROM activities "
            f"WHERE user_id = ? AND {col} IS NULL",
            (user_id,),
        ).fetchall()

        if not rows:
            print(f"  {col} [{user_name}]: nothing to backfill")
            continue

        print(f"  {col} [{user_name}]: {len(rows)} activities with NULL path, {len(index)} files indexed")

        for row in rows:
            gaid = str(row["garmin_activity_id"])
            # garmin_activity_id may carry a source prefix (e.g. "strava_123",
            # "cyclemeter_abc"); files are named after the bare ID after the "_".
            bare_gaid = gaid.split("_", 1)[-1] if "_" in gaid else gaid
            match = (
                index.get(gaid)
                or index.get(bare_gaid)
                or index.get(str(row["id"]))
            )
            if not match:
                continue
            found += 1
            db_path = str(db_base / user_name / match.name)
            if not dry_run:
                conn.execute(
                    f"UPDATE activities SET {col} = ? WHERE id = ?",
                    (db_path, row["id"]),
                )
            print(f"    {'DRY ' if dry_run else ''}UPDATE  id={row['id']}  {col} → {db_path}")
            updated += 1

    print(f"  {col}: {found} matches found, {updated} {'would be ' if dry_run else ''}updated")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db",          required=True, metavar="FILE")
    parser.add_argument("--fit-dir",     required=True, metavar="DIR",  help="Host base dir for FIT files (subdirs per user)")
    parser.add_argument("--fit-dir-db",  required=True, metavar="DIR",  help="Container base dir for FIT files stored in DB")
    parser.add_argument("--gpx-dir",     required=True, metavar="DIR",  help="Host base dir for GPX files (subdirs per user)")
    parser.add_argument("--gpx-dir-db",  required=True, metavar="DIR",  help="Container base dir for GPX files stored in DB")
    parser.add_argument("--user",        default=None,  metavar="NAME", help="Restrict to this user (default: all users)")
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()

    fit_dir    = Path(args.fit_dir).expanduser()
    gpx_dir    = Path(args.gpx_dir).expanduser()
    fit_dir_db = Path(args.fit_dir_db)
    gpx_dir_db = Path(args.gpx_dir_db)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    if args.user:
        row = conn.execute("SELECT id, name FROM users WHERE name = ?", (args.user,)).fetchone()
        if not row:
            sys.exit(f"ERROR: user '{args.user}' not found in DB")
        users = [(row["id"], row["name"])]
    else:
        users = [(r["id"], r["name"]) for r in conn.execute("SELECT id, name FROM users").fetchall()]

    if args.dry_run:
        print("DRY-RUN — nothing will be written\n")

    process_column(conn, "fit_path", FIT_SUFFIXES, fit_dir, fit_dir_db, users, args.dry_run)
    print()
    process_column(conn, "gpx_path", GPX_SUFFIXES, gpx_dir, gpx_dir_db, users, args.dry_run)

    if not args.dry_run:
        conn.commit()
    conn.close()

    if args.dry_run:
        print("\nDRY-RUN — nothing written.")


if __name__ == "__main__":
    main()
