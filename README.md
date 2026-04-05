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
| `--applehealth DIR` | **Apple Health** export | Extracted `Export.zip` folder containing `Export.xml` |
| `--garmin-archive DIR` | **Garmin** data export | Extracted Garmin account export containing upload zip archives |

### Requirements

Python 3.10 or newer.  
`zoneinfo` (stdlib ≥ 3.9) is used for UTC → Europe/Berlin conversion.  
`fitdecode>=0.10` is required for the `--garmin-archive` mode only:

```bash
pip install fitdecode>=0.10
```

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
| `--gpx-dest-db DIR` | *(same as --gpx-dest)* | Path prefix stored in the DB for GPX files (use when the app runs in Docker and sees a different path than the host). |
| `--fit-dest DIR` | *(not set)* | Directory to copy FIT/FIT.GZ files into. |
| `--fit-dest-db DIR` | *(same as --fit-dest)* | Path prefix stored in the DB for FIT files (use when the app runs in Docker and sees a different path than the host). |
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

### Mode: Apple Health (`--applehealth`)

Imports workouts from an **Apple Health** export. On your iPhone: Health app →
profile picture → **Export All Health Data** → share the resulting `Export.zip`
and extract it. Pass the extracted folder (the one containing `Export.xml`) to
this mode.

The XML is parsed with `iterparse` so even very large exports (> 1 GB) are
handled efficiently.

#### Usage

```bash
python3 strava_import.py --applehealth DIR --db FILE [OPTIONS]
```

#### Options

| Option | Default | Description |
|---|---|---|
| `--applehealth DIR` | *(required)* | Path to the extracted Apple Health export folder. |
| `--db FILE` | *(required)* | SQLite database file. |
| `--gpx-dest DIR` | *(not set)* | Directory to copy workout-route GPX files into. |
| `--user-id N` | `1` | `user_id` assigned to every imported row. |
| `--start-date DATE` | *(none)* | Only import activities on or after this date. |
| `--end-date DATE` | *(none)* | Only import activities on or before this date. |
| `--init-db` | *(off)* | Create tables if missing. |
| `--dry-run` | *(off)* | Simulate without touching the DB. |
| `--backup` | *(off)* | Write a timestamped DB backup before any changes. |
| `--overwrite-gpx` | *(off)* | Overwrite GPX files that already exist in `--gpx-dest`. |

#### Field mapping

| DB column | Source |
|---|---|
| `garmin_activity_id` | `"applehealth_<UTC_timestamp>"` (synthetic, stable) |
| `activity_name` | `"<type> <date>"` e.g. `"Lauf 2024-06-15"` |
| `activity_type` / `sport_type` | Mapped from `HKWorkoutActivityType*` (70+ types) |
| `source` | Always `'AppleHealth-Import'` |
| `start_time_utc` | `startDate` attribute converted to UTC |
| `start_time_local` | `startDate` wall-clock time (timezone stripped) |
| `duration_s` | `duration` attribute (converted from minutes) |
| `distance_m` | `WorkoutStatistics` sum for distance types (km × 1000) |
| `avg_speed_ms` | Derived: `distance_m / duration_s` |
| `avg_hr` / `max_hr` | `WorkoutStatistics HKQuantityTypeIdentifierHeartRate` |
| `elevation_gain_m` | `WorkoutStatistics HKQuantityTypeIdentifierElevationAscended` |
| `calories` | `totalEnergyBurned` attr or `HKQuantityTypeIdentifierActiveEnergyBurned` |
| `gpx_path` | Absolute path of copied GPX from `workout-routes/` |
| `raw_json` | `{"applehealth_source": "Apple Watch"}` |

#### Activity type mapping (selected)

| `HKWorkoutActivityType` | DB `activity_type` |
|---|---|
| Running | Lauf |
| Cycling / IndoorCycling / HandCycling | Fahrrad |
| Walking / IndoorWalk | Walk |
| Hiking | Wandern |
| Swimming / OpenWaterSwimming | Schwimmen |
| FunctionalStrengthTraining / TraditionalStrengthTraining | Kraft |
| HighIntensityIntervalTraining | HIIT |
| CrossCountrySkiing | Langlauf |
| Skiing / DownhillSkiing | Ski |
| SwimBikeRun | Triathlon |
| … 60+ more | mapped or passed through as-is |

#### Notes

- Activities with distance ≤ 9 m are skipped; workouts without any distance
  (e.g. strength training) are imported with `distance_m = NULL`.
- Cross-source duplicate detection uses a ±2 h time window plus ±5 % distance
  against existing activities for the same `--user-id`.
- Both `export.xml` and `Export.xml` filenames are accepted.

#### Examples

```bash
# Dry-run preview
python3 strava_import.py \
    --applehealth apple_health_export \
    --db ~/data/garmin_nostra.db \
    --dry-run

# Full import with GPX files
python3 strava_import.py \
    --applehealth apple_health_export \
    --db ~/data/garmin_nostra.db \
    --gpx-dest data/gpx \
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

### Mode: Garmin archive (`--garmin-archive`)

Imports activities from a **Garmin account data export**. Request your export
from Garmin Connect → Account → Data Management → Export Your Data. Extract the
resulting zip and pass the root folder to this mode.

The importer reads FIT files directly from the upload zip archives inside the
export (no manual extraction needed) and parses each file's session metrics
using `fitdecode`.

**Requires:** `pip install fitdecode>=0.10`

#### Usage

```bash
python3 strava_import.py --garmin-archive DIR --db FILE [OPTIONS]
```

#### Options

| Option | Default | Description |
|---|---|---|
| `--garmin-archive DIR` | *(required)* | Path to the extracted Garmin export root folder. |
| `--db FILE` | *(required)* | SQLite database file. |
| `--fit-dest DIR` | *(not set)* | Host directory to copy FIT files into. |
| `--fit-dest-db DIR` | *(same as --fit-dest)* | Container-internal path prefix stored in the DB (Docker setups). |
| `--start-date DATE` | *(none)* | Only import activities on or after this date. |
| `--end-date DATE` | *(none)* | Only import activities on or before this date. |
| `--user-id N` | `1` | `user_id` assigned to every imported row. |
| `--init-db` | *(off)* | Create tables if missing. |
| `--dry-run` | *(off)* | Simulate without touching the DB. |
| `--backup` | *(off)* | Write a timestamped DB backup before any changes. |

#### Matching logic

For each FIT file the importer tries to match an existing DB activity in order:

1. **±60 s timestamp match** — same activity already in DB (e.g. from garmin-nostra sync): update `fit_path` only if missing, nothing else is touched.
2. **±2 h + distance ±5 % match** — same real-world event from another source: update `fit_path` if missing, otherwise skip.
3. **No match** — insert a new row with `source = 'GarminArchive'`.

Non-activity FIT files in the export (segment lists, monitoring data, courses)
have no session message and are silently skipped.

#### Field mapping

| DB column | Source |
|---|---|
| `garmin_activity_id` | `"garminarchive_<UTC_timestamp>"` (synthetic) |
| `activity_name` | `"<type> <date>"` e.g. `"Lauf 2024-06-15"` |
| `activity_type` / `sport_type` | Mapped from FIT `sport` + `sub_sport` fields |
| `source` | Always `'GarminArchive'` |
| `start_time_utc` | FIT session `start_time` (UTC) |
| `start_time_local` | `start_time` converted to Europe/Berlin |
| `elapsed_time_s` / `duration_s` | `total_elapsed_time` |
| `moving_time_s` | `total_timer_time` |
| `distance_m` | `total_distance` |
| `elevation_gain_m` / `elevation_loss_m` | `total_ascent` / `total_descent` |
| `avg_speed_ms` / `max_speed_ms` | `avg_speed` / `max_speed` |
| `avg_hr` / `max_hr` | `avg_heart_rate` / `max_heart_rate` |
| `avg_cadence` / `max_cadence` | `avg_cadence` / `max_cadence` |
| `avg_power_w` / `max_power_w` / `normalized_power_w` | `avg_power` / `max_power` / `normalized_power` |
| `calories` | `total_calories` |
| `start_lat` / `start_lon` | `start_position_lat/long` (converted from semicircles) |
| `fit_path` | Container path of the copied FIT file |

#### Examples

```bash
# Dry-run preview
python3 strava_import.py \
    --garmin-archive ~/Downloads/20260405garmin_export \
    --db ~/data/garminnostra/garmin_nostra.db \
    --fit-dest ~/data/garminnostra/fit \
    --fit-dest-db /data/fit \
    --dry-run

# Full import with backup (Docker path mapping)
python3 strava_import.py \
    --garmin-archive ~/Downloads/20260405garmin_export \
    --db ~/data/garminnostra/garmin_nostra.db \
    --fit-dest ~/data/garminnostra/fit \
    --fit-dest-db /data/fit \
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

### Utility scripts

#### `fix_paths.py` — normalise host paths to container paths

Rewrites `fit_path` and `gpx_path` values that point to host paths
(`/home/vinz/data/garminnostra/…`) to container-internal paths (`/data/…`).
Run this once after switching to a Docker setup or after an import that wrote
host paths.

```bash
python3 fix_paths.py --db ~/data/garminnostra/garmin_nostra.db --dry-run
python3 fix_paths.py --db ~/data/garminnostra/garmin_nostra.db
```

Files not found on the host are reported and left unchanged.

#### `fix_strava_paths.py` — move Strava dump files to the data store

Finds activities whose `fit_path` or `gpx_path` still points into the Strava
dump folder (`~/bin/sport_import/strava-import/strava_data/`), copies each
file to the correct destination directory, and rewrites the DB path to the
container-internal path.

All four destination arguments are required (no defaults — paths are installation-specific):

```bash
python3 fix_strava_paths.py \
    --db ~/data/garminnostra/garmin_nostra.db \
    --fit-dest ~/data/garminnostra/fit/vinz \
    --fit-dest-db /data/fit/vinz \
    --gpx-dest ~/data/garminnostra/gpx/vinz \
    --gpx-dest-db /data/gpx/vinz \
    --dry-run

python3 fix_strava_paths.py \
    --db ~/data/garminnostra/garmin_nostra.db \
    --fit-dest ~/data/garminnostra/fit/vinz \
    --fit-dest-db /data/fit/vinz \
    --gpx-dest ~/data/garminnostra/gpx/vinz \
    --gpx-dest-db /data/gpx/vinz
```

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
