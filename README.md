# sport-import

A collection of tools for importing and visualising sports activities in the garmin-sync SQLite database.

| Component | File | Purpose |
|---|---|---|
| **Importer** | `strava_import.py` | One-off CLI tool: reads a Strava export and populates the DB |
| **Dashboard** | `web_dashboard.py` | Always-on Flask web app: visualises training trends |

---

## Part 1 — strava_import.py

A command-line tool that imports activities from a **Strava data export** into the
[garmin-sync](../garmin-sync/) SQLite database.

### What it does

1. Reads `activities.csv` from your Strava export folder.
2. Filters activities by an optional date range.
3. Maps Strava CSV fields to the `activities` table (see schema below).
4. Detects duplicates both by ID (`strava_<id>`) and by matching start
   timestamp against existing rows — so re-running never creates duplicates.
   If a duplicate is found but its GPX or FIT file path is missing in the DB,
   the file is copied and the row is updated automatically.
5. Copies GPX files to `--gpx-dest` and FIT/FIT.GZ files to `--fit-dest`.
6. Stores `source = 'Strava-Import'` on every inserted row.

### Requirements

Python 3.10 or newer. No third-party packages — standard library only.

### Obtaining a Strava export

1. Log in to Strava → **Settings → My Account → Download or Delete Your Account**.
2. Click **Request Your Archive** and wait for the e-mail.
3. Unzip the archive; you will have a folder like `Strava Dump 20260310/` that
   contains `activities.csv` and an `activities/` sub-folder with GPX and/or FIT files.

### Usage

```
python3 strava_import.py [OPTIONS]
```

#### Required options

| Option | Description |
|---|---|
| `--dump DIR` | Path to the Strava export folder (the one containing `activities.csv`). |
| `--db FILE` | SQLite database file. |

#### Optional options

| Option | Default | Description |
|---|---|---|
| `--gpx-dest DIR` | *(not set)* | Directory to copy GPX files into. Skipped if omitted. |
| `--fit-dest DIR` | *(not set)* | Directory to copy FIT/FIT.GZ files into. Skipped if omitted. |
| `--start-date DATE` | *(none)* | Only import activities on or after this date. Accepts `YYYY-MM-DD` or `DD.MM.YYYY`. |
| `--end-date DATE` | *(none)* | Only import activities on or before this date. Same formats. |
| `--user-id N` | `1` | `user_id` assigned to every imported row. Must already exist in the `users` table (see `--init-db`). |
| `--init-db` | *(off)* | Create the `users` and `activities` tables if they do not exist, and insert a default user row for the given `--user-id`. Also runs schema migrations (`fit_path`, `source` columns) via `ALTER TABLE ADD COLUMN`. |
| `--dry-run` | *(off)* | Simulate the import without touching the DB or copying files. Opens the DB **read-only**, detects duplicates and file collisions, then prints a full summary. Safe to run against a live database. |
| `--backup` | *(off)* | Write a timestamped copy of the DB before any changes. Ignored when `--dry-run` is set. |
| `--overwrite-gpx` | *(off)* | Overwrite GPX/FIT files that already exist in the destination. By default existing files are left untouched. |

### Examples

**Preview before importing (dry-run):**
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

### Duplicate detection

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

### Database schema

Running `--init-db` on an existing database is safe — it uses
`CREATE TABLE IF NOT EXISTS` and auto-migrates any missing columns.

#### Strava → DB field mapping

| DB column | Strava CSV column |
|---|---|
| `garmin_activity_id` | `"strava_" + Aktivitäts-ID` |
| `activity_name` | Name der Aktivität |
| `activity_type` / `sport_type` | Aktivitätsart |
| `source` | Always `'Strava-Import'` |
| `start_time_local` | Aktivitätsdatum (DD.MM.YYYY, HH:MM:SS → ISO 8601) — always populated |
| `start_time_utc` | Startzeit — only present for newer activities |
| `elapsed_time_s` / `duration_s` | Verstrichene Zeit (seconds, 2nd column) |
| `moving_time_s` | Bewegungszeit |
| `distance_m` | Distanz (meters, 2nd column) |
| `elevation_gain_m` / `elevation_loss_m` | Höhenzunahme / Höhenunterschied |
| `min_elevation_m` / `max_elevation_m` | Min. / Max. Höhe |
| `avg_speed_ms` / `max_speed_ms` | Geschwindigkeit |
| `avg_hr` / `max_hr` | Herzfrequenz |
| `avg_power_w` / `max_power_w` / `normalized_power_w` | Watt |
| `avg_cadence` / `max_cadence` | Trittfrequenz |
| `calories` | Kalorien |
| `avg_temperature_c` / `max_temperature_c` | Temperatur |
| `training_stress_score` / `intensity_factor` | TSS / IF |
| `steps` | Schritte insgesamt |
| `start_lat` / `start_lon` | Extracted from first GPX trackpoint |
| `gpx_path` | Absolute path of the copied GPX file |
| `fit_path` | Absolute path of the copied FIT/FIT.GZ file |
| `raw_json` | JSON with `strava_activity_id` and `strava_description` |
| `synced_at` | UTC timestamp of the import run |

### Notes

- **Idempotent**: Running the script multiple times on the same dump is safe.
- **German locale**: The summary distance column uses a comma decimal separator
  (`"9,55"`); the script always reads the period-separated detailed columns.
- **No external dependencies**: standard library only.

---

## Part 2 — web_dashboard.py (Activity Dashboard)

A responsive Flask web app for visualising long-term training trends from the
garmin-sync SQLite database.

### Features

- **Multi-user** — select any user via dropdown
- **Flexible activity types** — multi-select, handles German/English type names
- **Smart metric mode** — pace (min/km) for running, speed (km/h) for cycling; elevation chart only for cycling
- **Three time granularities** — week, month, year
- **Year filter** — include any subset of available years
- **Charts** — distance + activity count · pace or speed (avg, best, trend) · elevation (cycling)
- **Stat cards** — total distance, activity count, avg and best pace/speed
- **Mastodon export** — formatted text block + downloadable 1080×1080 social card PNG

### Installation (Docker — recommended for servers)

The dashboard is designed to run in Docker behind an nginx reverse proxy.
The container binds only to `127.0.0.1` so it is never directly reachable
from the internet.

**1. Clone the repository on your server:**
```bash
git clone https://github.com/vinzgreg/strava-import.git
cd strava-import
```

**2. Adjust the DB path in `docker-compose.yml`** if your database lives
somewhere other than `/home/vinz/data/garminnostra/garmin_nostra.db`:
```yaml
volumes:
  - /your/path/to/garmin_nostra.db:/data/garmin_nostra.db:ro
```

**3. Start the container:**
```bash
docker compose up -d
```

The dashboard is now running on `http://127.0.0.1:5050` (host-only).

**4. Add an nginx server block** to expose it under a domain:
```nginx
server {
    listen 443 ssl;
    server_name stats.yourdomain.com;

    # Restrict to your IP address (recommended)
    allow 1.2.3.4;
    deny all;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Alternatively, replace the IP restriction with HTTP basic auth:
```nginx
    auth_basic "Sports Dashboard";
    auth_basic_user_file /etc/nginx/.htpasswd;
```

**5. Reload nginx:**
```bash
sudo nginx -t && sudo systemctl reload nginx
```

### Running locally (without Docker)

```bash
pip install -r requirements.txt
python3 web_dashboard.py
# → http://localhost:5000
```

The `DB_PATH` environment variable overrides the default database location:
```bash
DB_PATH=/path/to/garmin_nostra.db python3 web_dashboard.py
```

### Defaults on load

| Setting | Default |
|---|---|
| User | id=1 |
| Activity types | All types containing "run" or "lauf" |
| Group by | Month |
| Years | All available years |

---

## License

MIT — see [LICENSE](LICENSE).
