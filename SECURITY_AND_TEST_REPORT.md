# RBAgriScan - audit, upgrade and test report

Existing project upgraded in place (no rewrite). 402 automated checks pass against MariaDB (see `tests/README.md`).

## What was done (8 phases)
1. **Security audit + pipeline fixes**: debug server off by default, open-redirect, session fixation, login/register throttling, private photo delivery (`/media`, ownership checked), IoT/ticket ownership holes, safe error pages (400/401/403/404/405/413/429/500), service worker no longer caches private pages/photos, translation circuit breaker (pages never hang), plant-validator cross-check (a Mango leaf can no longer get a Tomato disease), shared upload/camera pipeline.
2. **History, crop health, timelines, analytics, PDF** (real data only; "Not enough data yet" otherwise).
3. **Farms / fields** (scans, timelines, sensors, PDF per field; ownership enforced).
4. **Notifications** (in-app): scan reminders, low soil moisture, expert replies, health changes; preferences; lazy scheduler.
5. **AI assistant upgrade + voice + community**: text/photo/voice assistant that answers in the SELECTED language (photos go through plant validation first, non-plants never reach Gemini); farmer community with comments/replies, likes, expert badge, reports, auto-hide after 3 reports, admin moderation, abuse limits.
6. **Weather + estimated disease risk + opt-in location**: forecast, advisories ("estimated risk", never a guarantee), weather snapshot stored with scans (coordinates never stored), optional coarse (0.1 degree) location for weather alerts, removable at any time.
7. **Explainable AI + satellite/drone readiness**: occlusion-sensitivity heatmap for scans analysed by the built-in model (works with the shipped TFLite model; Grad-CAM can be registered via `EXPLAINERS`); imagery-provider interface that shows "Satellite/drone analysis is not configured yet." and never fabricates data.
8. **Languages, SEO, PWA, polish**: public indexable landing page, per-page meta/OG tags, noindex for private pages, robots/sitemap, bundled translations, hard-coded English wrapped in `tr()`, brand unified to RBAgriScan, inference thread-lock, Render config (stable SECRET_KEY, 1 worker x 4 threads, 120 s timeout).

## Findings worth knowing
- The shipped model has **25 classes** and, on its own, confidently accepts non-plant images (random green noise -> "Tomato healthy 84.9%"). The PlantNet/Gemini plant gate is therefore essential; if both are unavailable the app refuses to analyse instead of guessing.
- The TFLite interpreter was not thread-safe; it is now protected by a lock.
- Notification "reminders" run lazily when the app is opened (at most hourly) - there is no background worker on Render free.

## Languages (be precise)
- 117 languages are configured; all render correctly (html lang, RTL for ar/he/fa/ur) - tested.
- Bundled offline translations (core UI): hi, mr, bn, ar, gu, ta, te, fr, es, de, ja, zh-CN (+ bs, ne, la). These are machine-assisted and **need native-speaker review**.
- 14 of the 27 required languages (kn, ml, pa, ur, pt, it, nl, ru, uk, tr, ko, id, vi, th) and all other languages rely on the live translator (Google/MyMemory via deep-translator); if it is unreachable they fall back to English and the page still works. Translated text appears on the next page view (translation runs in the background).
- Newer pages (history, fields, notifications, community, landing) get their phrases translated by the live translator; they are not bundled.
- PDF reports: Latin-script languages are translated; other scripts fall back to English (built-in PDF font).

## NOT verified / not implemented (honest list)
- Not tested on real devices or browsers: camera on Android/iPhone/laptop, microphone / speech recognition, text-to-speech, responsive layouts (360px - 1920px+), PWA install. CSS safeguards were added but layout was not visually verified.
- Live PlantNet / Gemini / Open-Meteo calls were mocked. Check that `GEMINI_MODEL` is a valid model name for your key.
- Windows run not tested (tests + app were run on Linux with MariaDB); no Windows-specific code was added.
- Not implemented: email / push delivery of notifications, real satellite/drone data, Grad-CAM (needs a Keras model file), reports page beyond PDFs, symptoms/cause text (not in the disease data and not invented), per-field weather location, audit_logs table, translations for all 117 languages bundled offline.
- Health score is an indicative rule-based score (see `health_assessment()`), not a lab measurement.


## Round 3: farmer-first UI + Voice Assistant (this update)
- **Navbar**: 21 links -> 5 important ones (Scan Plant, Voice Assistant, Weather, My Fields, Expert Help) + a **Features** dropdown that still contains every other feature (Scan History, My Crops, IoT, Dashboard, Community, Supported Plants, Suggestions, Feedback, Contact, About, Admin/Moderation). Language dropdown removed; Google Translate widget, notification bell and Login/Logout kept. Mobile: hamburger + bottom tab bar (Scan, Voice, Weather, Fields, More).
- **Home for farmers**: "What do you want to do?" with 6 big icon tiles, a 3-step guide, and a first-visit welcome card (3 steps + Hindi/Marathi/Gujarati/Tamil/Telugu/English buttons that drive Google Translate).
- **Scan result**: Listen (reads result aloud), "Do this first" (first 3 real steps from the guidance - nothing invented) and "Not sure? Ask an expert".
- **Voice Assistant** (replaces "Assistant"): big mic, speaks answers by default (mute checkbox), speaking-language picker (15 languages), voice commands for apps and pages (ChatGPT, Gemini, Google, Gmail, YouTube, WhatsApp, Maps, weather, fields, scan, ...; English/Hindi/Hinglish/Devanagari), one-tap app buttons, "Ask ChatGPT" button (question prefilled) on every answer and on every error.
- **Assistant camera fixed**: live camera popup (getUserMedia: laptop webcam + phone back camera, flip, retake, use) with gallery fallback; the old `<input capture>` approach opened a file picker on laptops and the `DataTransfer` copy failed silently on older iPhones.
- Removed the background prefetch of 6 translations on every page load (no longer needed without the language dropdown).
- **Not possible from a website** (by design of browsers): controlling other apps (we can only open them), reading Gmail (needs OAuth), voice input on Firefox. Not tested on real devices: microphone, text-to-speech, camera popup, dropdown/bottom-bar layout, Google Translate interplay.
- Tests: 479 checks (tests/README.md). `.env` is NOT included in the zip.
