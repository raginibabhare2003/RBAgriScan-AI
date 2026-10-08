# static/icons/ — App Logo & PWA Icons

Ye folder image assets rakhta hai:
- `icon-192.png` / `icon-512.png` → Android/PWA app icons
- `icon-512-maskable.png` → maskable PWA icon
- `apple-touch-icon.png` → iPhone/iPad home-screen icon
- `favicon-16.png` / `favicon-32.png` → browser tab favicon

Actual logo HTML link `templates/base.html` me hai. Icons generate karne ka Python helper `gen_icons.py` hai.
Image files ke andar source-code comments nahi hote, isliye ye guide explanation provide karta hai.
