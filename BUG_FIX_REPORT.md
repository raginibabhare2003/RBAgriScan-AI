# PlantRagini International PWA — Bug Audit & Fix Report

## Bugs found and fixed

1. **Selected language was not applied to every page**
   - The global Jinja context used `TEXTS.get(lang, TEXTS["en"])`, so languages other than the built-in packs could silently show English.
   - Fixed: every request now loads the selected language through the translation/cache layer.

2. **New translation keys could be missing for Hindi/Marathi**
   - `translate_texts()` returned Hindi/Marathi immediately and did not fill newly added keys.
   - Fixed: built-in packs are preserved and missing keys are completed from the translation service.

3. **Many pages had hard-coded English UI**
   - Navigation, crop timeline, Plant Hospital, IoT, supported plants, offline page, and other labels were hard-coded.
   - Fixed: core user-facing labels now come from the selected language pack.

4. **JavaScript messages stayed in English**
   - Camera, offline, weather, chat, and detection messages were hard-coded in `app.js`.
   - Fixed: JavaScript now reads the selected language from `window.APP_TEXTS`.

5. **Chatbot language behavior was inconsistent**
   - The chatbot detected the typed message language and could answer in a language different from the selected site language.
   - Fixed: the selected site language is now the required output language, even if the farmer types the question in another language.

6. **Chatbot was too narrowly instructed**
   - The prompt focused mainly on disease/care questions.
   - Fixed: the assistant is now instructed to answer general agriculture/farming/plant questions and to be honest when current/live verification is required.

7. **Offline chatbot fallback was too limited**
   - Without Gemini, only a few keyword-based topics could receive useful answers.
   - The fallback remains a safety net; full general Q&A requires Gemini to be configured.

8. **Duplicate language codes**
   - The language list contained duplicates such as Nepali, Latin, Javanese and Sundanese.
   - Fixed: duplicate entries are removed at runtime. The project now exposes 117 unique listed languages.

9. **Language-detection aliases were incorrect**
   - Chinese language detection could be normalized to a code not present in the language map.
   - Fixed: aliases are normalized to `zh-CN`, `zh-TW`, `he`, `id`, etc.

10. **Dynamic crop/disease names could remain English**
    - Supported-plant names, crop names and crop stages were not consistently localized.
    - Fixed: these dynamic labels can use the selected-language translation helper and cache.

11. **Disease-guidance chatbot headings were always English**
    - "Home / Basic Care", "Natural Management", etc. were hard-coded in chatbot responses.
    - Fixed: these headings now come from the selected language pack.

12. **Flash messages were hard-coded English**
    - Several admin/crop/IoT/Plant Hospital success/error messages ignored the selected language.
    - Fixed: these messages use the translation helper.

13. **Translation cache writes were not atomic**
    - Concurrent requests could potentially write the same JSON cache file at the same time.
    - Fixed: cache files are written to a temporary file and atomically replaced.

14. **PWA could keep an old JavaScript/template cache**
    - The service-worker version was still `v1`.
    - Fixed: bumped to `smart-crop-ai-v2` so updated assets are installed.

## Round 2 fixes (this pass)

15. **Chatbot ignored typed language ("Gemini-style" auto-detect was dead code)**
    - `detect_message_language()` was fully written (with a docstring) but never called anywhere. `/api/chat` always replied in the site's dropdown language only, so a farmer who typed a question in a language different from the dropdown got an answer in the wrong language.
    - Fixed: `/api/chat` now calls `detect_message_language(message, fallback=site_lang)` and replies in the language the farmer actually typed (falling back safely to the site language for short/ambiguous text) - matching how Gemini/ChatGPT behave.

16. **Translation failures were permanently cached as the "translation"**
    - In `translate_texts()` (site-wide UI text) and `disease_info()` (per-disease guidance), any translation call that failed (network hiccup, rate limit, empty response) had its English fallback written into the on-disk cache as if it were a real translation. Once cached, that key was considered "done" and never retried - so a single bad moment could permanently freeze a language's UI or a disease's guidance in English, even after the translation service was working again.
    - Fixed: only successful translations are now persisted to the cache; failed ones are served in English for that one response and retried on the next request.

17. **Two admin/role-check routes crashed (500 error) instead of showing a message**
    - `admin_required` and `role_required` referenced an undefined `lang` variable when flashing an access-denied message, which raised `NameError` for any signed-in non-admin user hitting an admin/role-gated route.
    - Fixed: use `session.get("lang", "en")` instead.

18. **A duplicate `no_suggestions` key silently overwrote the real one**
    - The English text dictionary defined `no_suggestions` twice - once for the Suggestions page ("No suggestions yet. Be the first!") and once (unused) inside the admin-panel block ("No suggestions yet."). Because both used the exact same key, the second definition silently won, so the Suggestions page lost its friendlier text - and this incorrect text was the source string used to auto-translate the page into all 100+ other languages.
    - Fixed: renamed the unused admin-panel duplicates to `admin_no_contacts`/`admin_no_feedback`/`admin_no_suggestions` so they no longer collide.

## Verification performed

- Python `app.py` syntax compilation: PASS
- Jinja template parsing: PASS for all HTML templates
- JavaScript syntax check: PASS
- Template `texts.*` references checked against the combined English text keys: PASS
- Duplicate language check: 117 unique runtime languages

## External-service limitation

A full live test still depends on the deployment environment:
- MySQL must be reachable.
- Gemini API key must be valid and have quota.
- Pl@ntNet API key/quota is optional.
- Live translation for 100+ languages needs internet and the translation provider.
- IoT charts need sensor data or a test REST POST.
- Plant Hospital needs an expert/admin account for the expert side.

These are external dependencies, not something that can be proven by static code checks alone.


## HTTPS / Mobile Camera Fix
- Added production HTTPS enforcement using Render's `X-Forwarded-Proto` header.
- Added `render.yaml` with `FORCE_HTTPS=true`.
- Localhost HTTP remains allowed for development.
- Camera now checks secure context before calling `getUserMedia`.
- Added clearer permission, unavailable, not-found, in-use, capture, and secure-connection errors.
- Camera stream is stopped on page hide/close to release the device camera.
- Camera capture checks that a real video frame exists before creating the image.
- Chat now explicitly sends the selected page language to the server.
