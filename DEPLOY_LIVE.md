# RBAgriScan - local run and live deployment

## Local Windows setup

```bat
D:
cd \RBAgriScan-Final-100Lang-AnyPlant
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
python app.py
```

Open: `http://127.0.0.1:5000`

### MySQL for this project

The current XAMPP/MariaDB setup uses `root` with a blank password. Local `.env` should therefore contain:

```env
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DATABASE=smart_crop_ai
```

Create the database once in phpMyAdmin or MySQL:

```sql
CREATE DATABASE smart_crop_ai;
```

## GitHub

From the project folder:

```bat
git init
git add .
git commit -m "RBAgriScan mobile custom crops Google Translate"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/RBAgriScan.git
git push -u origin main
```

Do **not** commit `.env`, API keys, passwords, or database dumps.

## Render

1. Create a new Web Service from the GitHub repository.
2. Runtime: Python.
3. Build command:

```text
pip install -r requirements.txt
```

4. Start command:

```text
gunicorn app:app --workers 1 --threads 4 --timeout 120
```

5. Add these Environment Variables in Render:

```text
SECRET_KEY=<generate a strong secret>
FORCE_HTTPS=true
MYSQL_HOST=<external MySQL host>
MYSQL_PORT=3306
MYSQL_USER=<database user>
MYSQL_PASSWORD=<database password>
MYSQL_DATABASE=smart_crop_ai
PLANTNET_API_KEY=<your PlantNet key>
GEMINI_API_KEY=<your Gemini key>
ADMIN_EMAIL=<admin email>
ADMIN_PASSWORD=<strong admin password>
```

Render does not provide the project's MySQL database. Use an external managed MySQL/MariaDB service and allow the Render service to connect to it.

## Features in this update

- Responsive phone/tablet/laptop layout.
- Custom crop entry: farmer can enter any crop name.
- Five-stage default timeline for custom crops.
- Google Translate browser widget for broader language coverage.
- Existing server-side Google translation/cache remains available.
- Existing camera, chatbot voice, chatbot image upload, AI, PlantNet, Gemini, MySQL, Plant Hospital, IoT and PWA features are preserved.

## Python packages

No extra package is required for the browser Google Translate widget. The existing `deep-translator` package handles server-side Google translation, and `langdetect` handles chat-language detection.
