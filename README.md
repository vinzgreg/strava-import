# strava_import.py

A command-line tool that imports activities from a **Strava data export** into the
[garmin-sync](../garmin-sync/) SQLite database.

---

## What it does

1. Reads `activities.csv` from your Strava export folder.
2. Filters activities by an optional date range.
3. Maps Strava CSV fields to the `activities` table (see schema below).
4. Detects duplicates both by ID (`strava_<id>`) and by matching start
   timestamp against existing rows — so re-running never creates duplicates.
   If a duplicate is found but its GPX or FIT file path is missing in the DB,
   the file is copied and the row is updated automatically.
5. Copies GPX files to `--gpx-dest` and FIT/FIT.GZ files to `--fit-dest`.
6. Stores `source = 'Strava-Import'` on every inserted row.

---

## Requirements

Python 3.10 or newer (uses `datetime.fromisoformat` and `float | None` union
syntax). No third-party packages are required — only the Python standard
library is used.

---

## Installation

```bash
# Clone or copy this file alongside your garmin-sync project.
# No pip install needed.
python3 strava_import.py --help
```

---

## Obtaining a Strava export

1. Log in to Strava → **Settings → My Account → Download or Delete Your Account**.
2. Click **Request Your Archive** and wait for the e-mail.
3. Unzip the archive; you will have a folder like `Strava Dump 20260310/` that
   contains `activities.csv` and an `activities/` sub-folder with GPX and/or
   FIT files.

---

## Usage

```
python3 strava_import.py [OPTIONS]
```

### Required options

| Option | Description |
|---|---|
| `--dump DIR` | Path to the Strava export folder (the one containing `activities.csv`). |
| `--db FILE` | SQLite database file. |

### Optional options

| Option | Default | Description |
|---|---|---|
| `--gpx-dest DIR` | *(not set)* | Directory to copy GPX files into. Skipped if omitted. |
| `--fit-dest DIR` | *(not set)* | Directory to copy FIT/FIT.GZ files into. Skipped if omitted. |
| `--start-date DATE` | *(none)* | Only import activities on or after this date. Accepts `YYYY-MM-DD` or `DD.MM.YYYY`. |
| `--end-date DATE` | *(none)* | Only import activities on or before this date. Same formats. |
| `--user-id N` | `1` | `user_id` assigned to every imported row. Must already exist in the `users` table (see `--init-db`). |
| `--init-db` | *(off)* | Create the `users` and `activities` tables if they do not exist, and insert a default user row for the given `--user-id`. Also runs schema migrations (adds `fit_path`, `source` columns if missing). |
| `--dry-run` | *(off)* | Simulate the import without touching the DB or copying files. Opens the DB **read-only** to detect duplicates and file collisions, then prints a full issues list and summary. Safe to run against a live database. |
| `--backup` | *(off)* | Write a timestamped copy of the DB (e.g. `garmin.db.bak-20260310T120000`) before any changes. Ignored when `--dry-run` is set. |
| `--overwrite-gpx` | *(off)* | Overwrite GPX/FIT files that already exist in the destination. By default existing files are left untouched and flagged as a warning. |

---

## Examples

**Preview before importing (dry-run — DB is opened read-only, nothing is written):**
```bash
python3 strava_import.py \
    --dump "Strava Dump 20260310" \
    --db garmin.db \
    --gpx-dest data/gpx \
    --fit-dest data/fit \
    --dry-run
```

**Import everything, with a DB backup taken first:**
```bash
python3 strava_import.py \
    --dump "Strava Dump 20260310" \
    --db garmin.db \
    --gpx-dest data/gpx \
    --fit-dest data/fit \
    --backup
```

**Import only activities from 2022 and 2023:**
```bash
python3 strava_import.py \
    --dump "Strava Dump 20260310" \
    --db garmin.db \
    --gpx-dest data/gpx \
    --fit-dest data/fit \
    --start-date 2022-01-01 \
    --end-date 2023-12-31
```

**Import into a multi-user DB for user 3:**
```bash
python3 strava_import.py \
    --dump "Strava Dump 20260310" \
    --db garmin.db \
    --user-id 3
```

---

## Database schema

The script targets the garmin-sync schema. If the tables do not yet exist,
`--init-db` creates them. Running `--init-db` on an existing database is safe —
it uses `CREATE TABLE IF NOT EXISTS` and auto-migrates missing columns
(`fit_path`, `source`) via `ALTER TABLE ADD COLUMN`.

### Strava → DB field mapping

| DB column | Strava CSV column |
|---|---|
| `garmin_activity_id` | `"strava_" + Aktivitäts-ID` |
| `activity_name` | Name der Aktivität |
| `activity_type` / `sport_type` | Aktivitätsart |
| `source` | Always `'Strava-Import'` |
| `start_time_local` | Aktivitätsdatum (DD.MM.YYYY, HH:MM:SS → ISO 8601) — always populated |
| `start_time_utc` | Startzeit — only present for newer activities; empty for older exports |
| `elapsed_time_s` / `duration_s` | Verstrichene Zeit (seconds, 2nd column) |
| `moving_time_s` | Bewegungszeit |
| `distance_m` | Distanz (meters, 2nd column) |
| `elevation_gain_m` | Höhenzunahme |
| `elevation_loss_m` | Höhenunterschied |
| `min_elevation_m` | Min. Höhe |
| `max_elevation_m` | Max. Höhe |
| `avg_speed_ms` | Durchschnittliche Geschwindigkeit |
| `max_speed_ms` | Höchstgeschw. |
| `avg_hr` | Durchschnittliche Herzfrequenz |
| `max_hr` | Max. Herzfrequenz (2nd column) |
| `avg_power_w` | Durchschnittliche Watt |
| `max_power_w` | Max. Watt |
| `normalized_power_w` | Gewichtete durchschnittliche Leistung |
| `avg_cadence` | Durchschnittliche Trittfrequenz |
| `max_cadence` | Max. Tritt-/Schrittfrequenz |
| `calories` | Kalorien |
| `avg_temperature_c` | Durchschnittliche Temperatur |
| `max_temperature_c` | Max. Temperatur |
| `training_stress_score` | Trainingsbelastung |
| `intensity_factor` | Leistungszahl |
| `steps` | Schritte insgesamt |
| `start_lat` / `start_lon` | Extracted from first GPX trackpoint |
| `gpx_path` | Absolute path of the copied GPX file |
| `fit_path` | Absolute path of the copied FIT/FIT.GZ file |
| `raw_json` | JSON with `strava_activity_id` and `strava_description` |
| `synced_at` | UTC timestamp of the import run |
| `caldav_pushed` | Always `0` (not yet pushed) |
| `mastodon_posted` | Always `0` (not yet posted) |

> **Note on duplicate column names in activities.csv**
> Strava's CSV contains some column names twice (e.g. *Verstrichene Zeit*,
> *Distanz*, *Max. Herzfrequenz*). The script uses hard-coded zero-based column
> indices rather than header names to always pick the correct value.

---

## Duplicate detection

Duplicates are detected in two ways:

1. **By ID** — `garmin_activity_id = strava_<id>` already exists in the DB.
2. **By timestamp** — the activity's `start_time_local` or `start_time_utc`
   (normalised to `YYYY-MM-DDTHH:MM:SS`) matches any existing row's local or
   UTC timestamp. This catches activities already imported from Garmin that
   overlap with a Strava export.

When a duplicate is found, the script checks whether the existing DB row is
missing a `gpx_path` or `fit_path` that we have on disk. If so, the file is
copied and the row is updated. The summary reports:

- **Skipped (complete)** — duplicate found, all file paths already present.
- **Completed (file added)** — duplicate found, missing file path(s) filled in.

---

## Notes

- **Idempotent**: Running the script multiple times on the same dump is safe.
- **German locale**: The summary distance column uses a comma as the decimal
  separator (`"9,55"`) but the detailed columns use a period — the script
  always reads the period-separated detailed values.
- **No external dependencies**: Standard-library only (`csv`, `json`,
  `sqlite3`, `shutil`, `xml.etree.ElementTree`, `argparse`).
