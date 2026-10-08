# Multi-User Plant Hospital & Expert Marketplace — Blueprint

Status: **partially implemented**. Ticket creation, triage queue, claim/resolve,
and a working (polling, not real-time) message thread are live. RBAC is a
minimal role column, not a full permission system. Real-time chat and
expert verification are documented, not built — see why in §3 and §4.

## 1. What's implemented now

**Schema** (`init_db()` in `app.py`):

```sql
users.role            -- 'farmer' (default) | 'agronomist' | admin via users.is_admin
expert_profiles(user_id PK, bio, specialties, region, verified)
diagnostic_tickets(id, user_id, scan_id, image, model_prediction, confidence,
                    lat, lon, city, notes, status, assigned_expert_id, created_at)
ticket_messages(id, ticket_id, sender_id, message, created_at)
```

`status` moves through `open → in_review → resolved`. `expert_profiles`
exists in the schema but has no admin UI yet to verify an agronomist — right
now, anyone with `role='agronomist'` on their `users` row gets expert access.
**That's a placeholder, not a real trust system** — see §2.

**Access control**: `role_required(*roles)` decorator in `app.py` gates
routes to a set of role strings (admins always pass). `ticket_list()` and
`ticket_detail()` branch their query and the buttons they render based on
`is_expert = role == 'agronomist' or is_admin`.

**Ticket workflow**: `/experts/tickets` (GET: list mine, or the shared queue
if I'm an expert; POST: create, optionally pre-filled from a recent scan via
the dropdown in `expert_tickets.html`). `/experts/tickets/<id>` (GET: thread;
POST action=`message`/`claim`/`resolve`).

**"Secure chat"**: implemented as a plain HTML form POST + full page
reload — every message is stored in `ticket_messages` and access is checked
against `ticket.user_id == session.user_id OR is_expert` on every request.
It is *secure* (server-side authorization, no client-trusted state) but it
is **not real-time** — the other party has to reload to see a new message.
That gap, and how to close it, is §3.

## 2. RBAC — what a real implementation needs

The current `role` column is enough to gate routes, but a real expert
marketplace needs a verification workflow, not a self-declared role:

1. A user requests agronomist status (form: credentials, region,
   specialties) → row created in `expert_profiles` with `verified=0`.
2. An admin reviews it in `/admin` (extend the existing admin panel — the
   table already exists, just needs an admin route + template section) and
   flips `verified=1` **and** sets `users.role='agronomist'`. Do the role
   flip only on verification, never on self-request, or you've built a
   marketplace anyone can grant themselves access to.
3. Add a `middleware`-style check: `role_required('agronomist')` should also
   confirm `expert_profiles.verified=1`, not just `role='agronomist'` — two
   independent checks are harder to bypass by a stray UPDATE than one.

## 3. Real-time chat — WebSocket architecture

To go from "reload to see replies" to real-time:

**Server**: add `flask-socketio` (with `eventlet` or `gevent` as the async
worker) alongside the existing Flask app. A ticket room = `ticket_<id>`.
```python
from flask_socketio import SocketIO, join_room, emit
socketio = SocketIO(app, cors_allowed_origins="*")

@socketio.on("join_ticket")
def on_join(data):
    # server-side re-check of the same authorization ticket_detail() already
    # does — never trust a client-supplied ticket_id/user_id pair alone.
    join_room(f"ticket_{data['ticket_id']}")

@socketio.on("send_message")
def on_message(data):
    # validate + INSERT into ticket_messages exactly as the POST route does
    emit("new_message", payload, room=f"ticket_{data['ticket_id']}")
```
**Client**: `static/js/app.js` gets a small `initTicketChat()` using
`socket.io-client` from a CDN, listening for `new_message` and appending to
the DOM — the existing message-render markup in `expert_ticket_detail.html`
can be reused as an HTML template string.
**Deployment note**: WebSockets need a host that supports long-lived
connections (works on Render/Fly/a real VPS; does **not** work on classic
serverless functions like plain AWS Lambda without API Gateway's WebSocket
support specifically). This is the main reason it's documented rather than
silently wired in — it changes your hosting requirements.
**Photo-sharing in chat**: reuse the existing `UPLOAD_FOLDER`/`allowed_file`
upload path already in `app.py` for scan images; add an `image` column to
`ticket_messages` (schema is ready for it) and an upload input in the chat
form.

## 4. Expert dashboard / triage queue — UI structure

Implemented as a shared list at `/experts/tickets` (table: farmer, AI
prediction, city, status, opened, open-button) rather than a separate
dashboard route — reusing one page for both roles avoids the two views
drifting apart. For a dedicated triage view at scale, the natural split is:

- **Queue panel** (left): tickets `WHERE status='open'`, sorted oldest-first.
- **My tickets** (right): `WHERE assigned_expert_id = me AND status='in_review'`.
- **Map view**: once `diagnostic_tickets.lat/lon` is populated from the
  browser's geolocation (same pattern as the existing weather-location
  button in `static/js/app.js`), a Leaflet/Mapbox map clustering open
  tickets by region lets an agronomist pick up ones near them — this is
  where the PostGIS `location` column from the Timeline blueprint pays off
  again (`ORDER BY location <-> my_location LIMIT 20`).

## 5. What's deliberately not built

- **Payments** for a real "marketplace" (expert gets paid per consult) — a
  Stripe Connect integration, not mentioned in the original ask but implied
  by "marketplace"; flag if you want this scoped.
- **Abuse/moderation tooling** for the chat — rate limiting, report button,
  block list. Worth having before this is public, not before it's a demo.
