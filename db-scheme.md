CREATE TABLE IF NOT EXISTS activities (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                 INTEGER NOT NULL REFERENCES users(id),
    garmin_activity_id      TEXT    NOT NULL,

    -- identity
    activity_name           TEXT,
    activity_type           TEXT,
    sport_type              TEXT,

    -- time
    start_time_utc          TEXT,
    start_time_local        TEXT,
    timezone                TEXT,
    duration_s              REAL,
    elapsed_time_s          REAL,
    moving_time_s           REAL,

    -- distance & elevation
    distance_m              REAL,
    elevation_gain_m        REAL,
    elevation_loss_m        REAL,
    min_elevation_m         REAL,
    max_elevation_m         REAL,

    -- speed
    avg_speed_ms            REAL,
    max_speed_ms            REAL,

    -- heart rate
    avg_hr                  INTEGER,
    max_hr                  INTEGER,
    resting_hr              INTEGER,

    -- power
    avg_power_w             REAL,
    max_power_w             REAL,
    normalized_power_w      REAL,

    -- cadence / stride
    avg_cadence             INTEGER,
    max_cadence             INTEGER,
    avg_stride_length_m     REAL,
    avg_vertical_osc_cm     REAL,
    avg_ground_contact_ms   REAL,

    -- training load
    aerobic_training_effect REAL,
    training_stress_score   REAL,
    vo2max_estimate         REAL,
    intensity_factor        REAL,

    -- misc
    calories                INTEGER,
    steps                   INTEGER,
    avg_temperature_c       REAL,
    max_temperature_c       REAL,
    start_lat               REAL,
    start_lon               REAL,

    -- full raw payload for future-proofing
    raw_json                TEXT,

    -- sync state
    gpx_path                TEXT,
    caldav_pushed           INTEGER NOT NULL DEFAULT 0,
    mastodon_posted         INTEGER NOT NULL DEFAULT 0,
    synced_at               TEXT    NOT NULL,

    UNIQUE(user_id, garmin_activity_id)

