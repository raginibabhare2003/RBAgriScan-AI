> CODE MAP: Har major folder/file ka Hinglish explanation `CODE_MAP.md` me hai.
> Folder-wise explanation ke liye `templates/FOLDER_GUIDE.md`, `static/js/FOLDER_GUIDE.md`, `static/css/FOLDER_GUIDE.md`, `static/icons/FOLDER_GUIDE.md`, `static/uploads/FOLDER_GUIDE.md`, aur `docs/FOLDER_GUIDE.md` dekho.


## RBAgriScan 2-in-1 upgrade

- **Plant Detection:** leaf/crop photo upload or camera capture for plant identification and disease screening.
- **Soil Detection:** separate soil/farm-ground photo upload or camera capture for visual moisture, drainage, texture and irrigation screening.
- **UI order:** Plant Upload → Plant Result → Soil Upload → Soil Result.
- **Two clear detector options:** Plant Detection and Soil Detection buttons scroll directly to the selected tool.
- **Chatbot formatting:** common Gemini Markdown (bold, headings, bullets and separators) is safely rendered instead of showing raw escape characters.
- **Forgot password:** if configured SMTP delivery fails, the reset link is still shown for local testing; reverse-proxy HTTPS support is enabled for production links.
- Soil photo analysis is visual screening only; exact pH, N/P/K, EC and laboratory moisture require proper testing.
# 🌱 Smart Crop AI — International AgriTech Prototype

A Flask-based, multilingual agricultural decision-support prototype combining broad plant identification, local disease screening, AI assistance, weather intelligence, farmer history, and safe fallbacks.

## What changed in this build

- **Pl@ntNet API-first plant identification** using the `all` project. This removes the 25-class TFLite model as the only plant-identification path.
- Existing **TFLite disease model is preserved** as an offline/local disease fallback for its actual supported classes.
- **Gemini Vision** can analyze disease/symptoms when configured.
- **Multilingual farmer assistant** with conversation context.
- **Offline assistant fallback** instead of crashing on Gemini 429/quota/network errors.
- Image quality checks for blurry/dark/very small images.
- Secure API-key configuration through `.env`.
- `/api/health` endpoint for deployment checks.
- Existing login, dashboard, feedback, admin, weather, camera, history, and supported-plants functionality preserved.

## Important limitation

No responsible system can guarantee identification of every plant or disease. Pl@ntNet returns ranked species predictions and confidence scores; this application rejects low-confidence plant matches instead of forcing a name. The current TFLite disease model remains limited to the classes in `labels.txt`.

## Setup

1. Create a virtual environment.
2. Install dependencies: `pip install -r requirements.txt`
3. Copy `.env.example` to `.env`.
4. Add your **Pl@ntNet API key** to `PLANTNET_API_KEY`.
5. Optionally add `GEMINI_API_KEY` for AI disease analysis and the full farmer assistant.
6. Set your **MySQL** connection details (`MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`) in `.env`. The database itself (e.g. `smart_crop_ai`) must already exist — the app creates the tables inside it automatically on first run.
7. Run: `python app.py`
8. Open: `http://127.0.0.1:5000`

## Database (MySQL)

The app now uses MySQL instead of SQLite (via `PyMySQL`). On startup it runs `init_db()`, which creates the `users`, `scans`, `feedback`, `suggestions`, and `contact_messages` tables if they don't exist yet, and promotes `ADMIN_EMAIL` to an admin account.

Locally (Windows), the simplest options are XAMPP/WAMP (bundles MySQL) or installing MySQL Community Server directly, then creating the database with:
```sql
CREATE DATABASE smart_crop_ai;
```

## Render / Gunicorn

Start command: `gunicorn app:app` (already set in the included `Procfile`, so Render picks it up automatically).

Render does not offer a managed MySQL database, so use an external MySQL host (e.g. Railway, Aiven, Clever Cloud, PlanetScale, or Hostinger) and put its connection details in Render's Environment settings as `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` — along with the other variables from `.env.example`. Never commit `.env` or API keys.

## Detection flow

`Image → Quality Check → Pl@ntNet species identification → local disease model when applicable → Gemini disease analysis when available → safe/unknown fallback`

## Mobile camera & fast multilingual chat (latest update)

- **Mobile camera**: the live-camera capture now has a front/back flip button and clear, specific error messages (permission denied, no camera found, camera busy, or the page not being served over HTTPS — mobile browsers block camera access on plain `http://`). Deploy behind HTTPS (Render/any real host does this automatically) for the camera to work on phones.
- **Chat in any language**: `/api/chat` now auto-detects the language the farmer actually typed in (via `langdetect`) and replies in that same language, instead of only replying in whatever language is selected in the site dropdown. Run `pip install -r requirements.txt` to pick up the new `langdetect` dependency.
- **Instant language switching**: the site-wide text dictionary and disease-guidance text used to be translated one string at a time (slow — many sequential network calls on the first visit to a new language). Translations are now fetched concurrently and cached to disk in `translation_cache/` (gitignored), so the *first* switch to a language is much faster and every switch after that is effectively instant, served straight from cache.

## Progressive Web App (installable, offline-capable, mobile-friendly)

This build ships as a full PWA:

- **Install to home screen** on Android, iOS (Safari → Share → *Add to Home Screen*), and desktop Chrome/Edge (an "⬇️ Install App" button appears in the nav once the browser thinks it's installable). Installed, it opens full-screen with no browser chrome, its own icon, and a themed splash/status bar.
- **Offline shell**: `static/sw.js` (registered from every page via `static/js/app.js`) caches the CSS/JS/icons and every page you actually visit. Re-open the app with no signal and previously viewed pages (home, dashboard, about, supported plants, etc.) still load, served from cache. A navigation that isn't cached yet falls back to a friendly `/offline` page instead of the browser's default error screen.
- **Connectivity awareness**: an "offline" banner appears site-wide when the connection drops, the camera/upload/chat/weather features show an inline notice and refuse to silently fail, and the app auto-reloads once you're back online.
- **Honest limits**: detection (Pl@ntNet/Gemini/MySQL), weather, and the chat assistant all need a live connection - the local TFLite model runs *server-side*, not on-device, so scanning itself is not available with zero connectivity. Only the interface, navigation and previously-loaded content work fully offline. Turning the on-device TFLite path into true client-side offline detection (e.g. via TensorFlow.js in the browser) is a reasonable next step but is a separate, larger change from the PWA shell added here.
- Icons are generated as a set of PNGs at `static/icons/` (16/32/180/192/512/512-maskable) referenced from `static/manifest.json`.

Rebuilding or replacing the icons: run `python3 gen_icons.py` if you keep that script, or drop in your own PNGs at the same paths/sizes and keep `static/manifest.json` in sync.

## International / accessibility notes

- 100+ languages are listed in `LANGUAGES`/`TEXTS` in `app.py`; right-to-left languages (Arabic, Urdu, Persian, Hebrew, Pashto, Kurdish, Sindhi, Dhivehi, Uyghur, Yiddish) automatically render the whole page with `<html dir="rtl">` via the `text_dir` template variable - no per-page changes needed when adding a new RTL language, just add its code to `RTL_LANGS` in `app.py`.
- All markup lives in one `templates/base.html` shared layout now (nav, footer, PWA tags, security headers via `after_request`), so page-specific templates only contain their own `{% block content %}` - update the nav or `<head>` once and it applies everywhere.
- Tap targets and form inputs are sized for mobile (min 44px, 16px font to avoid iOS zoom-on-focus).

## Safety

The system is decision support, not a substitute for a qualified agricultural diagnosis. Chemical guidance is intentionally conservative and tells users to follow locally approved product labels and expert advice.


## Internationalization fix (August 2026)

The UI now uses the selected language across the shared navigation, detection screen,
weather messages, chatbot UI, crop timeline, Plant Hospital, IoT dashboard, admin
messages, feedback/contact/suggestion pages, and dynamic crop/disease labels.
Hindi and Marathi keep their built-in translations; other listed languages use the
translation service with a local disk cache.

Important: live translation requires internet access and `deep-translator`.
The Gemini Farmer Assistant always uses the **selected site language** for its answer,
even if the farmer types the question in another language. With a configured
`GEMINI_API_KEY`, the assistant is intended for general agriculture questions, not
only disease questions.

## Current feature status

- Plant identification: Pl@ntNet primary when configured; local PlantVillage/TFLite
  disease screening and Gemini Vision fallback when configured.
- Disease guidance: home/basic, natural, field, chemical-safety, and prevention sections.
- Weather/environment: browser location or city lookup.
- Farmer chatbot: Gemini-powered general agriculture assistant with conversation history.
- Crop Timeline: five stages, weather-adjusted estimates, reminders, and stage history.
- Plant Hospital: diagnostic tickets, expert queue, claim/resolve, and stored messages.
- IoT: device keys, REST sensor ingestion, 7/30-day charts, and threshold alerts.
- PWA: install prompt, cached pages/assets, connectivity banner, and offline page.

### Known external dependencies

The project cannot make live AI answers or live translations without their configured
services. Pl@ntNet and Gemini are optional API integrations. The IoT dashboard needs
a real sensor (or a test POST) to show readings. The Plant Hospital is a ticket system;
expert verification, payments, real-time WebSockets, and MQTT are future production
upgrades rather than pretending they are already implemented.
"# RBAgriScan" 

## Latest UI upgrade

- **Custom crop tracking:** farmers can choose “Add my own crop” and enter any crop name. Custom crops use the same five-stage timeline with a safe default duration when no crop-specific agronomy template exists.
- **Google Translate:** the shared navigation includes the Google Translate widget for broader page-language coverage on desktop, tablet and mobile. The existing server-side Google translation cache remains available for app labels and backend responses.
- **Responsive UI:** crop forms, navigation, cards, uploads and chatbot controls are hardened for phone, tablet and laptop widths.

Google Translate's browser widget is loaded from Google's hosted script, so it does **not** require an extra Python package. The existing `deep-translator` dependency is still used for server-side translations.
