# Tests (479 checks)

These are integration tests against a REAL MySQL/MariaDB database. **They delete rows from the app tables**, so they refuse to
run unless `MYSQL_DATABASE` ends with `_test`.

1. Create an empty test database:  `CREATE DATABASE smart_crop_ai_test CHARACTER SET utf8mb4;`
2. Run from the project root (Windows example):

       set MYSQL_USER=root
       set MYSQL_PASSWORD=your-password
       set MYSQL_DATABASE=smart_crop_ai_test
       python tests/test_rbagri.py
       python tests/test_phase1.py
       python tests/test_phase2.py
       python tests/test_phase3.py
       python tests/test_phase4.py
       python tests/test_phase5.py
       python tests/test_phase6.py
       python tests/test_languages.py
       python tests/test_realmodel.py

| File | Covers |
|---|---|
| test_rbagri.py | auth, CSRF, open redirect, IDOR, uploads, rate limits, error pages, scan pipeline (plant gate, low confidence, non-plant), languages |
| test_phase1.py | scan history, health score, timelines, analytics, PDF |
| test_phase2.py | farms/fields, field ownership, IoT link, field PDF |
| test_phase3.py | notifications, preferences, reminders, expert replies, soil moisture |
| test_phase4.py | photo + language chat, voice UI markers, community, moderation |
| test_phase5.py | weather, estimated disease risk, opt-in location, explanation images, satellite-ready, landing/SEO |
| test_phase6.py | new navbar (all features kept), farmer home tiles, Voice Assistant markup/JS, command parser (Node), assistant language, result Listen/Do-this-first |
| test_languages.py | bundled languages, all 117 configured languages render, offline fallback, route smoke test (every GET route x 3 roles x 3 languages), hard-coded English audit |
| test_realmodel.py | the REAL model.tflite: occlusion heatmap, thread safety, pipeline |

PlantNet and Gemini are mocked (no keys needed). Browser behaviour (camera, microphone, layout) is NOT covered by these tests.
