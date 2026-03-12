# sport-import

A collection of tools for importing and visualising sports activities in the garmin-sync SQLite database.

| Component | File | Purpose |
|---|---|---|
| **Importer** | `strava_import.py` | CLI tool: imports activities from multiple sources into the DB |
| **Dashboard** | `web_dashboard.py` | Always-on Flask web app: visualises training trends |

---

## Part 1 — strava_import.py

A command-line tool that imports activities from several export formats into the
[garmin-sync](../garmin-sync/) SQLite database. Exactly one import mode must be
specified per run.

### Import modes

| Mode flag | Source | Input |
|---|---|---|
| `--dump DIR` | **Strava** export | Folder containing `activities.csv` + `activities/` sub-folder |
| `--runmeter FILE` | **Runmeter** CSV | Semicolon-separated summary CSV (one row per activity) |
| `--cyclemeter FILE` | **Cyclemeter** CSV | Semicolon-separated CSV (one row per activity, full timestamp) |
| `--dailymile DIR` | **DailyMile** export | Folder containing `dailymile_export.csv` + per-activity JSON files |

### Requirements

Python 3.10 or newer. No third-party packages — standard library only.
`zoneinfo` (stdlib ≥ 3.9) is used for UTC → Europe/Berlin conversion in the DailyMile importer.

---

### Mode: Strava (`--dump`)

#### Obtaining a Strava export

1. Log in to Strava → **Settings → My Account → Download or Delete Your Account**.
2. Click **Request Your Archive** and wait for the e-mail.
3. Unzip the archive; you will have a folder like `Strava Dump 20260310/` that
   contains `activities.csv` and an `activities/` sub-folder with GPX and/or FIT files.

#### Usage

```bash
python3 strava_import.py --dump DIR --db FILE [OPTIONS]
```

#### Options

| Option | Default | Description |
|---|---|---|
| `--dump DIR` | *(required)* | Path to the Strava export folder. |
| `--db FILE` | *(required)* | SQLite database file. |
| `--gpx-dest DIR` | *(not set)* | Directory to copy GPX files into. |
| `--fit-dest DIR` | *(not set)* | Directory to copy FIT/FIT.GZ files into. |
| `--start-date DATE` | *(none)* | Only import activities on or after this date (`YYYY-MM-DD` or `DD.MM.YYYY`). |
| `--end-date DATE` | *(none)* | Only import activities on or before this date. Same formats. |
| `--user-id N` | `1` | `user_id` assigned to every imported row. |
| `--init-db` | *(off)* | Create tables if missing and run schema migrations. |
| `--dry-run` | *(off)* | Simulate without touching the DB. |
| `--backup` | *(off)* | Write a timestamped DB backup before any changes. |
| `--overwrite-gpx` | *(off)* | Overwrite GPX/FIT files that already exist in the destination. |

#### Strava → DB field mapping

| DB column | Strava CSV column |
|---|---|
| `garmin_activity_id` | `"strava_" + Aktivitäts-ID` |
| `activity_name` | Name der Aktivität |
| `activity_type` / `sport_type` | Aktivitätsart |
| `source` | Always `'Strava-Import'` |
| `start_time_local` | Aktivitätsdatum (DD.MM.YYYY, HH:MM:SS → ISO 8601) |
| `start_time_utc` | Startzeit |
| `elapsed_time_s` / `duration_s` | Verstrichene Zeit |
| `moving_time_s` | Bewegungszeit |
| `distance_m` | Distanz (metres) |
| `elevation_gain_m` / `elevation_loss_m` | Höhenzunahme / Höhenunterschied |
| `avg_speed_ms` / `max_speed_ms` | Geschwindigkeit |
| `avg_hr` / `max_hr` | Herzfrequenz |
| `avg_power_w` / `max_power_w` / `normalized_power_w` | Watt |
| `avg_cadence` / `max_cadence` | Trittfrequenz |
| `calories` | Kalorien |
| `training_stress_score` / `intensity_factor` | TSS / IF |
| `steps` | Schritte insgesamt |
| `start_lat` / `start_lon` | Extracted from first GPX trackpoint |
| `gpx_path` / `fit_path` | Absolute path of the copied file |
| `raw_json` | `{"strava_activity_id": …, "strava_description": …}` |

#### Examples

```bash
# Dry-run preview
python3 strava_import.py \
    --dump "Strava Dump 20260310" \
    --db garmin.db \
    --gpx-dest data/gpx --fit-dest data/fit \
    --dry-run

# Full import with backup
python3 strava_import.py \
    --dump "Strava Dump 20260310" \
    --db garmin.db \
    --gpx-dest data/gpx --fit-dest data/fit \
    --backup
```

---

### Mode: Runmeter (`--runmeter`)

Imports activities from a **Runmeter** summary CSV export. The file uses
semicolons as delimiters and German decimal commas (e.g. `"8,80"`).
Timestamps are date-only (`YYYY-MM-DD`); the script stores them as midnight
local time.

#### Usage

```bash
python3 strava_import.py --runmeter FILE --db FILE [OPTIONS]
```

#### Options

| Option | Default | Description |
|---|---|---|
| `--runmeter FILE` | *(required)* | Path to the Runmeter CSV file. |
| `--db FILE` | *(required)* | SQLite database file. |
| `--user-id N` | `1` | `user_id` assigned to every imported row. |
| `--dry-run` | *(off)* | Simulate without touching the DB. |
| `--backup` | *(off)* | Write a timestamped DB backup before any changes. |

#### Field mapping

| DB column | Runmeter CSV column |
|---|---|
| `garmin_activity_id` | `"runmeter_<date>_<type>"` (synthetic) |
| `activity_type` / `sport_type` | Derived from activity label |
| `source` | Always `'Runmeter'` |
| `start_time_local` | Date column (midnight, `YYYY-MM-DDT00:00:00`) |
| `distance_m` | Distance (km × 1000) |
| `duration_s` | Duration |
| `avg_speed_ms` / `max_speed_ms` | Average / max speed |
| `avg_hr` / `max_hr` | Heart rate |
| `avg_cadence` / `max_cadence` | Cadence (step or pedal depending on type) |
| `elevation_gain_m` / `elevation_loss_m` | Elevation |
| `calories` | Calories |
| `raw_json` | `{"runmeter_count": N}` when a row aggregates multiple activities |

#### Notes

- Rows with zero distance are skipped.
- Activities already in the DB from another source are detected by matching
  calendar date and distance (±5 %).

#### Example

```bash
python3 strava_import.py \
    --runmeter runmeter_data/Runmeter_Import.csv \
    --db ~/data/garmin_nostra.db \
    --backup
```

---

### Mode: Cyclemeter (`--cyclemeter`)

Imports cycling activities from a **Cyclemeter** summary CSV export. The format
is similar to Runmeter but includes full datetime timestamps, route names, pause
time, and bike name.

#### Usage

```bash
python3 strava_import.py --cyclemeter FILE --db FILE [OPTIONS]
```

#### Options

| Option | Default | Description |
|---|---|---|
| `--cyclemeter FILE` | *(required)* | Path to the Cyclemeter CSV file. |
| `--db FILE` | *(required)* | SQLite database file. |
| `--user-id N` | `1` | `user_id` assigned to every imported row. |
| `--dry-run` | *(off)* | Simulate without touching the DB. |
| `--backup` | *(off)* | Write a timestamped DB backup before any changes. |

#### Field mapping

| DB column | Cyclemeter CSV column |
|---|---|
| `garmin_activity_id` | `"cyclemeter_<timestamp_slug>"` (synthetic) |
| `activity_name` | Route name |
| `activity_type` / `sport_type` | Always `'Fahrrad'` |
| `source` | Always `'Cyclemeter'` |
| `start_time_local` | Full datetime column (ISO 8601) |
| `distance_m` | Distance (km × 1000) |
| `duration_s` / `elapsed_time_s` | Duration / elapsed time |
| `moving_time_s` | Duration minus pause time |
| `avg_speed_ms` / `max_speed_ms` | Speed |
| `avg_hr` / `max_hr` | Heart rate |
| `avg_cadence` / `max_cadence` | Cadence |
| `elevation_gain_m` / `elevation_loss_m` | Elevation |
| `calories` | Calories |
| `raw_json` | `{"bike": "…"}` |

#### Notes

- Rows with zero distance or unparseable timestamps are skipped silently.
- Cross-source duplicate detection uses a ±2 h time window plus ±5 % distance.

#### Example

```bash
python3 strava_import.py \
    --cyclemeter runmeter_data/Cyclemeter_Import.csv \
    --db ~/data/garmin_nostra.db \
    --backup
```

---

### Mode: DailyMile (`--dailymile`)

Imports activities from a **DailyMile** export folder. The folder must contain
`dailymile_export.csv` and individual JSON files named `<id>.json` (one per
activity). UTC timestamps in the CSV are converted to Europe/Berlin local time
(DST-aware).

#### Usage

```bash
python3 strava_import.py --dailymile DIR --db FILE [OPTIONS]
```

#### Options

| Option | Default | Description |
|---|---|---|
| `--dailymile DIR` | *(required)* | Path to the DailyMile export folder. |
| `--db FILE` | *(required)* | SQLite database file. |
| `--user-id N` | `1` | `user_id` assigned to every imported row. |
| `--dry-run` | *(off)* | Simulate without touching the DB. |
| `--backup` | *(off)* | Write a timestamped DB backup before any changes. |

#### Field mapping

| DB column | Source |
|---|---|
| `garmin_activity_id` | `"dailymile_<id>"` (from CSV id column) |
| `activity_name` | `title` from JSON |
| `activity_type` / `sport_type` | Mapped from DailyMile type (`Running`→`Lauf`, `Cycling`→`Fahrrad`, `Walking`→`Walk`, `Fitness`→`Fitness`) |
| `source` | Always `'DailyMile'` |
| `start_time_utc` | `workout_at` (UTC, from CSV) |
| `start_time_local` | `workout_at` converted to Europe/Berlin |
| `distance_m` | `distance` from JSON (km × 1000) |
| `duration_s` | `duration` from JSON (seconds; values > 86 400 s discarded) |
| `avg_hr` | `heart_rate` from JSON |
| `calories` | `calories` from JSON |
| `elevation_gain_m` | `climb` from JSON |
| `raw_json` | Full JSON file contents |

#### Notes

- Activities with distance ≤ 9 m are skipped.
- Cross-source duplicate detection uses a ±2 h time window plus ±5 % distance,
  with a calendar-date fallback for activities stored with midnight timestamps
  (e.g. Runmeter imports).

#### Example

```bash
python3 strava_import.py \
    --dailymile dailymile_export/ \
    --db ~/data/garmin_nostra.db \
    --backup
```

---

### Cross-source duplicate detection

All importers share the same duplicate detection logic. Before inserting an
activity, the script checks the existing DB for a match:

| Condition | Match criteria |
|---|---|
| Source has date-only timestamp (Runmeter) | Same calendar date + distance within ±5 % |
| DB entry has midnight timestamp (stored by Runmeter) | Source local date == DB date + distance within ±5 % |
| Both have full timestamps | Start times within ±2 h + distance within ±5 % |

This prevents double-importing activities that were already captured from a
different source (e.g. Garmin, Strava, Runmeter).

---

## Part 2 — web_dashboard.py (Activity Dashboard)

A responsive Flask web app for visualising long-term training trends from the
garmin-sync SQLite database.

### Features

- **Multi-user** — select any user via dropdown
- **Flexible activity types** — multi-select with Tom Select; handles German/English type names
- **Quick filter buttons** — one-click presets: 🏃 Running, 🚴 Bike, 🚵 MTB
- **Smart metric mode** — pace (min/km) for running, speed (km/h) for cycling; elevation chart only for cycling
- **Three time granularities** — week, month, year
- **Year filter** — include any subset of available years
- **Charts** — distance + activity count · pace or speed (avg, best, 3-period trend) · elevation (cycling)
- **Stat cards** — total distance, activity count, avg and best pace/speed
- **Record cards** — fastest activity (with name and date) · longest activity (with name and date)
- **Top-5 tables** — top 5 fastest and top 5 longest activities with full details
- **Mastodon export** — formatted text block + downloadable 1080×1080 social card PNG; both automatically aggregate to yearly resolution when the full history exceeds 24 periods

### Installation (Docker — recommended for servers)

The dashboard is designed to run in Docker behind an nginx reverse proxy.

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

The dashboard is now running on `http://localhost:5000`.

**4. Add an nginx server block** to expose it under a domain:
```nginx
server {
    listen 443 ssl;
    server_name stats.yourdomain.com;

    # Restrict to your IP address (recommended)
    allow 1.2.3.4;
    deny all;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Alternatively, use HTTP basic auth instead of IP restriction:
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
