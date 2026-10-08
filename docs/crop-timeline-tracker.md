# Automated Crop Timeline & Stage Tracker — Blueprint

Status: **implemented** in `app.py` / `templates/crop_timeline_*.html` on the existing MySQL stack.
This document explains the design, and the production-scale (PostgreSQL) version of the same design.

## 1. What's implemented now (MySQL, live in this codebase)

**Tables** (see `init_db()` in `app.py`):

```sql
crop_logs(id, user_id, crop_key, nickname, planted_on, city,
          current_stage_order, current_stage_started_on, status, created_at)
crop_stage_events(id, crop_log_id, stage_order, stage_name, note, logged_at)
care_reminders(id, crop_log_id, kind, due_date, done, created_at)
```

Stage *durations* (`CROP_STAGE_TEMPLATES` in `app.py`) are static, in-code reference
data for the MVP — 5 stages (Seedling → Vegetative → Flowering → Fruiting →
Harvesting) with baseline day-counts per crop. This keeps the MVP simple and
migration-free; see §3 for the admin-editable version.

**Backend logic** (`estimate_crop_plan()` in `app.py`):
- Takes `crop_key`, `planted_on`, and an optional `avg_temp_c`.
- Applies a multiplier (`_season_speed_multiplier`) derived from a live
  Open-Meteo lookup for the crop's city (`_current_temp_for_city`) — cold
  weather stretches every stage, warm-optimal weather is baseline, extreme
  heat slows things again (heat stress).
- Returns a list of `{order, stage, days, start, end}` — the full projected
  timeline from a single call, recomputed on every page view so it always
  reflects current weather rather than freezing an estimate at planting time.

**Reminders**: seeded once at creation (`_seed_reminders`) — a watering
reminder every 3 days for the full projected duration, plus a
fertilize/stage-check reminder at the start of each stage. Shown as a due-list
on `/crops/<id>` and a badge count on `/crops`.

**Routes**: `/crops` (list + create), `/crops/<id>` (timeline + advance stage),
`/crops/<id>/advance` (POST, logs a `crop_stage_events` row and moves the
crop to the next stage or to `harvested`), `/crops/reminder/<id>/done` (POST).

## 2. Notification system — what's here vs. what's next

**Implemented**: in-app "due now" reminder list, computed on page load from
`care_reminders`. Zero infrastructure, works today.

**Documented upgrade path** (not wired up — needs an account/API key and a
background worker, which don't belong silently added to a codebase without
your sign-off):
1. Add **APScheduler** (or a cron'd script) running a daily job that queries
   `care_reminders WHERE due_date = CURDATE() AND done = 0`.
2. For each row, look up the user's notification channel:
   - **Push**: Web Push (VAPID keys) — pairs naturally with the PWA service
     worker already in `static/sw.js`; add a `push` event handler there.
   - **SMS**: Twilio/MSG91 — most realistic reach for farmers with basic
     phones, and the highest ongoing cost per message.
   - **Email**: SMTP via Flask-Mail — cheapest, lowest open-rate for this
     audience.
3. Recommendation: start with Web Push (free, no per-message cost, and the
   PWA is already installed on the user's home screen) and add SMS only for
   users who opt in, since it carries a real per-message bill.

## 3. Production-scale version (PostgreSQL)

If/when this app moves off MySQL, the natural upgrade is to make stage
durations **admin-editable** instead of code constants, and to add
geospatial fields for regional tuning via PostGIS:

```sql
CREATE TABLE crop_stage_templates (
    id            SERIAL PRIMARY KEY,
    crop_key      TEXT NOT NULL,
    stage_order   SMALLINT NOT NULL,
    stage_name    TEXT NOT NULL,
    typical_days  SMALLINT NOT NULL,
    care_tip      TEXT,
    region        GEOGRAPHY(POLYGON, 4326),   -- PostGIS: optional per-region override
    UNIQUE (crop_key, stage_order, region)
);

CREATE TABLE crop_logs (
    id                        BIGSERIAL PRIMARY KEY,
    user_id                   BIGINT NOT NULL REFERENCES users(id),
    crop_key                  TEXT NOT NULL,
    nickname                  TEXT,
    planted_on                DATE NOT NULL,
    location                  GEOGRAPHY(POINT, 4326),   -- PostGIS: exact field location
    current_stage_order       SMALLINT DEFAULT 1,
    current_stage_started_on  DATE,
    status                    TEXT DEFAULT 'active',
    created_at                TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX crop_logs_location_gix ON crop_logs USING GIST (location);
```

Why PostGIS here specifically: once you have farmer locations as real
geography points, you can (a) let an agronomist query "tickets/crops within
50km of me" for the Expert Marketplace, and (b) correlate crop-stage timing
against regional weather/disease outbreak data — which is also the data
backbone the "predictive micro-climate forecasting" idea from your earlier
list would need. It's not required for the Timeline Tracker alone; it earns
its cost only once a second feature needs it too.

## 4. Frontend

Implemented as server-rendered Jinja (`crop_timeline_list.html`,
`crop_timeline_detail.html`) using the shared design system in
`static/css/style.css` — stage cards with a progress bar, a reminders panel,
and a stage-history log. If/when the app splits into an API-first backend
(see the "Engineering & Codebase Modernization" thread from your notes),
this becomes a React/Flutter timeline component consuming
`GET /api/crops/<id>` — the current `estimate_crop_plan()` function is
already pure and JSON-serializable, so that split is mechanical, not a
rewrite.
