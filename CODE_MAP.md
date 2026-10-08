# RBAgriScan – Code Map (Hindi + English)

Ye file batati hai ki project ka kaunsa folder/file kis kaam ke liye hai. **Code ke andar bhi Hinglish comments add kiye gaye hain** taaki future me easily samajh aaye.

## Main folders
- `templates/` → HTML pages / UI screens. **Kya dikhana hai** yahan decide hota hai.
- `static/js/` → Browser-side JavaScript. Camera, image preview, plant/soil mode switching, chatbot UI, language/UI helpers yahan.
- `static/css/` → Design, responsive/mobile layout, cards, buttons, dashboard styling.
- `static/icons/` → App logo/favicon/PWA icons. Ye image files hain, isliye image ke andar comments possible nahi; mapping yahan hai.
- `static/uploads/` → Uploaded crop/soil images ka runtime folder. `.gitkeep` sirf folder ko Git me preserve karta hai.
- `docs/` → Feature documentation: IoT, Crop Timeline, Plant Hospital.

## Main files
- `app.py` → **Main Flask backend**. Login, database, plant detection, PlantNet, Gemini, chatbot, soil analysis, translations, weather, crops, IoT, Plant Hospital, admin, password reset aur routes ka main control yahin hai.
- `templates/base.html` → Common navbar, language selector, logo/branding, footer aur common layout.
- `templates/index.html` → Home + separate Plant Detection and Soil Detection UI.
- `static/js/app.js` → Shared frontend logic, camera, preview, chatbot formatting, UI interactions.
- `static/css/style.css` → Complete visual design.
- `templates/iot_dashboard.html` → IoT sensor dashboard UI.
- `templates/forgot_password.html` + `reset_password.html` → Forgot/reset password screens.
- `static/sw.js` → PWA service worker/offline caching.
- `static/manifest.json` → PWA install metadata; JSON me comments valid nahi hote, isliye explanation yahan hai.
- `gen_icons.py` → PWA icons generate karne ka helper script.
- `requirements.txt` → Python packages.
- `.env.example` → API keys/password/config ka template. Real secrets isme mat rakho.
- `render.yaml` + `Procfile` + `runtime.txt` → Render deployment configuration.
- `labels.txt` → Local TensorFlow Lite model ke 25 class labels.
- `model.tflite` → Local crop disease ML model. Binary file hai, isliye comments possible nahi.

## Feature → exact code location
| Feature | Main code |
|---|---|
| 🌿 Plant Detection | `app.py` prediction functions + `templates/index.html` plant section + `static/js/app.js` camera/UI |
| 🪨 Soil Detection | `app.py` soil/Gemini vision route + `templates/index.html` soil section + `static/js/app.js` UI |
| 🤖 Gemini Chatbot | `app.py` → `gemini_chat()` + chat API route; `static/js/app.js` → chat UI/formatting |
| 🌍 Languages | `app.py` → language list/translation helpers + `base.html` language selector |
| 📡 IoT | `app.py` → IoT routes/API + `templates/iot_dashboard.html` + `docs/iot-sensor-dashboard.md` |
| 🔐 Forgot Password | `app.py` → forgot/reset routes + `forgot_password.html` + `reset_password.html` + SMTP config in `.env` |
| 🌦️ Weather | `app.py` weather route/API + `index.html` weather UI + JS location/weather handling |
| 🌾 My Crops | `app.py` crop/timeline routes + crop timeline templates |
| 🩺 Plant Hospital | `app.py` expert/ticket routes + expert templates + docs |
| 📊 Dashboard/Admin | `app.py` + dashboard/admin templates |
| 📱 PWA/Install App | `base.html`, `manifest.json`, `sw.js`, `static/icons/` |
| 🎨 Logo | `base.html` nav logo markup + `static/icons/` actual app icons + `gen_icons.py` generator |
| 🔒 Security | `.env`, `.gitignore`, password hashing/reset token logic, security headers in `app.py` |

## Important rule
Real API keys, SMTP passwords, admin passwords aur database passwords **source code me hard-code nahi karne**. `.env` use karo aur `.env` GitHub par upload mat karo.
