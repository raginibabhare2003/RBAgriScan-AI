# IoT Sensor & Smart Irrigation Integration Dashboard — Blueprint

Status: **implemented** for REST ingestion + dashboard (works today, no
external broker needed). MQTT ingestion is documented, not built — a real
MQTT broker isn't something this sandbox could stand up or test against, so
rather than fake it, here's the concrete path to add it.

## 1. What's implemented now (REST, live in this codebase)

**Schema**:
```sql
sensor_devices(id, user_id, device_key, name, crop_log_id, created_at)
sensor_readings(id, device_id, soil_moisture, temperature, humidity,
                 light_lux, n_ppm, p_ppm, k_ppm, recorded_at,
                 INDEX(device_id, recorded_at))
sensor_alerts(id, device_id, kind, message, acknowledged, created_at)
```
The `INDEX(device_id, recorded_at)` composite index matters as soon as you
have more than a token amount of data — every read query (`readings for
device X over the last 7/30 days`) filters on exactly those two columns.

**Ingestion**: `POST /api/iot/ingest` — a device authenticates with a
per-device `device_key` (generated with `secrets.token_hex(16)`, shown once
at device creation), not a login session, matching how a real ESP32 or a
Raspberry Pi bridge would call it. Payload is plain JSON; any subset of the
sensor fields is accepted so a moisture-only sensor doesn't need to send
nulls for NPK.

**Trigger rules**: `_check_sensor_thresholds()` — soil moisture < 25%,
temperature ≥ 40°C, or temperature ≤ 2°C each write a row to
`sensor_alerts`. Thresholds live in one `SENSOR_THRESHOLDS` dict at the top
of the IoT section in `app.py` — deliberately simple constants for the MVP,
not per-crop rules yet (see §3).

**Dashboard**: `/iot` — Chart.js (loaded from cdnjs) renders 7-day and
30-day line charts per device by calling `/api/iot/readings/<id>?range=7d`
(or `30d`), plus a small alerts strip from `/api/iot/alerts/<id>`. Both API
routes check `sensor_devices.user_id` against the session before returning
anything, so one farmer can't read another's sensor feed by guessing IDs.

## 2. Testing the pipeline without real hardware

Until you have a physical sensor, you can exercise the exact same endpoint:
```bash
curl -X POST https://your-app/api/iot/ingest \
  -H "Content-Type: application/json" \
  -d '{"device_key":"<key from /iot>","soil_moisture":18,"temperature":41,"humidity":40}'
```
That single call both stores a reading and (given those numbers) fires two
alerts — a good end-to-end smoke test before wiring a real device.

## 3. MQTT ingestion pipeline (production upgrade)

REST works fine for a handful of devices polling every few minutes. MQTT is
worth the extra moving part once you have many devices reporting more
frequently, because it's a persistent low-power connection instead of a new
HTTPS handshake per reading — this matters a lot on battery/solar field
hardware.

**Architecture**: `Sensor → MQTT broker (e.g. Mosquitto/EMQX/AWS IoT Core) →
bridge worker (subscribes, writes to MySQL/Postgres) → same
`sensor_readings` table the REST path already writes to.`

```python
# bridge_worker.py — a small separate process, not part of the Flask app
import paho.mqtt.client as mqtt, json, pymysql

def on_message(client, userdata, msg):
    payload = json.loads(msg.payload)
    device_key = msg.topic.split("/")[1]          # topic: sensors/<device_key>/readings
    # look up device_id by device_key, INSERT into sensor_readings,
    # run the same _check_sensor_thresholds() logic — factor that function
    # out of app.py into a shared module both processes import.

client = mqtt.Client()
client.on_message = on_message
client.connect("your-broker-host", 8883)           # TLS port
client.subscribe("sensors/+/readings")
client.loop_forever()
```
Key decisions this introduces that REST doesn't: you now run and secure a
broker (or pay for a managed one), you need TLS client-certificate or
username/password auth per device at the broker level (separate from the
app's `device_key`), and you need the bridge worker running as a long-lived
process (systemd service / separate container) alongside the web app. None
of that belongs silently added without you choosing a broker/host for it.

## 4. Time-series storage at scale

MySQL/Postgres with the composite index above is genuinely fine up to
tens of millions of rows for a dashboard like this. If you outgrow it
(thousands of devices reporting every few seconds), the standard move is a
purpose-built time-series store:

- **TimescaleDB** (a Postgres extension) — least disruptive if you're
  already moving to Postgres for the other features; `sensor_readings`
  becomes a "hypertable" with the same SQL you already write, but automatic
  time-partitioning and retention policies.
- **InfluxDB** — purpose-built, great compression, but a second query
  language (Flux/InfluxQL) instead of SQL.

Recommendation: don't reach for either until the composite-indexed MySQL
table actually shows slow query times in practice — premature migration
here costs more than it saves at this project's current scale.

## 5. Alert rules — where this connects to the rest of the app

`_check_sensor_thresholds()` is intentionally a small, separate function
(not inlined in the route) so it can grow into **crop-aware** rules later —
e.g. a moisture threshold that depends on the linked `crop_logs.crop_key`
and current stage (a seedling wants different soil moisture than a fruiting
plant), which is exactly the same weather-aware idea used in the Crop
Timeline blueprint's `estimate_crop_plan()`. That's also the natural home
for the "predictive micro-climate disease forecasting" idea from your
original list — once you have live soil/air data plus a stage-aware
threshold model, the forecast is a rule engine over data you already have,
not a new data source.
