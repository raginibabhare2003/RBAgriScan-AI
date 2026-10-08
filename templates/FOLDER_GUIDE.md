# templates/ — UI Pages

Is folder me Flask/Jinja HTML pages hain. **Frontend ka page structure yahan hota hai.** Backend `app.py` data bhejta hai aur ye files us data ko screen par dikhati hain.

- `base.html` → common logo, navbar, language selector, footer, CSS/JS/PWA links.
- `index.html` → main 2-in-1 screen: Plant Scan + Soil Analysis + Weather + Gemini Assistant.
- `iot_dashboard.html` → IoT device registration, charts and alerts.
- `forgot_password.html` → email enter karke reset link lena.
- `reset_password.html` → new password + confirmation.
- `auth.html` → login/register.
- `dashboard.html` / `admin.html` → user/admin dashboard.
- `supported_plants.html` → supported plant list.
- `crop_timeline_*.html` → My Crops and growth timeline.
- `expert_*.html` → Plant Hospital/expert tickets.
- `about.html`, `contact.html`, `feedback.html`, `suggestions.html` → informational/feedback pages.
