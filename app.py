# ============================================================
# RBAgriScan BACKEND - MAIN FILE
# Ye Flask backend project ka central control room hai.
# Hinglish note: Frontend templates screen dikhate hain, lekin
# login, database, AI prediction, Gemini, translation, weather,
# IoT, Plant Hospital aur password reset ka main logic yahan hai.
# ============================================================
import os, time, json, re, io, math, secrets, concurrent.futures, smtplib, traceback, threading
from email.mime.text import MIMEText
from datetime import date, datetime, timedelta
import pymysql
import pymysql.cursors
from pymysql.err import IntegrityError
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory, abort
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix


try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from google import genai
    from google.genai import types
    GEMINI_SDK_AVAILABLE = True
except Exception:
    GEMINI_SDK_AVAILABLE = False
import numpy as np
from PIL import Image
import tensorflow as tf
import requests

try:
    from deep_translator import GoogleTranslator, MyMemoryTranslator
    TRANSLATOR_AVAILABLE = True
except Exception:
    TRANSLATOR_AVAILABLE = False

# MyMemory needs a full "language-COUNTRY" code (e.g. "hi-IN"), not our plain
# 2-letter codes, so map ours to theirs. Used as a fallback provider - on some
# networks Google Translate's scraping endpoint is blocked/unreachable even
# though the plain "requests" library itself has internet access (seen in the
# field: GoogleTranslator raises TranslationNotFound for every single word),
# so every on-demand translation used to silently fail and every language
# stayed in English forever. A couple of codes we support (Kurdish, Frisian,
# Corsican) aren't in MyMemory's language list either, so they'll still fall
# back to Google-only; everything else now has a working second provider.
# ---------- LANGUAGE / TRANSLATION SETUP ----------
# Hinglish: Supported language codes aur translation helpers.
# User language choose karta hai; app UI/chat/disease guidance ko same language me rakhne ke liye ye section use hota hai.
MYMEMORY_LANG_CODES = {
"en":"en-GB","hi":"hi-IN","mr":"mr-IN","bn":"bn-IN","gu":"gu-IN","ta":"ta-IN","te":"te-IN","kn":"kn-IN",
"ml":"ml-IN","pa":"pa-IN","ur":"ur-PK","or":"or-IN","as":"as-IN","ne":"ne-NP","si":"si-LK","sa":"sa-IN",
"ar":"ar-SA","fa":"fa-IR","he":"he-IL","tr":"tr-TR","az":"az-AZ","hy":"hy-AM","ka":"ka-GE","el":"el-GR",
"ru":"ru-RU","uk":"uk-UA","bg":"bg-BG","sr":"sr-ME","hr":"hr-HR","bs":"bs-BA","sl":"sl-SI","sk":"sk-SK",
"cs":"cs-CZ","pl":"pl-PL","ro":"ro-RO","hu":"hu-HU","de":"de-DE","nl":"nl-NL","da":"da-DK","sv":"sv-SE",
"no":"nb-NO","fi":"fi-FI","is":"is-IS","et":"et-EE","lv":"lv-LV","lt":"lt-LT","ga":"ga-IE","cy":"cy-GB",
"fr":"fr-FR","es":"es-ES","it":"it-IT","pt":"pt-PT","ca":"ca-ES","eu":"eu-ES","gl":"gl-ES","sw":"sw-KE",
"am":"am-ET","so":"so-SO","zu":"zu-ZA","xh":"xh-ZA","af":"af-ZA","yo":"yo-NG","ig":"ig-NG","ha":"ha-NE",
"rw":"rw-RW","ny":"ny-MW","mg":"mg-MG","sn":"sn-ZW","st":"st-LS","tn":"tn-BW","wo":"wo-SN","ko":"ko-KR",
"ja":"ja-JP","zh-CN":"zh-CN","zh-TW":"zh-TW","vi":"vi-VN","th":"th-TH","id":"id-ID","ms":"ms-MY",
"tl":"tl-PH","km":"km-KH","lo":"lo-LA","my":"my-MM","mn":"mn-MN","jv":"jv-ID","su":"su-ID","ceb":"ceb-PH",
"haw":"haw-US","mi":"mi-NZ","sm":"sm-WS","to":"to-TO","fj":"fj-FJ","ht":"ht-HT","la":"la-XN","eo":"eo-EU",
"lb":"lb-LU","mt":"mt-MT","sq":"sq-AL","mk":"mk-MK","be":"be-BY","kk":"kk-KZ","uz":"uz-UZ","tg":"tg-TJ",
"ky":"ky-KG","tk":"tk-TM","tt":"tt-RU","ps":"ps-PK","sd":"sd-PK","dv":"dv-MV","bo":"bo-CN","ug":"ug-CN",
"yi":"yi-YD","br":"br-FR","gd":"gd-GB",
}

# ---- translator circuit breaker -------------------------------------------------
# External translators (Google scraping / MyMemory) can be blocked, slow or rate
# limited. Without protection every page render waited for the timeouts again.
# After 3 consecutive total failures we stop calling them for 2 minutes and serve
# cached / bundled / English text instead, so pages never hang because of translation.
_TR_FAILS = 0
_TR_DOWN_UNTIL = 0.0
_TR_LOCK = threading.Lock()

def _translator_down():
    return time.time() < _TR_DOWN_UNTIL

def _translator_result(ok):
    global _TR_FAILS, _TR_DOWN_UNTIL
    with _TR_LOCK:
        if ok:
            _TR_FAILS = 0
        else:
            _TR_FAILS += 1
            if _TR_FAILS >= 3:
                _TR_DOWN_UNTIL = time.time() + 120
                _TR_FAILS = 0

def _translate(text, lang):
    """Translate `text` (English) into `lang`, trying Google Translate first
    and falling back to MyMemory if Google's endpoint fails (blocked network,
    rate limit, etc). Returns None (not the untranslated English text) when
    every provider fails, so callers can tell a real translation from a
    failure instead of silently treating English-as-translation as success."""
    if not TRANSLATOR_AVAILABLE or not text or _translator_down():
        return None
    try:
        result = GoogleTranslator(source="en", target=lang).translate(text)
        if result:
            _translator_result(True)
            return result
    except Exception:
        pass
    mm_target = MYMEMORY_LANG_CODES.get(lang)
    if mm_target:
        try:
            result = MyMemoryTranslator(source="en-GB", target=mm_target).translate(text)
            if result:
                _translator_result(True)
                return result
        except Exception:
            pass
    _translator_result(False)
    return None

try:
    from langdetect import detect_langs as _langdetect_detect_langs, DetectorFactory as _LangDetectFactory
    _LangDetectFactory.seed = 0  # deterministic results
    LANGDETECT_AVAILABLE = True
except Exception:
    LANGDETECT_AVAILABLE = False

def detect_message_language(text, fallback="en"):
    """Detect what language a free-typed chat message is in, so the assistant
    can reply in that same language ('any language' chat search/output), even
    if it differs from the language currently selected in the site's dropdown.
    Falls back to the site language whenever detection is short, ambiguous, or
    low-confidence (e.g. short romanized/mixed-script messages like "plant ko
    kaise bachaye" are very easy to misdetect as an unrelated language such as
    Estonian or French) — in that case we'd rather keep replying in the site's
    selected language than guess wrong and reply in the wrong language."""
    text = (text or "").strip()
    if not text or not LANGDETECT_AVAILABLE or len(text) < 15:
        return fallback
    try:
        candidates = _langdetect_detect_langs(text)
        if not candidates:
            return fallback
        top = candidates[0]
        code, confidence = top.lang, top.prob
    except Exception:
        return fallback
    if confidence < 0.90:
        return fallback
    # langdetect uses ISO 639-1 codes; a couple differ from our LANG_MAP keys.
    code = LANG_ALIASES.get(code.lower(), code)
    return code if code in LANG_MAP else fallback

app = Flask(__name__)
# Render and other reverse proxies terminate HTTPS before Flask. Trust one
# proxy hop so url_for(..., _external=True) generates the correct https link.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", "")
if not app.secret_key:
    # Never ship a predictable production session secret. Local setup should
    # provide SECRET_KEY through .env / environment variables.
    # NOTE: a random per-process key logs everyone out on every restart and breaks
    # sessions when gunicorn runs more than one worker -> always set SECRET_KEY.
    app.secret_key = secrets.token_hex(32)
    print("WARNING: SECRET_KEY is not set. Using a temporary random key; set SECRET_KEY in .env / Render env vars.")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "8")) * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

_RATE_LOCK = threading.Lock()
_RATE_BUCKETS = {}
_RATE_LIMIT = int(os.environ.get("APP_RATE_LIMIT_PER_MINUTE", "60"))

# Render terminates TLS before forwarding the request to Gunicorn. When deployed
# on Render, redirect any plain-HTTP browser request to HTTPS so mobile browsers
# can use camera/geolocation APIs. Local development remains HTTP-friendly.
# ---------- PRODUCTION HTTPS ----------
# Hinglish: Render/production par HTTPS force kiya ja sakta hai. Mobile camera ke liye secure HTTPS important hai.
FORCE_HTTPS = os.environ.get("FORCE_HTTPS", "true" if os.environ.get("RENDER") else "false").strip().lower() in {"1", "true", "yes", "on"}
app.config["SESSION_COOKIE_SECURE"] = FORCE_HTTPS

@app.before_request
def enforce_https_in_production():
    if not FORCE_HTTPS:
        return None
    forwarded_proto = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip().lower()
    host = request.host.split(":", 1)[0].lower()
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if forwarded_proto != "https" and host not in local_hosts:
        target = request.url.replace("http://", "https://", 1)
        return redirect(target, code=308)
    return None
# ---------- APP PATHS + DATABASE CONFIG ----------
# Hinglish: Model, labels, uploads aur MySQL configuration ke paths/env variables yahan define hote hain.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
MODEL_PATH = os.path.join(BASE_DIR, "model.tflite")

# MySQL connection settings (set these in .env / Render environment variables).
MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "smart_crop_ai")
LABELS_PATH = os.path.join(BASE_DIR, "labels.txt")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"jpg","jpeg","png","webp"}
# ---------- SUPPORTED PLANTS + CROP TIMELINE ----------
# Hinglish: Plant names aur growth stages/reminder templates. My Crops feature isi data ko use karta hai.
SUPPORTED_PLANTS = {
    "Apple": "Apple", "Corn_(maize)": "Corn", "Grape": "Grape",
    "Peach": "Peach", "Pepper,_bell": "Bell Pepper", "Potato": "Potato",
    "Strawberry": "Strawberry", "Tomato": "Tomato"
}

# ---------------------------------------------------------------------------
# Crop Timeline & Stage Tracker - reference data
# Baseline stage durations (days) for each supported crop, in order. This is
# static in-code reference data for the MVP; an enterprise build would move
# this into an admin-editable table (see docs/crop-timeline-tracker.md) so
# agronomists can tune it per region without a deploy.
# ---------------------------------------------------------------------------
CROP_STAGES = ["Seedling", "Vegetative", "Flowering", "Fruiting", "Harvesting"]
CROP_STAGE_TEMPLATES = {
    "Tomato":        [14, 28, 14, 30, 10],
    "Potato":        [14, 35, 14, 35, 10],
    "Grape":         [21, 45, 21, 60, 14],
    "Apple":         [21, 60, 14, 90, 21],
    "Corn_(maize)":  [10, 40, 14, 30, 10],
    "Pepper,_bell":  [21, 30, 14, 35, 14],
    "Strawberry":    [14, 21, 14, 30, 21],
    "Peach":         [21, 55, 14, 70, 14],
}

def crop_display_name(crop_key):
    """Return a human-friendly crop name, including farmer-entered custom crops."""
    key = str(crop_key or "").strip()
    if key.startswith("custom:"):
        return key[7:].strip() or "Custom Crop"
    return key.replace("_", " ").replace("(maize)", "").replace(",", " ").strip()

# Make the helper available to every Jinja template.
app.jinja_env.globals["crop_display_name"] = crop_display_name

# A day-length/temperature-driven multiplier: warm, well-lit conditions speed
# growth up (shorter stages); cold/overcast conditions slow it down.
def _season_speed_multiplier(avg_temp_c):
    if avg_temp_c is None:
        return 1.0
    if avg_temp_c < 10: return 1.35   # cold - growth slows a lot
    if avg_temp_c < 15: return 1.15
    if avg_temp_c <= 28: return 1.0   # optimal range for most of these crops
    if avg_temp_c <= 34: return 0.92  # warm - modest speed-up
    return 1.1                        # too hot - heat stress slows things again

def estimate_crop_plan(crop_key, planted_on, avg_temp_c=None):
    """Return [{stage, order, start, end, days}] for a crop planted on `planted_on`."""
    base_days = CROP_STAGE_TEMPLATES.get(crop_key, [14, 28, 14, 30, 14])
    mult = _season_speed_multiplier(avg_temp_c)
    plan = []
    cursor = planted_on
    for i, (name, days) in enumerate(zip(CROP_STAGES, base_days), start=1):
        adj_days = max(3, round(days * mult))
        start = cursor
        end = start + timedelta(days=adj_days)
        plan.append({"order": i, "stage": name, "days": adj_days, "start": start, "end": end})
        cursor = end
    return plan

# Strict rejection: a prediction is accepted only if it is confident AND
# stable across several image views. This prevents forcing every image into a class.
MIN_CONFIDENCE = 0.72
MIN_MARGIN = 0.12
AUGMENT_VIEWS = 3

LANGUAGES = [
("en","English"),("hi","Hindi"),("mr","Marathi"),("bn","Bengali"),("gu","Gujarati"),("ta","Tamil"),
("te","Telugu"),("kn","Kannada"),("ml","Malayalam"),("pa","Punjabi"),("ur","Urdu"),("or","Odia"),
("as","Assamese"),("ne","Nepali"),("si","Sinhala"),("sa","Sanskrit"),("ar","Arabic"),("fa","Persian"),
("he","Hebrew"),("tr","Turkish"),("az","Azerbaijani"),("hy","Armenian"),("ka","Georgian"),("el","Greek"),
("ru","Russian"),("uk","Ukrainian"),("bg","Bulgarian"),("sr","Serbian"),("hr","Croatian"),("bs","Bosnian"),
("sl","Slovenian"),("sk","Slovak"),("cs","Czech"),("pl","Polish"),("ro","Romanian"),("hu","Hungarian"),
("de","German"),("nl","Dutch"),("da","Danish"),("sv","Swedish"),("no","Norwegian"),("fi","Finnish"),
("is","Icelandic"),("et","Estonian"),("lv","Latvian"),("lt","Lithuanian"),("ga","Irish"),("cy","Welsh"),
("fr","French"),("es","Spanish"),("it","Italian"),("pt","Portuguese"),("ca","Catalan"),("eu","Basque"),
("gl","Galician"),("sw","Swahili"),("am","Amharic"),("so","Somali"),("zu","Zulu"),("xh","Xhosa"),
("af","Afrikaans"),("yo","Yoruba"),("ig","Igbo"),("ha","Hausa"),("rw","Kinyarwanda"),("ny","Chichewa"),
("mg","Malagasy"),("sn","Shona"),("st","Sesotho"),("tn","Tswana"),("wo","Wolof"),("ko","Korean"),
("ja","Japanese"),("zh-CN","Chinese Simplified"),("zh-TW","Chinese Traditional"),("vi","Vietnamese"),
("th","Thai"),("id","Indonesian"),("ms","Malay"),("tl","Filipino"),("km","Khmer"),("lo","Lao"),
("my","Burmese"),("mn","Mongolian"),("ne","Nepali"),("jv","Javanese"),("su","Sundanese"),
("ceb","Cebuano"),("haw","Hawaiian"),("mi","Maori"),("sm","Samoan"),("to","Tongan"),("fj","Fijian"),
("ht","Haitian Creole"),("la","Latin"),("eo","Esperanto"),("lb","Luxembourgish"),("mt","Maltese"),
("sq","Albanian"),("mk","Macedonian"),("be","Belarusian"),("kk","Kazakh"),("uz","Uzbek"),("tg","Tajik"),
("ky","Kyrgyz"),("tk","Turkmen"),("tt","Tatar"),("ps","Pashto"),("ku","Kurdish"),("sd","Sindhi"),
("dv","Dhivehi"),("bo","Tibetan"),("ug","Uyghur"),("yi","Yiddish"),("fy","Frisian"),("co","Corsican"),
("la","Latin"),("br","Breton"),("gd","Scots Gaelic"),("jv","Javanese"),("su","Sundanese")
]
# Remove duplicate language codes while preserving the first display name.
LANGUAGES = list(dict(LANGUAGES).items())
# Sort the dropdown alphabetically by display name (English stays first so it's
# always the easiest to find/default to; everything else is A-Z).
_en_entry = [e for e in LANGUAGES if e[0] == "en"]
_rest_sorted = sorted((e for e in LANGUAGES if e[0] != "en"), key=lambda e: e[1].lower())
LANGUAGES = _en_entry + _rest_sorted
LANG_MAP = dict(LANGUAGES)
# Canonical aliases used by language detection / translation providers.
LANG_ALIASES = {"zh-cn": "zh-CN", "zh-tw": "zh-TW", "iw": "he", "in": "id", "jw": "jv"}
# Languages that read right-to-left, so the layout can flip via <html dir="rtl">.
RTL_LANGS = {"ar","fa","he","ur","ps","ku","sd","dv","ug","yi"}

# ---------- PLANTNET / GBIF PLANT IDENTIFICATION ----------
# Hinglish: PlantNet broad plant identification karta hai; result ko local disease model se alag rakha jata hai.
PLANTNET_API_KEY = os.environ.get("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = os.environ.get("PLANTNET_PROJECT", "all").strip() or "all"
PLANTNET_MIN_CONFIDENCE = float(os.environ.get("PLANTNET_MIN_CONFIDENCE", "0.45"))
PLANTNET_ENABLED = bool(PLANTNET_API_KEY)
PLANTNET_URL = "https://my-api.plantnet.org/v2/identify"

# ---------- GEMINI AI: CHATBOT + VISION ----------
# Hinglish: Gemini multilingual farmer assistant aur photo-based AI analysis ke liye use hota hai.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash").strip()
# Hybrid vision settings: local TFLite first, Gemini Vision for unsupported/uncertain images.
GEMINI_VERIFY_LOCAL = os.environ.get("GEMINI_VERIFY_LOCAL", "false").strip().lower() in {"1", "true", "yes", "on"}
GEMINI_ENABLED = bool(GEMINI_API_KEY and GEMINI_SDK_AVAILABLE)
gemini_client = None
if GEMINI_ENABLED:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print("Gemini client error:", e)
        GEMINI_ENABLED = False

def _classify_gemini_error(e):
    """Turn a raw Gemini/SDK exception into a short, safe reason code + a
    farmer-facing message, so the UI stops showing one generic
    'temporarily unavailable' line for every possible failure (quota,
    bad/missing key, wrong model name, network, bad JSON, etc.)."""
    text = str(e)
    low = text.lower()
    if not GEMINI_ENABLED:
        return "not_configured", "AI soil analysis needs GEMINI_API_KEY configured."
    if "429" in text or "resource_exhausted" in low or "quota" in low:
        return "quota_exhausted", "Daily AI quota is used up for today. Please try again after the quota resets (usually next day), or upload a new Gemini API key."
    if "api key not valid" in low or "api_key_invalid" in low or "401" in text or "permission_denied" in low or "403" in text:
        return "invalid_key", "GEMINI_API_KEY looks invalid or unauthorized. Please check the key in .env / Render environment settings."
    if "404" in text or "not_found" in low or "not found" in low:
        return "bad_model", f"Configured Gemini model '{GEMINI_MODEL}' was not found or is unavailable. Please check GEMINI_MODEL in .env."
    if "not valid json" in low or "json" in low and "not" in low:
        return "bad_json", "AI returned an unexpected response for this photo. Please try again with a clearer soil photo."
    if "timeout" in low or "timed out" in low:
        return "timeout", "AI request timed out. Please check your internet connection and try again."
    return "unknown", "Soil image analysis is temporarily unavailable. Please try again."

def _gemini_text(prompt, image_path=None):
    if not GEMINI_ENABLED or gemini_client is None:
        raise RuntimeError("Gemini API is not configured. Set GEMINI_API_KEY in .env.")

    contents = [prompt]

    if image_path:
        mime = "image/jpeg"
        ext = os.path.splitext(image_path)[1].lower()

        if ext == ".png":
            mime = "image/png"
        elif ext == ".webp":
            mime = "image/webp"

        with open(image_path, "rb") as f:
            image_part = types.Part.from_bytes(
                data=f.read(),
                mime_type=mime
            )

        contents = [image_part, prompt]

    # Primary model + fallback models
    models_to_try = [
        GEMINI_MODEL,
        "gemini-3.7-flash",
        "gemini-3.6-flash"
    ]

    # Remove duplicate model names
    models_to_try = list(dict.fromkeys(models_to_try))

    last_error = None

    for model_name in models_to_try:
        try:
            print(f"Gemini request -> trying model: {model_name}")

            response = gemini_client.models.generate_content(
                model=model_name,
                contents=contents
            )

            text = getattr(response, "text", None)

            if text:
                print(f"Gemini request -> success: {model_name}")
                return text.strip()

            last_error = RuntimeError(
                f"Gemini returned an empty response from {model_name}."
            )

        except Exception as e:
            last_error = e

            print(
                f"Gemini request -> failed: {model_name} -> "
                f"{type(e).__name__}: {e}"
            )

            # Try the next model automatically
            continue

    raise RuntimeError(
        "Gemini is temporarily unavailable. "
        "All configured Gemini models failed. "
        f"Last error: {last_error}"
    )
    return text.strip()

def gemini_validate_plant(path, lang="en"):
    """Strict plant-only gate. It must pass before local disease inference when
    PlantNet is unavailable. Failure is intentionally conservative: unknown."""
    if not GEMINI_ENABLED:
        return None
    prompt = f"""
You are a strict image validator for an agricultural plant-disease app.
Inspect the attached image and return ONLY valid JSON with keys: is_plant, plant,
confidence, supported_crop. is_plant must be true only when the main subject is
a real visible plant/leaf/crop, not a person, animal, building, food, car, phone,
screenshot, sky, road, soil-only image, or random object. confidence is 0-100.
Do not guess a crop from a background. supported_crop should be the most reliable common plant/crop name you can identify; it may be ANY plant, not only a fixed list.
If the image clearly shows a plant but the exact species is uncertain, still set is_plant=true and use a broad name such as "Unknown plant".
If uncertain that the subject is a plant, set is_plant=false and confidence below 50.
"""
    raw = _gemini_text(prompt, path)
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
    data = json.loads(cleaned)
    is_plant = bool(data.get("is_plant")) and float(data.get("confidence") or 0) >= 70
    return {"is_plant": is_plant, "plant": str(data.get("plant") or "Unknown"),
            "confidence": float(data.get("confidence") or 0),
            "supported_crop": str(data.get("supported_crop") or "Unknown")}

def gemini_detect(path, lang):
    language_name = LANG_MAP.get(lang, "English")
    prompt = f"""
You are the AI fallback plant-disease expert for RBAgriScan.
Analyze the attached plant/leaf image. The local PlantVillage model may not support this plant.
Return ONLY valid JSON with these keys:
plant, disease, confidence, explanation, home_remedies, natural, field, chemical, prevention.
confidence must be a number from 0 to 100.
Use "{language_name}" for all text values.
If the image is not a plant/leaf or cannot be assessed reliably, set plant and disease to "Unknown" and confidence below 30.
Identify the plant at the broadest reliable level (common name, and scientific name inside explanation when useful).
Disease may be "Healthy", "Unknown", or a likely disease; do not invent a precise disease when the visual evidence is weak.
Give practical farmer-safe guidance.
For chemical advice, do not give dangerous mixing instructions; recommend following a locally approved product label or agricultural expert.
"""
    text = _gemini_text(prompt, path)
    # Remove common markdown JSON fences if Gemini adds them.
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    try:
        data = json.loads(cleaned)
    except Exception:
        m = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not m:
            raise RuntimeError("Gemini response was not valid JSON.")
        data = json.loads(m.group(0))
    data.setdefault("plant", "Unknown")
    data.setdefault("disease", "Unknown")
    data.setdefault("confidence", 0)
    data.setdefault("explanation", "")
    for key in ("home_remedies", "natural", "field", "chemical", "prevention"):
        value = data.get(key, [])
        data[key] = value if isinstance(value, list) else [str(value)]
    data["confidence"] = float(data.get("confidence") or 0)
    data["unknown"] = str(data["plant"]).lower() == "unknown" or data["confidence"] < 30
    return data

# CHATBOT CORE
# Hinglish: User ka agriculture question Gemini ko bhejta hai. Selected language aur previous chat history context ke saath answer generate hota hai.
def gemini_chat(message, lang, history=None):
    language_name = LANG_MAP.get(lang, "English")
    history = history or []
    compact = []
    for item in history[-8:]:
        if isinstance(item, dict):
            role = "User" if item.get("role") == "user" else "Assistant"
            content = str(item.get("content", ""))[:1200]
            compact.append(f"{role}: {content}")
    history_text = "\n".join(compact)
    current_plant = session.get("last_plant", "Unknown")
    current_disease = session.get("last_class", "Unknown")
    current_conf = session.get("last_confidence", "")
    context = f"Current detection context: plant={current_plant}; disease={current_disease}; confidence={current_conf}."
    prompt = f"""
You are RBAgriScan Farmer Assistant. Reply in {language_name} using simple, natural language suitable for a farmer. If the user's message is clearly written in another language or script, reply in that language instead of forcing the selected website language. For short or romanized messages, preserve the user's wording style when possible.
{context}
Answer the user's question even when it is outside the detection result. Cover agriculture and general plant/farming questions such as crops, diseases, pests, soil, irrigation, fertilizer, weather, cultivation, harvesting, farm planning, and plant identification. If the user asks a non-agriculture question, still answer helpfully when possible, or clearly say what you cannot verify. Never invent facts. If the question needs current local information, say that live data/source verification is needed. Be practical and easy to understand.
Do not claim certainty from symptoms alone. Do not prescribe unsafe pesticide mixing.
If chemical control is discussed, advise using a locally approved product exactly as
its label says and consulting a local agriculture expert when needed.
Conversation:
{history_text}

User: {message}
Assistant:
"""
    return _gemini_text(prompt)

# ---------- BUILT-IN UI TRANSLATIONS ----------
# Hinglish: Common buttons/headings/navigation text ke translations.
TEXTS = {
"en":{"title":"RBAgriScan","brand":"RBAgriScan","home":"Home","about":"About","feedback":"Farmer Feedback",
"login":"Login","dashboard":"Dashboard","logout":"Logout","camera":"Live Camera","upload_title":"AI Plant Disease Detection",
"hero_title":"Smart Plant Disease Detection for Farmers","hero_text":"Upload a leaf photo or use your camera. The AI rejects uncertain images instead of guessing.",
"upload":"Choose Image","detect":"Detect","scan_plant":"Scan Plant","location":"Use My Location","weather":"Weather & Environment","result":"Detection Result",
"uploaded":"Uploaded Image","plant":"Plant","disease":"Disease","confidence":"Confidence","status":"Status","precautions":"Farmer Guidance",
"home_remedies":"Home / Basic Care","natural":"Natural Management","field":"Field Management","chemical":"Chemical Control","prevention":"Prevention",
"unknown":"Plant Not Identified","unknown_text":"The image is not confidently recognized as one of the supported PlantVillage classes. Please use a clear leaf photo.",
"feedback_title":"Farmer Feedback","name":"Name","message":"Message","send":"Send Feedback","about_title":"About RBAgriScan",
"dashboard_title":"Farmer Dashboard","history":"Recent Scans","no_history":"No scans yet.","email":"Email","password":"Password","register":"Create Account",
"login_title":"Farmer Login","register_title":"Farmer Registration","new_user":"New farmer? Register","existing_user":"Already registered? Login",
"camera_help":"Allow camera access, center one leaf, then capture.","capture":"Capture","retake":"Retake","use_photo":"Use Photo",
"location_help":"Location is used only to fetch local weather. You can also enter a city manually.","city":"City","get_weather":"Get Weather",
"not_available":"Not available","weather_error":"Could not fetch weather. Check internet/location.","safe_note":"AI result is a screening aid; confirm important decisions with a local agricultural expert.",
"language":"Language","welcome":"Welcome","email_exists":"Email already registered.","invalid_login":"Invalid email or password.","feedback_saved":"Thank you for your feedback.",
"forgot_password":"Forgot password?","forgot_password_text":"Enter your registered email and we'll send you a link to reset your password.","send_reset_link":"Send Reset Link","reset_link_sent":"If that email is registered, a password reset link has been sent.","reset_link_dev_note":"Email sending isn't configured yet, so here is your reset link:","reset_password_title":"Reset Password","new_password":"New Password","confirm_password":"Confirm New Password","reset_invalid":"This reset link is invalid or has expired. Please request a new one.","reset_success":"Your password has been reset. Please login.","password_mismatch":"Passwords do not match, or are too short (minimum 6 characters).","change_password":"Change Password","current_password":"Current Password","password_changed":"Your password has been changed.","reset_email_subject":"Reset your RBAgriScan password","reset_email_body":"We received a request to reset your RBAgriScan password. Click the link below to choose a new password. This link expires in 1 hour. If you did not request this, you can ignore this email.",
"contact_title":"Contact Us","contact_text":"Have a question or need help? Send us a message and our team will get back to you.","send_message":"Send Message","contact_saved":"Thank you, we received your message.",
"suggestion_title":"Suggestions","suggestion_text":"Share ideas to help us improve RBAgriScan for farmers like you.","suggestion_placeholder":"Write your suggestion...","send_suggestion":"Send Suggestion","suggestion_saved":"Thank you for your suggestion.","recent_suggestions":"Recent Suggestions","no_suggestions":"No suggestions yet. Be the first!",
"rating":"Rating","your_rating":"Your Rating","recent_feedback":"Recent Farmer Feedback","avg_rating":"Average Rating","no_feedback":"No feedback yet.",
"nav_detection":"Detection","nav_weather":"Weather","nav_assistant":"Assistant","nav_plants":"Plants We Detect","nav_contact":"Contact","nav_suggestions":"Suggestions","nav_install":"Install App","nav_refresh":"Refresh",
"back":"Back","close":"Close","flip_camera":"Flip Camera","offline":"You're offline","back_online":"Back online.","offline_browse":"Browsing cached pages - detection, weather and chat need a connection.","offline_detection":"You're offline. Uploading and camera detection need a connection - your photo will not be analyzed until you're back online.",
"camera_unavailable":"Camera is not available in this browser.","camera_permission":"Camera permission is required.","camera_denied":"Camera permission was denied. Please allow camera access for this site.","camera_not_found":"No camera was found on this device.","camera_in_use":"The camera is already in use by another app.","camera_secure":"Camera needs a secure HTTPS connection on mobile browsers.","open_https":"Open the secure HTTPS version","camera_not_ready":"Camera is not ready yet. Please wait a moment and try again.","camera_capture_failed":"Could not capture the photo. Please try again.","offline_detect":"You're offline - plant scanning needs an internet connection. Your photo wasn't sent.",
"unrecognized":"Unrecognized","confidence_label":"Confidence","gemini_fallback":"Gemini AI fallback","server_error":"Could not reach the server. Check your connection and try again.","chat_you":"You","chat_assistant":"RBAgriScan","chat_offline":"RBAgriScan Offline Assistant","chat_offline_msg":"You're offline right now, so the assistant can't respond. Your question wasn't sent - try again once you're back online.","chat_server_error":"Could not reach the server.","chat_empty":"Please enter a question.","chat_general_prompt":"Ask about crops, plants, diseases, pests, irrigation, fertilizer, weather, farming practices, soil, market planning, or any other agriculture question.","send":"Send","my_crops":"My Crops","iot_sensors":"IoT Sensors","plant_hospital":"Plant Hospital","admin":"Admin",
"weather_loading":"Loading...","weather_offline":"You're offline - weather needs an internet connection.","weather_rain":"Rain","weather_wind":"Wind","humidity":"humidity","location_denied":"Location permission was not granted.","geo_unsupported":"Geolocation is not supported.","weather_server_error":"Could not reach the server.",
"plants_title":"Plants We Can Detect","plants_description":"These are the crop types and disease classes currently supported by the local model. Upload a clear leaf image for the best result.","supported_class":"supported class","supported_classes":"supported classes",
"timeline_title":"Automated Crop Timeline","timeline_text":"Track each planting from seedling to harvest, with stage-by-stage duration estimates that adjust to local weather, plus watering and care reminders.","log_crop":"Log a new crop","crop":"Crop","nickname":"Nickname (optional)","planted_on":"Planted on","city_weather":"City (for weather-adjusted timing)","start_tracking":"Start Tracking","your_crops":"Your crops","planted":"Planted","stage":"Stage","reminders_due":"reminders due","no_crops":"No crops logged yet - add your first one above.","status":"Status","growth_timeline":"Growth Timeline","stage_history":"Stage history","advance_to":"Advance to","mark_harvested":"Mark as Harvested","done":"Done","all_crops":"All crops","optional_note":"Optional note about this transition","reminder_due":"due","overdue":"overdue",
"hospital":"Plant Hospital","hospital_farmer":"Stuck on a diagnosis? Escalate it to a verified agronomist.","hospital_expert":"Triage queue - open and assigned diagnostic tickets from farmers.","open_ticket":"Open a diagnostic ticket","attach_scan":"Attach a recent scan (optional)","none":"none","city_region":"City / region","describe":"Describe what's happening","describe_placeholder":"What did the AI say, and what are you still unsure about?","submit_expert":"Submit to an agronomist","your_tickets":"Your tickets","queue":"Queue","farmer":"Farmer","prediction":"Prediction","opened":"Opened","open":"Open","nothing_here":"Nothing here yet.","ticket":"Ticket","unclassified":"Unclassified","no_location":"No location given","says":"says","claim":"Claim this ticket","conversation":"Conversation","no_messages":"No messages yet.","write_reply":"Write a reply...","send":"Send","mark_resolved":"Mark resolved","reopen_claim":"Reopen (claim)","thread_note":"This conversation refreshes after sending. Live WebSocket updates can be added in a production deployment.",
"iot_title":"Smart Irrigation Dashboard","iot_text":"Connect soil moisture, temperature, humidity or NPK sensors and watch live trends.","register_device":"Register a new device","device_name":"Device name","device_placeholder":"e.g. Greenhouse bed 2","link_crop":"Link to a crop (optional)","create_device":"Create Device","device_key_note":"You'll get a one-time device key to paste into your sensor configuration.","your_devices":"Your devices","no_devices":"No devices yet - register one above.","ingesting":"Ingesting data","seven_day":"7-day trend","thirty_day":"30-day trend","soil_moisture":"Soil moisture %","temp":"Temp °C","humidity_chart":"Humidity %","alert_threshold":"Readings under 25% soil moisture, or temperature ≥40°C / ≤2°C, automatically raise an alert.",
"admin_title":"Admin Control Center","admin_text":"Manage farmer scan history and review project activity.","users":"Users","scans":"Scans","contacts":"Contacts","suggestions":"Suggestions","scan_data":"Scan Data","delete_all_scans":"Delete All Dashboard Scan Data","admin_only":"Only admin accounts can perform this action.","admin_no_contacts":"No contact messages.","admin_no_feedback":"No feedback yet.","admin_no_suggestions":"No suggestions yet.",
"about_ai":"Strict confidence, top-2 margin and multi-view stability checks reduce forced predictions.","about_access":"Upload images or use a live camera on supported browsers.","about_environment":"Browser location or city can be used with weather data.","about_languages":"100+ language options are available. UI, guidance and assistant responses use the selected language when translation service is available.","about_pwa":"Install the site like an app. Previously visited pages can be cached for offline browsing; live detection, weather and AI chat require a connection.",
"project_contact":"Project Owner Contact","get_in_touch":"Get In Touch","mobile":"Mobile","recent":"Recent","help_us":"Help Us Improve","no_data":"No data available."},
"hi":{"title":"स्मार्ट क्रॉप AI","brand":"स्मार्ट क्रॉप AI","home":"होम","about":"हमारे बारे में","feedback":"किसान प्रतिक्रिया","login":"लॉगिन","dashboard":"डैशबोर्ड","logout":"लॉगआउट","camera":"लाइव कैमरा",
"upload_title":"AI पौधा रोग पहचान","hero_title":"किसानों के लिए स्मार्ट पौधा रोग पहचान","hero_text":"पत्ती की फोटो अपलोड करें या कैमरा उपयोग करें। AI अनिश्चित फोटो पर जबरदस्ती अनुमान नहीं लगाएगा।",
"upload":"फोटो चुनें","detect":"स्कैन करें","scan_plant":"पौधा स्कैन करें","location":"मेरी लोकेशन","weather":"मौसम और वातावरण","result":"पहचान परिणाम","uploaded":"अपलोड फोटो","plant":"पौधा","disease":"रोग","confidence":"विश्वास स्तर","status":"स्थिति","precautions":"किसान मार्गदर्शन","home_remedies":"घरेलू/बुनियादी देखभाल","natural":"प्राकृतिक प्रबंधन","field":"खेत प्रबंधन","chemical":"रासायनिक नियंत्रण","prevention":"रोकथाम",
"unknown":"पौधा पहचाना नहीं गया","unknown_text":"फोटो समर्थित PlantVillage वर्गों में पर्याप्त विश्वास के साथ नहीं पहचानी गई। साफ पत्ती की फोटो लें।","feedback_title":"किसान प्रतिक्रिया","name":"नाम","message":"संदेश","send":"प्रतिक्रिया भेजें","about_title":"स्मार्ट क्रॉप AI के बारे में",
"dashboard_title":"किसान डैशबोर्ड","history":"हाल की स्कैन","no_history":"अभी कोई स्कैन नहीं है।","email":"ईमेल","password":"पासवर्ड","register":"खाता बनाएं","login_title":"किसान लॉगिन","register_title":"किसान पंजीकरण","new_user":"नए किसान? पंजीकरण करें","existing_user":"पहले से खाता है? लॉगिन करें",
"camera_help":"कैमरा अनुमति दें, एक पत्ती को बीच में रखें और फोटो लें।","capture":"फोटो लें","retake":"दोबारा लें","use_photo":"फोटो उपयोग करें","location_help":"लोकेशन का उपयोग केवल स्थानीय मौसम के लिए होता है। शहर भी डाल सकते हैं।","city":"शहर","get_weather":"मौसम देखें","not_available":"उपलब्ध नहीं","weather_error":"मौसम नहीं मिल सका। इंटरनेट/लोकेशन जांचें।","safe_note":"AI परिणाम केवल स्क्रीनिंग सहायता है; महत्वपूर्ण निर्णय के लिए स्थानीय कृषि विशेषज्ञ से पुष्टि करें।","language":"भाषा","welcome":"स्वागत है","email_exists":"यह ईमेल पहले से पंजीकृत है।","invalid_login":"ईमेल या पासवर्ड गलत है।","feedback_saved":"आपकी प्रतिक्रिया के लिए धन्यवाद।",
"forgot_password":"पासवर्ड भूल गए?","forgot_password_text":"अपना पंजीकृत ईमेल डालें, हम आपको पासवर्ड रीसेट करने का लिंक भेजेंगे।","send_reset_link":"रीसेट लिंक भेजें","reset_link_sent":"अगर यह ईमेल पंजीकृत है, तो पासवर्ड रीसेट लिंक भेज दिया गया है।","reset_link_dev_note":"ईमेल भेजने की सुविधा अभी सेट नहीं है, इसलिए यह रहा आपका रीसेट लिंक:","reset_password_title":"पासवर्ड रीसेट करें","new_password":"नया पासवर्ड","confirm_password":"नया पासवर्ड फिर से डालें","reset_invalid":"यह रीसेट लिंक अमान्य है या समय समाप्त हो गया है। कृपया नया लिंक मांगें।","reset_success":"आपका पासवर्ड रीसेट हो गया है। कृपया लॉगिन करें।","password_mismatch":"पासवर्ड मेल नहीं खाते, या बहुत छोटे हैं (कम से कम 6 अक्षर)।","change_password":"पासवर्ड बदलें","current_password":"मौजूदा पासवर्ड","password_changed":"आपका पासवर्ड बदल दिया गया है।","reset_email_subject":"अपना RBAgriScan पासवर्ड रीसेट करें","reset_email_body":"हमें आपका RBAgriScan पासवर्ड रीसेट करने का अनुरोध मिला है। नया पासवर्ड चुनने के लिए नीचे दिए गए लिंक पर क्लिक करें। यह लिंक 1 घंटे में समाप्त हो जाएगा। यदि आपने यह अनुरोध नहीं किया है, तो इस ईमेल को अनदेखा करें।",
"contact_title":"संपर्क करें","contact_text":"कोई सवाल है या मदद चाहिए? हमें संदेश भेजें, हमारी टीम जल्द जवाब देगी।","send_message":"संदेश भेजें","contact_saved":"धन्यवाद, आपका संदेश मिल गया है।",
"suggestion_title":"सुझाव","suggestion_text":"स्मार्ट क्रॉप AI को बेहतर बनाने के लिए अपने विचार साझा करें।","suggestion_placeholder":"अपना सुझाव लिखें...","send_suggestion":"सुझाव भेजें","suggestion_saved":"आपके सुझाव के लिए धन्यवाद।","recent_suggestions":"हाल के सुझाव","no_suggestions":"अभी कोई सुझाव नहीं है। सबसे पहले आप बनें!",
"rating":"रेटिंग","your_rating":"आपकी रेटिंग","recent_feedback":"हाल की किसान प्रतिक्रिया","avg_rating":"औसत रेटिंग","no_feedback":"अभी कोई प्रतिक्रिया नहीं है।"},
"mr":{"title":"स्मार्ट क्रॉप AI","brand":"स्मार्ट क्रॉप AI","home":"मुख्यपृष्ठ","about":"आमच्याबद्दल","feedback":"शेतकरी अभिप्राय","login":"लॉगिन","dashboard":"डॅशबोर्ड","logout":"लॉगआउट","camera":"लाइव्ह कॅमेरा",
"upload_title":"AI वनस्पती रोग ओळख","hero_title":"शेतकऱ्यांसाठी स्मार्ट वनस्पती रोग ओळख","hero_text":"पानाचा फोटो अपलोड करा किंवा कॅमेरा वापरा. AI अनिश्चित फोटोवर जबरदस्ती अंदाज लावणार नाही.",
"upload":"फोटो निवडा","detect":"ओळखा","scan_plant":"पौधा स्कॅन करा","location":"माझे स्थान","weather":"हवामान आणि पर्यावरण","result":"ओळख परिणाम","uploaded":"अपलोड फोटो","plant":"वनस्पती","disease":"रोग","confidence":"विश्वास पातळी","status":"स्थिती","precautions":"शेतकरी मार्गदर्शन","home_remedies":"घरगुती/मूलभूत काळजी","natural":"नैसर्गिक व्यवस्थापन","field":"शेत व्यवस्थापन","chemical":"रासायनिक नियंत्रण","prevention":"प्रतिबंध",
"unknown":"वनस्पती ओळखली नाही","unknown_text":"फोटो समर्थित PlantVillage वर्गांमध्ये पुरेशा विश्वासाने ओळखला गेला नाही. स्वच्छ पानाचा फोटो घ्या.","feedback_title":"शेतकरी अभिप्राय","name":"नाव","message":"संदेश","send":"अभिप्राय पाठवा","about_title":"स्मार्ट क्रॉप AI बद्दल",
"dashboard_title":"शेतकरी डॅशबोर्ड","history":"अलीकडील स्कॅन","no_history":"अजून स्कॅन नाही.","email":"ईमेल","password":"पासवर्ड","register":"खाते तयार करा","login_title":"शेतकरी लॉगिन","register_title":"शेतकरी नोंदणी","new_user":"नवीन शेतकरी? नोंदणी करा","existing_user":"खाते आहे? लॉगिन करा",
"camera_help":"कॅमेरा परवानगी द्या, एक पान मध्यभागी ठेवा आणि फोटो घ्या.","capture":"फोटो घ्या","retake":"पुन्हा घ्या","use_photo":"फोटो वापरा","location_help":"स्थानाचा वापर फक्त स्थानिक हवामानासाठी केला जातो. शहर टाकू शकता.","city":"शहर","get_weather":"हवामान पहा","not_available":"उपलब्ध नाही","weather_error":"हवामान मिळू शकले नाही. इंटरनेट/स्थान तपासा.","safe_note":"AI निकाल फक्त स्क्रीनिंगसाठी आहे; महत्त्वाच्या निर्णयासाठी स्थानिक कृषी तज्ज्ञांचा सल्ला घ्या.","language":"भाषा","welcome":"स्वागत","email_exists":"हा ईमेल आधीच नोंदणीकृत आहे.","invalid_login":"ईमेल किंवा पासवर्ड चुकीचा आहे.","feedback_saved":"अभिप्रायाबद्दल धन्यवाद.",
"forgot_password":"पासवर्ड विसरलात?","forgot_password_text":"तुमचा नोंदणीकृत ईमेल टाका, आम्ही तुम्हाला पासवर्ड रीसेट लिंक पाठवू.","send_reset_link":"रीसेट लिंक पाठवा","reset_link_sent":"जर हा ईमेल नोंदणीकृत असेल, तर पासवर्ड रीसेट लिंक पाठवली गेली आहे.","reset_link_dev_note":"ईमेल पाठवण्याची सुविधा अद्याप सेट केलेली नाही, त्यामुळे हा तुमचा रीसेट लिंक आहे:","reset_password_title":"पासवर्ड रीसेट करा","new_password":"नवीन पासवर्ड","confirm_password":"नवीन पासवर्ड पुन्हा टाका","reset_invalid":"हा रीसेट लिंक अवैध आहे किंवा कालबाह्य झाला आहे. कृपया नवीन लिंकची विनंती करा.","reset_success":"तुमचा पासवर्ड रीसेट झाला आहे. कृपया लॉगिन करा.","password_mismatch":"पासवर्ड जुळत नाहीत, किंवा खूप लहान आहेत (किमान 6 अक्षरे).","change_password":"पासवर्ड बदला","current_password":"सध्याचा पासवर्ड","password_changed":"तुमचा पासवर्ड बदलला गेला आहे.","reset_email_subject":"तुमचा RBAgriScan पासवर्ड रीसेट करा","reset_email_body":"आम्हाला तुमचा RBAgriScan पासवर्ड रीसेट करण्याची विनंती मिळाली आहे. नवीन पासवर्ड निवडण्यासाठी खालील लिंकवर क्लिक करा. ही लिंक 1 तासात कालबाह्य होईल. जर तुम्ही ही विनंती केली नसेल, तर हा ईमेल दुर्लक्षित करा.",
"contact_title":"संपर्क करा","contact_text":"काही प्रश्न आहे किंवा मदत हवी आहे? आम्हाला संदेश पाठवा, आमची टीम लवकरच उत्तर देईल.","send_message":"संदेश पाठवा","contact_saved":"धन्यवाद, तुमचा संदेश मिळाला आहे.",
"suggestion_title":"सूचना","suggestion_text":"स्मार्ट क्रॉप AI अधिक चांगले करण्यासाठी तुमच्या कल्पना शेअर करा.","suggestion_placeholder":"तुमची सूचना लिहा...","send_suggestion":"सूचना पाठवा","suggestion_saved":"तुमच्या सूचनेबद्दल धन्यवाद.","recent_suggestions":"अलीकडील सूचना","no_suggestions":"अजून सूचना नाहीत. सर्वप्रथम तुम्ही व्हा!",
"rating":"रेटिंग","your_rating":"तुमची रेटिंग","recent_feedback":"अलीकडील शेतकरी अभिप्राय","avg_rating":"सरासरी रेटिंग","no_feedback":"अजून अभिप्राय नाही."}
}

DISEASE_DATA = {'Apple___Apple_scab': {'en': {'name': 'Apple Scab', 'home': ['Remove and destroy infected leaves.', 'Keep the area around trees clean.', 'Remove fallen infected leaves.'], 'natural': ['Improve air circulation by pruning.', 'Avoid excessive moisture on leaves.', 'Use healthy planting material.'], 'field': ['Prune dense branches.', 'Remove infected plant debris.', 'Maintain proper plant spacing.'], 'chemical': ['Use a suitable fungicide according to local agricultural recommendations.', 'Follow the product label carefully.'], 'prevention': ['Use resistant varieties where available.', 'Maintain orchard sanitation.', 'Avoid prolonged leaf wetness.']}, 'hi': {'name': 'सेब का स्कैब रोग', 'home': ['संक्रमित पत्तियों को हटाकर नष्ट करें।', 'पेड़ के आसपास की जगह साफ रखें।', 'गिरी हुई संक्रमित पत्तियों को हटाएं।'], 'natural': ['छंटाई करके हवा का संचार बढ़ाएं।', 'पत्तियों पर अत्यधिक नमी से बचें।', 'स्वस्थ पौध सामग्री का उपयोग करें।'], 'field': ['घनी शाखाओं की छंटाई करें।', 'संक्रमित पौध अवशेष हटाएं।', 'पौधों के बीच उचित दूरी रखें।'], 'chemical': ['स्थानीय कृषि सलाह के अनुसार उपयुक्त फफूंदनाशक का उपयोग करें।', 'उत्पाद के लेबल पर दिए निर्देशों का पालन करें।'], 'prevention': ['जहाँ उपलब्ध हो रोग प्रतिरोधी किस्मों का उपयोग करें।', 'बगीचे की स्वच्छता बनाए रखें।', 'पत्तियों पर लंबे समय तक नमी से बचें।']}, 'mr': {'name': 'सफरचंद स्कॅब रोग', 'home': ['संक्रमित पाने काढून नष्ट करा.', 'झाडाच्या आजूबाजूचा परिसर स्वच्छ ठेवा.', 'गळलेली संक्रमित पाने काढून टाका.'], 'natural': ['छाटणी करून हवेचे योग्य वहन ठेवा.', 'पानांवर जास्त ओलावा राहू देऊ नका.', 'निरोगी रोपांचा वापर करा.'], 'field': ['दाट फांद्यांची छाटणी करा.', 'संक्रमित वनस्पती अवशेष काढा.', 'झाडांमध्ये योग्य अंतर ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्यानुसार योग्य बुरशीनाशक वापरा.', 'उत्पादनाच्या लेबलवरील सूचना पाळा.'], 'prevention': ['उपलब्ध असल्यास रोगप्रतिकारक वाण वापरा.', 'बागेची स्वच्छता राखा.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.']}}, 'Apple___Black_rot': {'en': {'name': 'Apple Black Rot', 'home': ['Remove infected fruits and leaves.', 'Remove dead branches.', 'Keep fallen plant material away from the orchard.'], 'natural': ['Improve sunlight and air circulation.', 'Avoid unnecessary leaf wetness.', 'Maintain healthy tree growth.'], 'field': ['Prune dead and infected branches.', 'Remove mummified fruits.', 'Maintain orchard sanitation.'], 'chemical': ['Apply an appropriate fungicide according to local recommendations.', 'Follow the product label.'], 'prevention': ['Remove diseased plant material regularly.', 'Avoid tree injuries.', 'Use healthy planting material.']}, 'hi': {'name': 'सेब का ब्लैक रॉट रोग', 'home': ['संक्रमित फल और पत्तियाँ हटा दें।', 'पेड़ की सूखी शाखाएँ काट दें।', 'गिरी हुई संक्रमित सामग्री हटा दें।'], 'natural': ['धूप और हवा का अच्छा संचार रखें।', 'पत्तियों पर अनावश्यक नमी से बचें।', 'पेड़ को स्वस्थ रखें।'], 'field': ['सूखी और संक्रमित शाखाओं की छंटाई करें।', 'सूखे संक्रमित फलों को हटाएं।', 'बगीचे की सफाई बनाए रखें।'], 'chemical': ['स्थानीय कृषि सलाह के अनुसार उचित फफूंदनाशक का प्रयोग करें।', 'लेबल पर दी गई मात्रा से अधिक उपयोग न करें।'], 'prevention': ['रोगग्रस्त सामग्री नियमित रूप से हटाएं।', 'पेड़ को चोट लगने से बचाएं।', 'स्वस्थ पौध सामग्री का उपयोग करें।']}, 'mr': {'name': 'सफरचंद ब्लॅक रॉट रोग', 'home': ['संक्रमित फळे आणि पाने काढा.', 'झाडाच्या वाळलेल्या फांद्या काढा.', 'संक्रमित वनस्पती अवशेष दूर ठेवा.'], 'natural': ['सूर्यप्रकाश आणि हवेचे योग्य वहन ठेवा.', 'पानांवर अनावश्यक ओलावा टाळा.', 'झाडाची वाढ निरोगी ठेवा.'], 'field': ['वाळलेल्या आणि संक्रमित फांद्यांची छाटणी करा.', 'वाळलेली संक्रमित फळे काढा.', 'बागेची स्वच्छता राखा.'], 'chemical': ['स्थानिक कृषी सल्ल्यानुसार योग्य बुरशीनाशक वापरा.', 'लेबलवरील सूचनांचे पालन करा.'], 'prevention': ['रोगग्रस्त अवशेष नियमित काढा.', 'झाडाला इजा होणार नाही याची काळजी घ्या.', 'निरोगी रोपांचा वापर करा.']}}, 'Apple___Cedar_apple_rust': {'en': {'name': 'Apple Cedar Rust', 'home': ['Remove badly infected leaves.', 'Keep the orchard clean.'], 'natural': ['Improve air circulation.', 'Avoid excessive moisture.'], 'field': ['Remove infected plant material.', 'Maintain adequate spacing.'], 'chemical': ['Use a recommended fungicide when necessary.', 'Follow the product label.'], 'prevention': ['Use resistant varieties where available.', 'Maintain orchard sanitation.']}, 'hi': {'name': 'सेब सीडर रस्ट', 'home': ['बहुत अधिक संक्रमित पत्तियों को हटा दें।', 'बगीचे को साफ रखें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'अत्यधिक नमी से बचें।'], 'field': ['संक्रमित पौध सामग्री हटाएं।', 'पौधों के बीच पर्याप्त दूरी रखें।'], 'chemical': ['आवश्यकता होने पर अनुशंसित फफूंदनाशक का उपयोग करें।', 'उत्पाद के लेबल का पालन करें।'], 'prevention': ['जहाँ उपलब्ध हो प्रतिरोधी किस्मों का उपयोग करें।', 'बगीचे की स्वच्छता बनाए रखें।']}, 'mr': {'name': 'सफरचंद सीडर रस्ट', 'home': ['जास्त संक्रमित पाने काढून टाका.', 'बाग स्वच्छ ठेवा.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'जास्त ओलावा टाळा.'], 'field': ['संक्रमित वनस्पती अवशेष काढा.', 'झाडांमध्ये योग्य अंतर ठेवा.'], 'chemical': ['गरजेनुसार शिफारस केलेले बुरशीनाशक वापरा.', 'उत्पादनाच्या लेबलवरील सूचना पाळा.'], 'prevention': ['उपलब्ध असल्यास रोगप्रतिकारक वाण वापरा.', 'बागेची स्वच्छता राखा.']}}, 'Apple___healthy': {'en': {'name': 'Healthy Apple Leaf', 'home': ['No disease treatment is required.', 'Continue regular plant care.'], 'natural': ['Provide adequate sunlight.', 'Maintain balanced watering.'], 'field': ['Monitor leaves regularly.', 'Remove weeds around the plant.'], 'chemical': ['Do not use fungicides unnecessarily.'], 'prevention': ['Maintain good sanitation.', 'Inspect plants regularly.']}, 'hi': {'name': 'स्वस्थ सेब की पत्ती', 'home': ['बीमारी के उपचार की आवश्यकता नहीं है।', 'नियमित पौधों की देखभाल जारी रखें।'], 'natural': ['पर्याप्त धूप दें।', 'संतुलित सिंचाई करें।'], 'field': ['पत्तियों की नियमित जांच करें।', 'पौधे के आसपास की खरपतवार हटाएं।'], 'chemical': ['बिना आवश्यकता फफूंदनाशक का उपयोग न करें।'], 'prevention': ['अच्छी स्वच्छता बनाए रखें।', 'पौधों की नियमित जांच करें।']}, 'mr': {'name': 'निरोगी सफरचंद पान', 'home': ['रोगासाठी उपचाराची आवश्यकता नाही.', 'नियमित वनस्पती काळजी सुरू ठेवा.'], 'natural': ['पुरेसा सूर्यप्रकाश द्या.', 'पाण्याचे संतुलित व्यवस्थापन करा.'], 'field': ['पानांची नियमित तपासणी करा.', 'झाडाभोवतीची तण काढा.'], 'chemical': ['गरज नसताना बुरशीनाशक वापरू नका.'], 'prevention': ['चांगली स्वच्छता राखा.', 'वनस्पतींची नियमित तपासणी करा.']}}, 'Corn_(maize)___Cercospora_leaf_spot Gray_leaf_spot': {'en': {'name': 'Corn Gray Leaf Spot', 'home': ['Remove badly affected leaves and crop debris.', 'Avoid working through wet foliage when possible.', 'Keep the field clean around plants.'], 'natural': ['Improve airflow with proper plant spacing.', 'Use balanced nutrition and avoid excessive nitrogen.', 'Avoid prolonged leaf wetness where possible.'], 'field': ['Rotate corn with non-host crops when practical.', 'Manage crop residue after harvest.', 'Monitor lower leaves regularly for expanding spots.'], 'chemical': ['Use a labeled fungicide only when disease pressure and local recommendations justify it.', 'Follow the product label and local agricultural guidance.'], 'prevention': ['Use tolerant varieties where available.', 'Practice crop rotation and residue management.', 'Scout fields early and regularly.']}, 'hi': {'name': 'Corn Gray Leaf Spot', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Corn Gray Leaf Spot', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Corn_(maize)___Common_rust_': {'en': {'name': 'Corn Common Rust', 'home': ['Remove heavily affected leaves when practical.', 'Keep volunteer plants and weeds under control.', 'Avoid unnecessary overhead watering.'], 'natural': ['Maintain good airflow.', 'Support healthy growth with balanced fertilizer.', 'Avoid prolonged leaf wetness.'], 'field': ['Scout lower and middle leaves for rust pustules.', 'Remove heavily infected debris after harvest.', 'Use crop rotation as part of an integrated plan.'], 'chemical': ['Use a labeled fungicide when justified by local disease pressure.', 'Follow the label and local agricultural advice.'], 'prevention': ['Choose resistant/tolerant hybrids where available.', 'Scout early during favorable weather.', 'Maintain field sanitation.']}, 'hi': {'name': 'Corn Common Rust', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Corn Common Rust', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Corn_(maize)___Northern_Leaf_Blight': {'en': {'name': 'Corn Northern Leaf Blight', 'home': ['Remove severely infected leaves when practical.', 'Keep crop debris managed after harvest.', 'Avoid unnecessary leaf wetness.'], 'natural': ['Maintain balanced nutrition and plant vigor.', 'Improve airflow through appropriate spacing.', 'Avoid repeated overhead irrigation late in the day.'], 'field': ['Rotate crops where practical.', 'Manage infected residue.', 'Scout lower leaves early and often.'], 'chemical': ['Use a labeled fungicide when recommended locally.', 'Follow the product label and pre-harvest requirements.'], 'prevention': ['Plant resistant or tolerant hybrids where available.', 'Use crop rotation and residue management.', 'Monitor during cool, humid periods.']}, 'hi': {'name': 'Corn Northern Leaf Blight', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Corn Northern Leaf Blight', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Corn_(maize)___healthy': {'en': {'name': 'Healthy Corn Leaf', 'home': ['No disease treatment is needed.', 'Remove weeds competing with the crop.', 'Keep the field clean and well drained.'], 'natural': ['Maintain balanced irrigation and nutrition.', 'Provide adequate sunlight and airflow.', 'Monitor plant vigor regularly.'], 'field': ['Scout leaves and stems regularly.', 'Maintain sensible plant spacing.', 'Manage weeds and crop residue.'], 'chemical': ['Avoid routine fungicide use when there is no disease indication.'], 'prevention': ['Use healthy seed and tolerant hybrids where available.', 'Practice crop rotation.', 'Inspect plants early for changes.']}, 'hi': {'name': 'Healthy Corn Leaf', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Healthy Corn Leaf', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Grape___Black_rot': {'en': {'name': 'Grape Black Rot', 'home': ['Remove and destroy mummified berries and infected leaves.', 'Keep fallen infected fruit away from vines.', 'Improve vineyard sanitation.'], 'natural': ['Prune for sunlight and airflow.', 'Avoid prolonged leaf wetness.', 'Maintain balanced vine nutrition.'], 'field': ['Remove infected clusters and plant debris.', 'Manage weeds to improve airflow.', 'Scout leaves and fruit regularly.'], 'chemical': ['Use a labeled fungicide according to local recommendations and label directions.', 'Observe harvest and re-entry instructions on the label.'], 'prevention': ['Remove mummified fruit promptly.', 'Use clean planting material.', 'Maintain an open canopy.']}, 'hi': {'name': 'Grape Black Rot', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Grape Black Rot', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Grape___Esca_(Black_Measles)': {'en': {'name': 'Grape Esca / Black Measles', 'home': ['Remove severely affected plant parts where appropriate.', 'Disinfect pruning tools between vines.', 'Do not leave infected pruning waste in the vineyard.'], 'natural': ['Maintain balanced vine vigor and avoid unnecessary stress.', 'Improve canopy airflow.', 'Avoid injuries to trunks and pruning wounds.'], 'field': ['Mark symptomatic vines for monitoring.', 'Remove severely diseased wood according to local extension advice.', 'Keep vineyard sanitation high.'], 'chemical': ['There is no simple curative chemical treatment; use only locally recommended products/practices.', 'Follow agricultural extension guidance for trunk-disease management.'], 'prevention': ['Use healthy planting material.', 'Make clean, careful pruning cuts.', 'Avoid unnecessary trunk injuries.']}, 'hi': {'name': 'Grape Esca / Black Measles', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Grape Esca / Black Measles', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Grape___Leaf_blight_(Isariopsis_Leaf_Spot)': {'en': {'name': 'Grape Leaf Blight', 'home': ['Remove heavily infected leaves and debris.', 'Keep fallen leaves away from vine rows.', 'Avoid unnecessary leaf wetness.'], 'natural': ['Open the canopy for better airflow and sunlight.', 'Use balanced irrigation.', 'Avoid dense, humid canopy conditions.'], 'field': ['Prune crowded growth.', 'Scout leaves regularly.', 'Remove infected debris after pruning.'], 'chemical': ['Use an appropriate labeled fungicide only when locally recommended.', 'Follow the product label.'], 'prevention': ['Maintain vineyard sanitation.', 'Use healthy planting material.', 'Manage canopy humidity.']}, 'hi': {'name': 'Grape Leaf Blight', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Grape Leaf Blight', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Grape___healthy': {'en': {'name': 'Healthy Grape Leaf', 'home': ['No disease treatment is needed.', 'Remove weeds and damaged plant material.', 'Keep vines clean and well supported.'], 'natural': ['Maintain good canopy airflow.', 'Water consistently without prolonged leaf wetness.', 'Provide balanced nutrition.'], 'field': ['Prune and train vines properly.', 'Scout leaves and fruit regularly.', 'Keep vineyard floor managed.'], 'chemical': ['Avoid unnecessary pesticide applications.'], 'prevention': ['Use healthy planting material.', 'Maintain sanitation and canopy management.', 'Monitor regularly.']}, 'hi': {'name': 'Healthy Grape Leaf', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Healthy Grape Leaf', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Peach___Bacterial_spot': {'en': {'name': 'Peach Bacterial Spot', 'home': ['Remove severely affected leaves and fruit where practical.', 'Avoid moving through wet foliage.', 'Remove fallen infected debris.'], 'natural': ['Improve airflow through pruning.', 'Avoid overhead irrigation when possible.', 'Maintain balanced tree nutrition.'], 'field': ['Prune crowded branches.', 'Maintain orchard sanitation.', 'Monitor young leaves and fruit after wet weather.'], 'chemical': ['Use only a locally recommended bactericide and follow its label.', 'Do not exceed labeled rates or spray frequency.'], 'prevention': ['Choose tolerant varieties where available.', 'Use clean planting material.', 'Avoid unnecessary tree wounds.']}, 'hi': {'name': 'Peach Bacterial Spot', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Peach Bacterial Spot', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Peach___healthy': {'en': {'name': 'Healthy Peach Leaf', 'home': ['No disease treatment is needed.', 'Remove weeds and dead plant material.', 'Keep the orchard clean.'], 'natural': ['Provide adequate sunlight and airflow.', 'Maintain balanced watering.', 'Support healthy tree growth with balanced nutrition.'], 'field': ['Prune appropriately and monitor leaves and fruit.', 'Keep orchard floor clean.', 'Scout after wet weather.'], 'chemical': ['Avoid unnecessary pesticide applications.'], 'prevention': ['Use healthy planting material.', 'Maintain orchard sanitation.', 'Inspect trees regularly.']}, 'hi': {'name': 'Healthy Peach Leaf', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Healthy Peach Leaf', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Pepper,_bell___Bacterial_spot': {'en': {'name': 'Bell Pepper Bacterial Spot', 'home': ['Remove badly infected leaves and fruit.', 'Avoid handling plants when foliage is wet.', 'Remove infected plant debris.'], 'natural': ['Use drip irrigation where possible.', 'Improve airflow and avoid overcrowding.', 'Keep leaves dry as practical.'], 'field': ['Rotate away from susceptible crops.', 'Remove volunteer plants and weeds.', 'Sanitize tools and hands after handling infected plants.'], 'chemical': ['Use only locally recommended bactericides and follow the label.', 'Do not mix or exceed products unless the label permits it.'], 'prevention': ['Use certified clean seed/transplants.', 'Choose tolerant varieties where available.', 'Practice crop rotation and sanitation.']}, 'hi': {'name': 'Bell Pepper Bacterial Spot', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Bell Pepper Bacterial Spot', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Pepper,_bell___healthy': {'en': {'name': 'Healthy Bell Pepper Leaf', 'home': ['No disease treatment is needed.', 'Remove weeds and damaged leaves.', 'Keep the growing area clean.'], 'natural': ['Use balanced irrigation.', 'Provide good airflow and sunlight.', 'Avoid prolonged leaf wetness.'], 'field': ['Scout leaves and fruit regularly.', 'Maintain sensible plant spacing.', 'Manage weeds around plants.'], 'chemical': ['Avoid unnecessary pesticides.'], 'prevention': ['Use healthy transplants.', 'Maintain field sanitation.', 'Rotate crops where practical.']}, 'hi': {'name': 'Healthy Bell Pepper Leaf', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Healthy Bell Pepper Leaf', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Potato___Early_blight': {'en': {'name': 'Potato Early Blight', 'home': ['Remove badly affected lower leaves when practical.', 'Remove infected crop debris.', 'Avoid unnecessary leaf wetness.'], 'natural': ['Maintain balanced fertilizer, especially adequate potassium.', 'Use mulch or practices that reduce soil splash where appropriate.', 'Avoid plant stress from irregular watering.'], 'field': ['Rotate crops.', 'Remove volunteer potatoes.', 'Scout older leaves first.'], 'chemical': ['Use a labeled fungicide only when locally recommended.', 'Rotate fungicide modes of action according to the label and local advice.'], 'prevention': ['Use healthy seed tubers.', 'Practice crop rotation.', 'Maintain good field sanitation.']}, 'hi': {'name': 'Potato Early Blight', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Potato Early Blight', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Potato___Late_blight': {'en': {'name': 'Potato Late Blight', 'home': ['Remove and destroy severely infected foliage when advised.', 'Do not leave infected tubers or debris in the field.', 'Avoid handling plants when wet.'], 'natural': ['Improve airflow and avoid prolonged leaf wetness.', 'Use well-managed irrigation.', 'Monitor closely during cool, humid weather.'], 'field': ['Remove cull piles and volunteer potatoes.', 'Scout frequently after favorable weather.', 'Harvest carefully to reduce tuber injury.'], 'chemical': ['Use locally recommended late-blight fungicides when disease risk is high.', 'Follow the label and resistance-management guidance.'], 'prevention': ['Plant certified disease-free seed.', 'Destroy cull piles.', 'Use resistant/tolerant varieties where available.']}, 'hi': {'name': 'Potato Late Blight', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Potato Late Blight', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Potato___healthy': {'en': {'name': 'Healthy Potato Leaf', 'home': ['No disease treatment is needed.', 'Keep weeds controlled.', 'Remove damaged debris from the field.'], 'natural': ['Maintain balanced irrigation and nutrition.', 'Avoid prolonged leaf wetness.', 'Support good airflow between plants.'], 'field': ['Scout leaves and stems regularly.', 'Rotate crops.', 'Use proper hilling and field sanitation.'], 'chemical': ['Avoid routine fungicide use without disease evidence.'], 'prevention': ['Use certified seed tubers.', 'Rotate crops.', 'Monitor regularly.']}, 'hi': {'name': 'Healthy Potato Leaf', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Healthy Potato Leaf', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Strawberry___Leaf_scorch': {'en': {'name': 'Strawberry Leaf Scorch', 'home': ['Remove severely affected leaves.', 'Remove dead plant debris from beds.', 'Avoid prolonged leaf wetness.'], 'natural': ['Improve airflow by avoiding overcrowding.', 'Water at the soil level when possible.', 'Maintain balanced nutrition.'], 'field': ['Remove old infected leaves after harvest as appropriate.', 'Keep beds weed-free.', 'Monitor new growth regularly.'], 'chemical': ['Use a labeled fungicide only if the disease diagnosis and local recommendation support it.', 'Follow the product label.'], 'prevention': ['Use healthy planting material.', 'Avoid overcrowded beds.', 'Maintain sanitation and irrigation management.']}, 'hi': {'name': 'Strawberry Leaf Scorch', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Strawberry Leaf Scorch', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Strawberry___healthy': {'en': {'name': 'Healthy Strawberry Leaf', 'home': ['No disease treatment is needed.', 'Remove damaged leaves and weeds.', 'Keep beds clean.'], 'natural': ['Water at the root zone.', 'Provide sunlight and airflow.', 'Maintain balanced nutrition.'], 'field': ['Scout leaves and fruit regularly.', 'Remove old debris after harvest.', 'Maintain appropriate plant spacing.'], 'chemical': ['Avoid unnecessary pesticides.'], 'prevention': ['Use healthy runners/transplants.', 'Maintain bed sanitation.', 'Monitor regularly.']}, 'hi': {'name': 'Healthy Strawberry Leaf', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Healthy Strawberry Leaf', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Tomato___Bacterial_spot': {'en': {'name': 'Tomato Bacterial Spot', 'home': ['Remove severely affected leaves and fruit.', 'Avoid working with wet plants.', 'Remove infected debris from the growing area.'], 'natural': ['Use drip irrigation where possible.', 'Improve airflow and avoid overcrowding.', 'Avoid leaf wetness lasting overnight.'], 'field': ['Rotate away from susceptible crops.', 'Sanitize tools.', 'Scout new growth and fruit regularly.'], 'chemical': ['Use only locally recommended bactericides and follow the label.', 'Do not exceed label rates.'], 'prevention': ['Use clean seed/transplants.', 'Choose tolerant varieties where available.', 'Practice crop rotation and sanitation.']}, 'hi': {'name': 'Tomato Bacterial Spot', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Tomato Bacterial Spot', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Tomato___Early_blight': {'en': {'name': 'Tomato Early Blight', 'home': ['Remove lower infected leaves.', 'Remove fallen infected debris.', 'Avoid soil splash onto leaves where practical.'], 'natural': ['Mulch to reduce splash.', 'Maintain balanced irrigation and nutrition.', 'Improve airflow around plants.'], 'field': ['Rotate crops where practical.', 'Stake or trellis plants to improve airflow.', 'Scout lower leaves regularly.'], 'chemical': ['Use a labeled fungicide when locally recommended.', 'Follow label directions and resistance-management advice.'], 'prevention': ['Use healthy transplants.', 'Rotate crops.', 'Maintain sanitation and mulch/soil-splash control.']}, 'hi': {'name': 'Tomato Early Blight', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Tomato Early Blight', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Tomato___Late_blight': {'en': {'name': 'Tomato Late Blight', 'home': ['Remove and destroy severely infected plant material.', 'Do not compost heavily infected debris.', 'Avoid handling plants while wet.'], 'natural': ['Improve airflow.', 'Use irrigation that minimizes leaf wetness.', 'Monitor closely during cool, humid weather.'], 'field': ['Scout frequently.', 'Remove volunteer tomatoes and potatoes.', 'Keep infected debris out of the field.'], 'chemical': ['Use locally recommended late-blight products when risk is high.', 'Follow the label and resistance-management guidance.'], 'prevention': ['Use healthy transplants.', 'Remove volunteer hosts.', 'Monitor weather and plants early.']}, 'hi': {'name': 'Tomato Late Blight', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Tomato Late Blight', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}, 'Tomato___healthy': {'en': {'name': 'Healthy Tomato Leaf', 'home': ['No disease treatment is needed.', 'Remove weeds and damaged leaves.', 'Keep the growing area clean.'], 'natural': ['Water at the root zone.', 'Provide good airflow and sunlight.', 'Maintain balanced nutrition.'], 'field': ['Stake or trellis plants.', 'Scout leaves and fruit regularly.', 'Rotate crops where practical.'], 'chemical': ['Avoid unnecessary pesticide applications.'], 'prevention': ['Use healthy transplants.', 'Maintain sanitation.', 'Monitor plants regularly.']}, 'hi': {'name': 'Healthy Tomato Leaf', 'home': ['इस रोग से प्रभावित भागों को हटाएं और खेत/पौधे का क्षेत्र साफ रखें।', 'संक्रमित अवशेषों को पौधे के पास न छोड़ें।', 'पत्तियों पर लंबे समय तक नमी रहने से बचें।'], 'natural': ['हवा का अच्छा संचार रखें।', 'संतुलित सिंचाई और पोषण दें।', 'पौधों को अनावश्यक तनाव से बचाएं।'], 'field': ['संक्रमित अवशेष हटाएं।', 'पौधों की नियमित निगरानी करें।', 'जहाँ संभव हो उचित दूरी और फसल चक्र अपनाएं।'], 'chemical': ['केवल स्थानीय कृषि विशेषज्ञ की सलाह और उत्पाद के लेबल के अनुसार दवा का उपयोग करें।', 'लेबल में दी गई मात्रा और सुरक्षा निर्देशों का पालन करें।'], 'prevention': ['स्वस्थ बीज/रोपण सामग्री का उपयोग करें।', 'खेत और उपकरणों की स्वच्छता रखें।', 'रोग के शुरुआती लक्षणों की नियमित जांच करें।']}, 'mr': {'name': 'Healthy Tomato Leaf', 'home': ['रोगग्रस्त भाग काढून टाका आणि परिसर स्वच्छ ठेवा.', 'संक्रमित अवशेष झाडाजवळ ठेवू नका.', 'पानांवर जास्त वेळ ओलावा राहू देऊ नका.'], 'natural': ['हवेचे योग्य वहन ठेवा.', 'संतुलित पाणी व पोषण द्या.', 'झाडांवर अनावश्यक ताण येऊ देऊ नका.'], 'field': ['संक्रमित अवशेष काढून टाका.', 'पिकाची नियमित तपासणी करा.', 'शक्य असल्यास योग्य अंतर आणि पीक फेरपालट ठेवा.'], 'chemical': ['स्थानिक कृषी तज्ज्ञांच्या सल्ल्याने आणि उत्पादनाच्या लेबलनुसारच औषध वापरा.', 'लेबलवरील मात्रा व सुरक्षा सूचना पाळा.'], 'prevention': ['निरोगी बियाणे/रोपांचा वापर करा.', 'शेत व साधने स्वच्छ ठेवा.', 'रोगाची सुरुवातीची लक्षणे नियमित तपासा.']}}}


# ---------- DATABASE LAYER ----------
# Hinglish: MySQL connection/query helper. Login, crops, scans, IoT aur tickets ka data yahan se store/read hota hai.
class DBConn:
    """Thin wrapper so the rest of app.py can keep using the same
    sqlite-style calls (conn.execute("...WHERE email=?", (x,)), row["col"],
    conn.commit(), conn.close()) while actually talking to MySQL via PyMySQL.
    """
    def __init__(self):
        self._conn = pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            charset="utf8mb4"
        )
        self._cursor = self._conn.cursor()

    def execute(self, sql, params=()):
        # sqlite uses "?" placeholders; PyMySQL/MySQL uses "%s".
        self._cursor.execute(sql.replace("?", "%s"), params)
        return self._cursor

    def executescript(self, script):
        for stmt in [s.strip() for s in script.split(";") if s.strip()]:
            self._cursor.execute(stmt)
        self._conn.commit()

    def commit(self):
        self._conn.commit()

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    def close(self):
        try:
            self._cursor.close()
        finally:
            self._conn.close()

def db():
    return DBConn()

def init_db():
    conn=db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INT AUTO_INCREMENT PRIMARY KEY,email VARCHAR(255) UNIQUE NOT NULL,password VARCHAR(255) NOT NULL,name VARCHAR(255) NOT NULL,is_admin INT DEFAULT 0,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS scans(id INT AUTO_INCREMENT PRIMARY KEY,user_id INT,image VARCHAR(255),plant VARCHAR(255),disease VARCHAR(255),confidence FLOAT,status VARCHAR(255),created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS feedback(id INT AUTO_INCREMENT PRIMARY KEY,user_id INT,name VARCHAR(255),message TEXT,rating INT DEFAULT 5,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS suggestions(id INT AUTO_INCREMENT PRIMARY KEY,user_id INT,name VARCHAR(255),message TEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS contact_messages(id INT AUTO_INCREMENT PRIMARY KEY,name VARCHAR(255),email VARCHAR(255),message TEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

    -- Crop Timeline & Stage Tracker
    CREATE TABLE IF NOT EXISTS crop_logs(id INT AUTO_INCREMENT PRIMARY KEY,user_id INT NOT NULL,crop_key VARCHAR(100) NOT NULL,nickname VARCHAR(255),planted_on DATE NOT NULL,city VARCHAR(255),current_stage_order INT DEFAULT 1,current_stage_started_on DATE,status VARCHAR(20) DEFAULT 'active',created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS crop_stage_events(id INT AUTO_INCREMENT PRIMARY KEY,crop_log_id INT NOT NULL,stage_order INT NOT NULL,stage_name VARCHAR(50) NOT NULL,note TEXT,logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS care_reminders(id INT AUTO_INCREMENT PRIMARY KEY,crop_log_id INT NOT NULL,kind VARCHAR(30) NOT NULL,due_date DATE NOT NULL,done INT DEFAULT 0,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

    -- IoT Sensor & Smart Irrigation
    CREATE TABLE IF NOT EXISTS sensor_devices(id INT AUTO_INCREMENT PRIMARY KEY,user_id INT NOT NULL,device_key VARCHAR(64) UNIQUE NOT NULL,name VARCHAR(255) NOT NULL,crop_log_id INT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS sensor_readings(id BIGINT AUTO_INCREMENT PRIMARY KEY,device_id INT NOT NULL,soil_moisture FLOAT,temperature FLOAT,humidity FLOAT,light_lux FLOAT,n_ppm FLOAT,p_ppm FLOAT,k_ppm FLOAT,recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,INDEX idx_device_time (device_id, recorded_at));
    CREATE TABLE IF NOT EXISTS sensor_alerts(id INT AUTO_INCREMENT PRIMARY KEY,device_id INT NOT NULL,kind VARCHAR(30),message TEXT,acknowledged INT DEFAULT 0,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

    -- Plant Hospital & Expert Marketplace
    CREATE TABLE IF NOT EXISTS expert_profiles(user_id INT PRIMARY KEY,bio TEXT,specialties VARCHAR(255),region VARCHAR(255),verified INT DEFAULT 0);
    CREATE TABLE IF NOT EXISTS diagnostic_tickets(id INT AUTO_INCREMENT PRIMARY KEY,user_id INT NOT NULL,scan_id INT,image VARCHAR(255),model_prediction VARCHAR(255),confidence FLOAT,lat FLOAT,lon FLOAT,city VARCHAR(255),notes TEXT,status VARCHAR(20) DEFAULT 'open',assigned_expert_id INT,resolution TEXT,resolved_at DATETIME,client_ref VARCHAR(64) UNIQUE,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS ticket_messages(id INT AUTO_INCREMENT PRIMARY KEY,ticket_id INT NOT NULL,sender_id INT NOT NULL,message TEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    """)
    # Safe migrations for older databases.
    try:
        conn.execute("ALTER TABLE feedback ADD COLUMN rating INT DEFAULT 5")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INT DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'farmer'")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN reset_token VARCHAR(255)")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN reset_token_expiry DATETIME")
        conn.commit()
    except Exception:
        pass
    # Phase 1: scan history / crop health. Existing rows keep NULL ("Not enough data yet"); nothing is deleted.
    for ddl in ("ALTER TABLE scans ADD COLUMN health_score INT NULL",
                "ALTER TABLE scans ADD COLUMN severity VARCHAR(16) NULL",
                "ALTER TABLE scans ADD COLUMN result_json MEDIUMTEXT NULL",
                "CREATE INDEX idx_scans_user_id ON scans(user_id, id)",
                # Phase 2: farm / field management
                "CREATE TABLE IF NOT EXISTS fields(id INT AUTO_INCREMENT PRIMARY KEY,user_id INT NOT NULL,farm_name VARCHAR(120) NOT NULL DEFAULT '',"
                "name VARCHAR(120) NOT NULL,crop VARCHAR(120) NOT NULL,area_acres DECIMAL(10,2) NULL,notes VARCHAR(500) NULL,"
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,INDEX idx_fields_user(user_id))",
                "ALTER TABLE scans ADD COLUMN field_id INT NULL",
                "CREATE INDEX idx_scans_field ON scans(field_id)",
                "ALTER TABLE sensor_devices ADD COLUMN field_id INT NULL",
                # Phase 3: notifications (kind + params are stored, text is rendered later in the user's language)
                "CREATE TABLE IF NOT EXISTS notifications(id INT AUTO_INCREMENT PRIMARY KEY,user_id INT NOT NULL,kind VARCHAR(30) NOT NULL,"
                "params_json VARCHAR(1000) NULL,link VARCHAR(255) NULL,dedupe_key VARCHAR(100) NULL,is_read TINYINT NOT NULL DEFAULT 0,"
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,INDEX idx_notif_user(user_id,is_read,id),UNIQUE KEY uq_notif_dedupe(user_id,dedupe_key))",
                "CREATE TABLE IF NOT EXISTS notification_prefs(user_id INT PRIMARY KEY,scan_reminders TINYINT NOT NULL DEFAULT 1,"
                "soil_moisture TINYINT NOT NULL DEFAULT 1,expert_reply TINYINT NOT NULL DEFAULT 1,health_change TINYINT NOT NULL DEFAULT 1,"
                "reminder_days INT NOT NULL DEFAULT 7)",
                # Phase 4: farmer community
                "CREATE TABLE IF NOT EXISTS community_posts(id INT AUTO_INCREMENT PRIMARY KEY,user_id INT NOT NULL,kind VARCHAR(10) NOT NULL DEFAULT 'post',"
                "title VARCHAR(150) NOT NULL,body TEXT NOT NULL,plant VARCHAR(100) NULL,image VARCHAR(255) NULL,status VARCHAR(10) NOT NULL DEFAULT 'visible',"
                "report_count INT NOT NULL DEFAULT 0,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,INDEX idx_cp_status(status,id),INDEX idx_cp_user(user_id))",
                "CREATE TABLE IF NOT EXISTS community_comments(id INT AUTO_INCREMENT PRIMARY KEY,post_id INT NOT NULL,user_id INT NOT NULL,parent_id INT NULL,"
                "body VARCHAR(2000) NOT NULL,status VARCHAR(10) NOT NULL DEFAULT 'visible',report_count INT NOT NULL DEFAULT 0,"
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,INDEX idx_cc_post(post_id,id))",
                "CREATE TABLE IF NOT EXISTS community_likes(post_id INT NOT NULL,user_id INT NOT NULL,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(post_id,user_id))",
                "CREATE TABLE IF NOT EXISTS community_reports(id INT AUTO_INCREMENT PRIMARY KEY,user_id INT NOT NULL,target_type VARCHAR(10) NOT NULL,target_id INT NOT NULL,"
                "reason VARCHAR(200) NULL,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,UNIQUE KEY uq_report(user_id,target_type,target_id))",
                # Phase 5: weather risk, coarse opt-in location, scan weather snapshot, explanation image
                "ALTER TABLE notification_prefs ADD COLUMN disease_risk TINYINT NOT NULL DEFAULT 1",
                "ALTER TABLE notification_prefs ADD COLUMN weather_advisory TINYINT NOT NULL DEFAULT 1",
                "ALTER TABLE notification_prefs ADD COLUMN loc_lat DECIMAL(5,1) NULL",
                "ALTER TABLE notification_prefs ADD COLUMN loc_lon DECIMAL(5,1) NULL",
                "ALTER TABLE scans ADD COLUMN weather_json VARCHAR(400) NULL",
                "ALTER TABLE scans ADD COLUMN explain_image VARCHAR(255) NULL",
                "ALTER TABLE scans ADD CONSTRAINT fk_scans_field FOREIGN KEY (field_id) REFERENCES fields(id) ON DELETE SET NULL",
                "ALTER TABLE sensor_devices ADD CONSTRAINT fk_sensor_field FOREIGN KEY (field_id) REFERENCES fields(id) ON DELETE SET NULL"):
        try:
            conn.execute(ddl)
            conn.commit()
        except Exception:
            pass  # column/index already exists
    # Plant Hospital migrations for databases created by an older build.
    for ddl in (
        "ALTER TABLE diagnostic_tickets ADD COLUMN resolution TEXT",
        "ALTER TABLE diagnostic_tickets ADD COLUMN resolved_at DATETIME",
        "ALTER TABLE diagnostic_tickets ADD COLUMN client_ref VARCHAR(64) UNIQUE",
    ):
        try:
            conn.execute(ddl)
            conn.commit()
        except Exception:
            pass

    # Optional admin bootstrap. Never create an account from hard-coded credentials.
    admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if admin_email and admin_password:
        admin = conn.execute("SELECT id FROM users WHERE email=?", (admin_email,)).fetchone()
        if admin:
            conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
        else:
            conn.execute(
                "INSERT INTO users(name,email,password,is_admin) VALUES(?,?,?,1)",
                ("Administrator", admin_email, generate_password_hash(admin_password))
            )
    conn.commit(); conn.close()

# CRITICAL FIX: this must run when the MODULE is imported, not only inside
# `if __name__=="__main__"` further below. Gunicorn (used by Render, and
# most production hosts) starts the app with `gunicorn app:app` -- it
# IMPORTS app.py as a module and never runs that block, so the database
# tables (and the admin account) were never created on the live server.
init_db()

# --- Password reset email -------------------------------------------------
# Optional: set SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/SMTP_FROM in the
# environment to actually deliver the reset link by email. If SMTP isn't
# configured, the reset link is shown directly on the confirmation page
# instead, so "forgot password" still works out of the box without extra setup.
# ---------- FORGOT PASSWORD EMAIL / SMTP ----------
# Hinglish: Gmail SMTP configured ho to reset link email se send hota hai; local mode me link confirmation page par bhi dikhaya ja sakta hai.
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER).strip()
SMTP_ENABLED = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)

def send_reset_email(to_email, reset_url, lang="en"):
    subject = tx("reset_email_subject", lang)
    body = tx("reset_email_body", lang) + "\n\n" + reset_url
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        return True
    except Exception:
        return False

def current_lang():
    # URL -> session -> language cookie -> English. This makes the selector
    # survive normal navigation and PWA/session restoration reliably.
    lang = request.args.get("lang") or session.get("lang") or request.cookies.get("rbagriscan_lang") or "en"
    if lang not in LANG_MAP:
        lang = "en"
    session["lang"] = lang
    return lang

def tx(key, lang=None):
    lang=lang or session.get("lang","en")
    return TEXTS.get(lang,TEXTS["en"]).get(key,TEXTS["en"].get(key,key))

def login_required(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        return fn(*a,**kw)
    return wrapper

def _csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token

def _same_origin_csrf_ok():
    token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
    return bool(token and secrets.compare_digest(str(token), str(session.get("csrf_token", ""))))

def _rate_hit(key, limit, window=60):
    """Return True when `key` has exceeded `limit` hits inside `window` seconds."""
    now = time.time()
    with _RATE_LOCK:
        recent = [t for t in _RATE_BUCKETS.get(key, []) if now - t < window]
        if len(recent) >= limit:
            _RATE_BUCKETS[key] = recent
            return True
        recent.append(now); _RATE_BUCKETS[key] = recent
        if len(_RATE_BUCKETS) > 5000:
            _RATE_BUCKETS.clear()
    return False

_AUTH_POST_PATHS = {"/login", "/register", "/forgot-password"}

def _wants_json():
    return request.path.startswith("/api/") or request.path == "/detect-camera" or request.is_json

def _error_response(code, message):
    """One place that renders every error page: translated, no stack traces."""
    if _wants_json():
        return jsonify({"ok": False, "error": message}), code
    try:
        lang = session.get("lang", "en")
        return render_template("error.html", code=code, message=translate_phrase(message, lang) if lang != "en" else message), code
    except Exception:
        return ("<!doctype html><meta charset=utf-8><title>Error %s</title><h1>Error %s</h1><p>%s</p>" % (code, code, message)), code

@app.before_request
def block_direct_upload_urls():
    # Uploaded photos are private: they are only served through /media/<name>
    # after an ownership check (see media()). Direct /static/uploads/* is closed.
    if request.path.startswith("/static/uploads/"):
        abort(404)

@app.before_request
def protect_requests():
    if request.method == "POST" and request.path in _AUTH_POST_PATHS:
        if _rate_hit((request.remote_addr or "unknown", "auth:" + request.path), 10, 60):
            return _error_response(429, "Too many attempts. Please wait a minute and try again.")
    # Device-to-server IoT ingestion uses a per-device secret instead of browser CSRF.
    if request.method == "POST" and request.endpoint and request.path != "/api/iot/ingest":
        if not _same_origin_csrf_ok():
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Security token expired. Refresh the page and try again."}), 403
            return _error_response(403, "Security token expired. Refresh the page and try again.")
    # Small in-process abuse guard for expensive AI endpoints. Production can add a
    # reverse-proxy/WAF limit as a second layer.
    if request.path in {"/detect-camera", "/api/chat", "/api/soil-analysis"} or (request.path == "/" and request.method == "POST"):
        now = time.time(); ip = request.remote_addr or "unknown"  # ProxyFix already resolves the real client IP; never trust raw X-Forwarded-For
        key = (ip, request.path)
        with _RATE_LOCK:
            recent = [t for t in _RATE_BUCKETS.get(key, []) if now - t < 60]
            if len(recent) >= _RATE_LIMIT:
                return jsonify({"ok": False, "error": "Too many requests. Please wait a minute and try again."}), 429
            recent.append(now); _RATE_BUCKETS[key] = recent
            if len(_RATE_BUCKETS) > 5000:
                _RATE_BUCKETS.clear()
    return None

@app.context_processor
def context():
    lang=session.get("lang","en")
    base_code=lang.split("-")[0].lower()
    texts = {k: (v.replace("Smart Crop AI", "RBAgriScan") if isinstance(v, str) else v) for k, v in translate_texts(lang, network=False).items()}
    return {"language":lang,"texts":texts,"languages":LANGUAGES,
            "csrf_token":_csrf_token(),
            "text_dir":"rtl" if base_code in RTL_LANGS else "ltr",
            "user":session.get("user_name"),"is_admin":bool(session.get("is_admin")),
            "gemini_enabled":GEMINI_ENABLED,"plantnet_enabled":PLANTNET_ENABLED,
            "ui_json":json.dumps(texts, ensure_ascii=False),"tr":tr_fast}

_INDEXABLE_PATHS = {"/", "/about", "/supported-plants", "/contact", "/login", "/register"}

@app.after_request
def add_security_headers(resp):
    # HTML pages contain server-rendered translations. Do not let a browser,
    # proxy, or PWA cache serve the previous language after a switch.
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Vary"] = "Cookie"
    # Only the public marketing pages may be indexed. Everything else (accounts, scans, API, media) is noindex.
    if request.path not in _INDEXABLE_PATHS or "user_id" in session:
        resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(self), geolocation=(self), microphone=(self)")
    return resp

@app.errorhandler(400)
def bad_request(_e):
    return _error_response(400, "The request could not be understood.")

@app.errorhandler(401)
def unauthorized(_e):
    return _error_response(401, "Please log in to continue.")

@app.errorhandler(403)
def forbidden(_e):
    return _error_response(403, "You do not have permission to open this page.")

@app.errorhandler(404)
def not_found(_e):
    return _error_response(404, "Page not found.")

@app.errorhandler(405)
def method_not_allowed(_e):
    return _error_response(405, "This action is not allowed here.")

@app.errorhandler(413)
def request_too_large(_e):
    return _error_response(413, "Image/file is too large. Maximum upload size is %d MB." % (app.config["MAX_CONTENT_LENGTH"] // (1024*1024)))

@app.errorhandler(429)
def too_many(_e):
    return _error_response(429, "Too many requests. Please try again later.")

@app.errorhandler(500)
def server_error(_e):
    app.logger.exception("Unhandled server error")
    return _error_response(500, "Something went wrong on the server. Please try again.")

@app.route("/robots.txt")
def robots_txt():
    return "User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /media/\nDisallow: /history\nDisallow: /fields\nDisallow: /notifications\nDisallow: /community\nDisallow: /admin\nDisallow: /experts\nDisallow: /iot\nDisallow: /crops\nDisallow: /dashboard\nSitemap: " + url_for("sitemap_xml", _external=True) + "\n", 200, {"Content-Type":"text/plain; charset=utf-8"}

@app.route("/sitemap.xml")
def sitemap_xml():
    urls = [url_for(x, _external=True) for x in ("home","about","supported_plants","contact")]
    body = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>" + "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">" + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>"
    return body, 200, {"Content-Type":"application/xml; charset=utf-8"}

@app.route("/sw.js")
def service_worker():
    # Served from the root (not /static/sw.js) so its scope covers the whole
    # site - a service worker can only control paths at or below its own URL.
    resp = send_from_directory(os.path.join(BASE_DIR, "static"), "sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp

@app.route("/offline")
def offline_page():
    current_lang()
    return render_template("offline.html")

# ---------- LOCAL TFLITE DISEASE MODEL ----------
# Hinglish: labels.txt load + image preprocessing + TensorFlow Lite prediction yahan hota hai.
def load_labels():
    if not os.path.exists(LABELS_PATH): return []
    return [x.strip() for x in open(LABELS_PATH,encoding="utf-8") if x.strip()]
labels=load_labels()

interpreter=None; input_details=None; output_details=None
if os.path.exists(MODEL_PATH):
    try:
        interpreter=tf.lite.Interpreter(model_path=MODEL_PATH)
        interpreter.allocate_tensors()
        input_details=interpreter.get_input_details(); output_details=interpreter.get_output_details()
        print("TFLite:", input_details[0]["shape"], input_details[0]["dtype"], "labels:",len(labels))
    except Exception as e: print("Model load error:",e)

_INFER_LOCK = threading.Lock()   # the TFLite interpreter is NOT thread-safe: one inference at a time

def preprocess(img):
    return _prepare_pil(Image.open(img).convert("RGB"))

def _prepare_pil(im):
    shape=input_details[0]["shape"]; h,w=int(shape[1]),int(shape[2])
    im=im.convert("RGB").resize((w,h))
    arr=np.asarray(im,dtype=np.float32)/255.0
    arr=np.expand_dims(arr,0)
    if input_details[0]["dtype"]==np.float32: return arr
    scale,zp=input_details[0]["quantization"]
    return np.round(arr/scale+zp).astype(input_details[0]["dtype"]) if scale else arr.astype(input_details[0]["dtype"])

def raw_predict(arr):
    with _INFER_LOCK:
        interpreter.set_tensor(input_details[0]["index"],arr)
        interpreter.invoke()
        out=interpreter.get_tensor(output_details[0]["index"])[0].astype(np.float32)
    # If model returns logits, softmax them. If already probabilities, normalize safely.
    if np.any(out<0) or out.max()>1.001 or abs(out.sum()-1)>0.05:
        e=np.exp(out-out.max()); out=e/e.sum()
    else:
        out=out/(out.sum() or 1)
    return out


def image_quality(path):
    """Fast, conservative quality gate. Returns (ok, message, metrics)."""
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            w,h=im.size
            if w < 256 or h < 256:
                return False, "Please upload a clearer image (at least 256×256 pixels).", {"width":w,"height":h}
            arr=np.asarray(im.resize((256,256)),dtype=np.float32)
            brightness=float(arr.mean())
            contrast=float(arr.std())
            # Laplacian-like sharpness without OpenCV: variance of adjacent differences.
            gray=arr.mean(axis=2)
            sharp=float(np.var(np.diff(gray,axis=0))+np.var(np.diff(gray,axis=1)))
            if brightness < 22:
                return False, "The image is too dark. Please take the photo in better lighting.", {"width":w,"height":h,"brightness":brightness,"sharpness":sharp}
            if brightness > 245:
                return False, "The image is overexposed. Please retake the photo without direct glare.", {"width":w,"height":h,"brightness":brightness,"sharpness":sharp}
            if sharp < 2.0:
                return False, "The image looks blurry. Keep the leaf/plant steady and retake the photo.", {"width":w,"height":h,"brightness":brightness,"sharpness":sharp}
            return True, "", {"width":w,"height":h,"brightness":brightness,"sharpness":sharp}
    except Exception:
        return False, "The image could not be read safely. Please upload a JPG or PNG photo.", {}

def plantnet_identify(path, lang="en"):
    """Primary broad plant-species identification using the official Pl@ntNet API."""
    if not PLANTNET_ENABLED:
        raise RuntimeError("PLANTNET_API_KEY is not configured.")
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            if im.width > 2200 or im.height > 2200:
                im.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
            bio = io.BytesIO()
            im.save(bio, format="JPEG", quality=90, optimize=True)
            bio.seek(0)
            filename = os.path.splitext(os.path.basename(path))[0] + ".jpg"
            files = [("images", (filename, bio, "image/jpeg"))]
            # 'auto' is explicitly supported by Pl@ntNet and avoids guessing leaf/flower/fruit.
            data = [("organs", "auto")]
            supported_plantnet_langs = {"en","fr","es","pt","de","it","ar","cs"}
            plang = lang if lang in supported_plantnet_langs else "en"
            url = f"{PLANTNET_URL}/{PLANTNET_PROJECT}"
            params = {
                "api-key": PLANTNET_API_KEY,
                "lang": plang,
                "nb-results": 5,
                "detailed": "true"
            }
            r = requests.post(url, params=params, data=data, files=files, timeout=30)
            if r.status_code == 404:
                raise RuntimeError("Pl@ntNet could not find a reliable plant species in this image.")
            if r.status_code == 429:
                raise RuntimeError("Pl@ntNet daily identification quota has been reached.")
            if r.status_code in (401,403):
                raise RuntimeError("Pl@ntNet API key is invalid or not authorized.")
            r.raise_for_status()
            payload = r.json()
            results = payload.get("results") or []
            if not results:
                return None
            top = results[0]
            species = top.get("species") or {}
            score = float(top.get("score") or 0)
            common = species.get("commonNames") or []
            scientific = species.get("scientificNameWithoutAuthor") or species.get("scientificName") or "Unknown"
            plant = common[0] if common else scientific
            # Keep a moderate threshold; the UI still shows the score and never claims certainty.
            accepted = score >= PLANTNET_MIN_CONFIDENCE
            return {
                "plant": plant, "scientific_name": scientific, "confidence": round(score*100,2),
                "accepted": accepted, "family": ((species.get("family") or {}).get("scientificNameWithoutAuthor") or ""),
                "genus": ((species.get("genus") or {}).get("scientificNameWithoutAuthor") or ""),
                "common_names": common[:5], "gbif_id": str((top.get("gbif") or {}).get("id") or ""),
                "best_match": payload.get("bestMatch") or scientific,
                "engine_version": payload.get("version") or "",
                "remaining_requests": payload.get("remainingIdentificationRequests"),
                "top_results": [{
                    "name": ((x.get("species") or {}).get("scientificNameWithoutAuthor") or "Unknown"),
                    "score": round(float(x.get("score") or 0)*100,2),
                    "common": (((x.get("species") or {}).get("commonNames") or [""])[0])
                } for x in results[:5]]
            }
    except requests.RequestException as e:
        raise RuntimeError("Pl@ntNet service is temporarily unavailable. Please try again.") from e

def plantnet_disease_identify(path):
    """Use Pl@ntNet disease endpoint when available; it covers only a limited set of species/pathologies."""
    if not PLANTNET_ENABLED:
        return None
    try:
        with Image.open(path) as im:
            im=im.convert("RGB")
            if im.width > 2200 or im.height > 2200:
                im.thumbnail((2200,2200), Image.Resampling.LANCZOS)
            bio=io.BytesIO(); im.save(bio,format="JPEG",quality=90,optimize=True); bio.seek(0)
            files=[("images",(os.path.basename(path)+".jpg",bio,"image/jpeg"))]
            data=[("organs","auto")]
            r=requests.post("https://my-api.plantnet.org/v2/diseases/identify",params={"api-key":PLANTNET_API_KEY,"nb-results":5},data=data,files=files,timeout=30)
            if r.status_code in (404,429,401,403):
                return None
            r.raise_for_status()
            payload=r.json(); results=payload.get("results") or []
            if not results: return None
            top=results[0]
            score=float(top.get("score") or 0)
            return {"label":top.get("label") or top.get("name") or "Unknown disease","code":top.get("name") or "","confidence":round(score*100,2),"top_results":results[:5]}
    except Exception:
        return None

def build_plantnet_result(path, lang):
    q=plantnet_identify(path,lang)
    if not q: return None
    q["source"]="Pl@ntNet"
    q["unknown"]=not q["accepted"]
    q["disease"]="Unknown"
    q["explanation"]=f"Plant identification: {q['plant']} ({q['scientific_name']}). Disease identification requires symptom/leaf evidence and is kept separate from species identification."
    return q

def validate_plant_image(path, lang="en"):
    """Independent plant gate that accepts any identifiable plant.
    Disease classification is attempted separately only when the local model
    supports that crop; otherwise the broader AI/PlantNet result is used."""
    if PLANTNET_ENABLED:
        try:
            p = plantnet_identify(path, lang)
            if p and p.get("accepted"):
                p["supported_crop"] = p.get("plant") or p.get("scientific_name") or "Unknown plant"
                return {"ok": True, "provider":"plantnet", **p}
        except Exception:
            pass
    if GEMINI_ENABLED:
        try:
            g = gemini_validate_plant(path, lang)
            if g and g.get("is_plant"):
                crop = g.get("supported_crop") or g.get("plant") or "Unknown plant"
                return {"ok": True, "provider":"gemini", "supported_crop":crop, **g}
            return {"ok": False, "reason":"not_a_plant", "plant":g.get("plant","Unknown") if g else "Unknown", "confidence":g.get("confidence",0) if g else 0}
        except Exception:
            pass
    return {"ok": False, "reason": "validator_unavailable", "plant": "Unknown", "confidence": 0}

def normalize_ai_guidance(data):
    """Map Gemini's guidance keys to the same structure used by local disease guidance."""
    return {
        "name": str(data.get("disease") or "Unknown"),
        "home": data.get("home_remedies") or [],
        "natural": data.get("natural") or [],
        "field": data.get("field") or [],
        "chemical": data.get("chemical") or [],
        "prevention": data.get("prevention") or [],
    }

def predict_strict(path):
    if interpreter is None: raise RuntimeError("model.tflite is missing or could not be loaded.")
    image=Image.open(path).convert("RGB")
    variants=[image, image.transpose(Image.Transpose.FLIP_LEFT_RIGHT), image.rotate(4,expand=False)]
    preds=[]
    for v in variants:
        # preprocess accepts path; save temporary in memory isn't convenient, use same transform here
        shape=input_details[0]["shape"]; h,w=int(shape[1]),int(shape[2])
        a=np.asarray(v.resize((w,h)),dtype=np.float32)
        a=np.expand_dims(a,0)
        if input_details[0]["dtype"]!=np.float32:
            scale,zp=input_details[0]["quantization"]
            a=np.round(a/scale+zp).astype(input_details[0]["dtype"]) if scale else a.astype(input_details[0]["dtype"])
        preds.append(raw_predict(a))
    mean=np.mean(preds,axis=0)
    order=np.argsort(mean)[::-1]
    top=int(order[0]); second=float(mean[order[1]]) if len(order)>1 else 0
    conf=float(mean[top]); margin=conf-second
    cls=labels[top] if top<len(labels) else "Unknown"
    # Stability is the fraction of views agreeing on the same class.
    stable=sum(int(np.argmax(p)==top) for p in preds)/len(preds)
    accepted=bool(conf>=MIN_CONFIDENCE and margin>=MIN_MARGIN and stable>=2/3)
    plant=cls.split("___")[0] if "___" in cls else cls
    plant_name=SUPPORTED_PLANTS.get(plant, plant)
    return {"class":cls,"confidence":conf*100,"margin":margin*100,"stable":stable*100,
            "accepted":accepted,"plant":plant_name,"plant_key":plant,"supported":plant in SUPPORTED_PLANTS}

def default_info(cls,lang):
    clean=cls.replace("___"," - ").replace("_"," ")
    disease_name=clean
    base={
        "en":{"name":disease_name,"home":[f"Inspect the affected {disease_name} symptoms and remove only severely affected tissue where appropriate.","Keep the plant area clean and remove fallen infected material.","Avoid unnecessary leaf wetness and overwatering."],"natural":["Improve air circulation and sunlight exposure.","Water at the root zone when practical.","Keep tools and hands clean when moving between plants."],"field":["Scout nearby plants for similar symptoms.","Remove and dispose of clearly infected debris appropriately.","Maintain suitable spacing and sanitation."],"chemical":["Use a locally approved product only if the diagnosis is confirmed and treatment is appropriate.","Follow the product label, protective-equipment instructions and local agricultural guidance; never mix products unless the label permits it."],"prevention":["Use healthy planting material.","Monitor the crop regularly for early symptoms.","Record recurring symptoms and consult an agricultural expert if the diagnosis is uncertain."]},
        "hi":{"name":disease_name,"home":["प्रभावित पौधे/पत्तियों के लक्षणों की जांच करें और बहुत प्रभावित भाग को उचित तरीके से हटाएं।","गिरे हुए संक्रमित अवशेष हटाकर क्षेत्र साफ रखें।","अनावश्यक पत्ती-नमी और अधिक पानी से बचें।"],"natural":["हवा का अच्छा संचार और पर्याप्त धूप रखें।","जहाँ संभव हो जड़ क्षेत्र में पानी दें।","पौधों के बीच काम करते समय औजार साफ रखें।"],"field":["आसपास के पौधों में समान लक्षण देखें।","स्पष्ट रूप से संक्रमित अवशेषों को उचित तरीके से हटाएं।","उचित दूरी और खेत की स्वच्छता रखें।"],"chemical":["रोग की पुष्टि और स्थानीय सलाह के बाद ही अनुमोदित दवा का उपयोग करें।","लेबल, सुरक्षा निर्देश और स्थानीय कृषि सलाह का पालन करें; बिना लेबल अनुमति के दवाओं को न मिलाएं।"],"prevention":["स्वस्थ रोपण सामग्री का उपयोग करें।","शुरुआती लक्षणों के लिए नियमित निगरानी करें।","संदेह होने पर कृषि विशेषज्ञ से पुष्टि लें।"]},
        "mr":{"name":disease_name,"home":["प्रभावित पानांची/भागांची लक्षणे तपासा आणि जास्त बाधित भाग योग्य पद्धतीने काढा.","संक्रमित अवशेष काढून परिसर स्वच्छ ठेवा.","पानांवर अनावश्यक ओलावा आणि जास्त पाणी टाळा."],"natural":["हवेचे योग्य वहन आणि पुरेसा सूर्यप्रकाश ठेवा.","शक्य असल्यास मुळाजवळ पाणी द्या.","रोपांमध्ये काम करताना साधने स्वच्छ ठेवा."],"field":["आजूबाजूच्या रोपांमध्ये समान लक्षणे तपासा.","स्पष्टपणे संक्रमित अवशेष योग्य पद्धतीने काढा.","योग्य अंतर आणि शेताची स्वच्छता राखा."],"chemical":["रोगाची खात्री आणि स्थानिक कृषी सल्ल्यानंतरच मान्य औषध वापरा.","लेबल व सुरक्षा सूचनांचे पालन करा; लेबल परवानगी देत नसल्यास औषधे मिसळू नका."],"prevention":["निरोगी लागवड साहित्य वापरा.","सुरुवातीची लक्षणे नियमित तपासा.","संशय असल्यास कृषी तज्ज्ञांकडून निदानाची खात्री करा."]}}
    return base.get(lang,base["en"])

def disease_info(cls,lang):
    data=DISEASE_DATA.get(cls) or default_info(cls,"en")
    if lang in data: return data[lang]
    # Translate on demand for 100+ languages if deep-translator/network is available.
    if not TRANSLATOR_AVAILABLE or lang=="en": return data["en"]
    cache_key = f"disease::{cls}"
    disk_cache = _load_translation_cache(lang)
    if cache_key in disk_cache:
        return disk_cache[cache_key]
    try:
        jobs=[]  # (field_key, item_index_or_None, text)
        for k,v in data["en"].items():
            if isinstance(v,list):
                for i,x in enumerate(v): jobs.append((k,i,x))
            else:
                jobs.append((k,None,v))
        results={}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(20,len(jobs) or 1)) as ex:
            futs={ex.submit(_translate,text,lang):(k,i) for k,i,text in jobs}
            for fut in concurrent.futures.as_completed(futs):
                k,i=futs[fut]
                try:
                    value=fut.result()
                    results[(k,i)]=value if value else None
                except Exception:
                    results[(k,i)]=None
        out={}
        all_ok=True
        for k,v in data["en"].items():
            if isinstance(v,list):
                translated=[results.get((k,i)) for i in range(len(v))]
                out[k]=[translated[i] if translated[i] is not None else v[i] for i in range(len(v))]
                if any(x is None for x in translated): all_ok=False
            else:
                translated=results.get((k,None))
                out[k]=translated if translated is not None else v
                if translated is None: all_ok=False
        # Only persist to the on-disk cache when every field translated
        # successfully - a partial failure previously cached the English
        # fallback permanently, "stuck" a disease's guidance in English for
        # that language even after the translator was working again.
        if all_ok:
            disk_cache[cache_key]=out
            _save_translation_cache(lang, disk_cache)
        return out
    except Exception:
        return data["en"]

# ---------- ON-DEMAND TRANSLATION CACHE ----------
# Hinglish: 100+ language translation requests ko cache karke repeated API calls kam kiye jate hain.
TRANSLATION_CACHE_DIR = os.path.join(BASE_DIR, "translation_cache")
os.makedirs(TRANSLATION_CACHE_DIR, exist_ok=True)

def _translation_cache_path(lang):
    return os.path.join(TRANSLATION_CACHE_DIR, f"{lang}.json")

_TCACHE_MEM = {}   # lang -> (file mtime, data): avoids re-reading + re-parsing the JSON for every tr() call on a page
def _load_translation_cache(lang):
    path = _translation_cache_path(lang)
    try:
        mt = os.path.getmtime(path)
        hit = _TCACHE_MEM.get(lang)
        if hit and hit[0] == mt: return hit[1]
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _TCACHE_MEM[lang] = (mt, data)
        return data
    except Exception:
        return {}

def _save_translation_cache(lang, data):
    try:
        target = _translation_cache_path(lang)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, target)
    except Exception:
        pass

TEXTS["en"].update({
"about_text":"RBAgriScan is a farmer-focused web application using a PlantVillage-trained TensorFlow Lite model for supported crop disease screening.",
"ai_label":"AI","access_label":"Access","environment_label":"Environment","install_offline":"Install & offline","date":"Date",
"offline_title":"You're Offline","offline_text":"No internet connection right now. Live plant scanning, weather, and the farmer assistant need a connection.",
"offline_still":"What you can still do","offline_cached":"Browse cached pages","offline_photo":"Line up your photo","offline_retry":"Auto-retry","offline_wait":"Waiting for a connection...",
"project_contact":"Project Owner Contact","get_in_touch":"Get In Touch","mobile":"Mobile"
})
TEXTS["en"].update({
"ai_screening":"AI Screening","disease_guidance":"Disease Guidance","scientific_name":"Scientific name","source":"Source","family":"Family","genus":"Genus","not_reliably_identified":"Not reliably identified","disease_analysis":"Gemini AI disease analysis used.","local_model_matched":"Local disease model matched this crop.","location_environment":"Location & Environment","assistant_title":"Gemini Farmer Assistant","assistant_description":"Ask about crops, plants, diseases, pests, irrigation, fertilizer, weather, soil, farming or your detection result.","hello_farmer":"Hello! Ask me a farming question.","chat_placeholder":"Ask your farming question...","loading":"Loading...","rain":"Rain","wind":"Wind","back_detection":"Back to Detection"
})
TEXTS["en"].update({
"admin_required":"Admin access required.","no_access":"You don't have access to that area.","scan_deleted":"Selected scan deleted.","all_scans_deleted":"All dashboard scan data has been deleted by admin.",
"crop_supported_error":"Please choose a supported crop.","custom_crop_option":"➕ Add my own crop","custom_crop":"My crop name","enter_crop_name":"Enter crop name","custom_crop_required":"Please enter your crop name.","crop_added":"Crop added to your timeline.","crop_not_found":"Crop log not found.","harvested":"Marked as harvested - nice work.","stage_moved":"Moved to {stage} stage.",
"device_created":"Device created. Device key (copy it now - it's shown once): {key}","ticket_created":"Diagnostic ticket created - an agronomist will pick it up.","ticket_not_found":"Ticket not found.","ticket_offline_queued":"No/weak network. Your Plant Hospital ticket was saved on this device and will be sent automatically when the connection returns.","ticket_syncing":"Sending saved Plant Hospital tickets...","ticket_synced":"Saved Plant Hospital ticket sent.","resolution":"Resolution","resolution_placeholder":"Explain what was found and what the farmer should do next.","resolved_on":"Resolved","assigned_to":"Assigned to","open_hospital":"Open Plant Hospital","tickets":"Tickets","open_tickets":"Open tickets","no_tickets":"No Plant Hospital tickets yet.","offline_queue":"Waiting to sync","reopen":"Reopen ticket",
"image_type_error":"Unsupported image type. Use JPG, PNG or WebP.","image_read_error":"The image could not be read safely. Please upload a JPG or PNG photo.","image_dark":"The image is too dark. Please take the photo in better lighting.","image_bright":"The image is overexposed. Please retake the photo without direct glare.","image_blurry":"The image looks blurry. Keep the leaf/plant steady and retake the photo.","image_small":"Please upload a clearer image (at least 256×256 pixels).",
})
# Extra UI labels are merged into the built-in Hindi/Marathi packs so they never
# fall back to missing-key errors. Other languages are translated on demand.
TEXTS["en"].update({
"nav_menu":"Toggle menu","farmer_ai_screening":"Farmer-first AI screening","ai_farming_tools":"AI FARMING TOOLS","ai_farmer_support":"AI FARMER SUPPORT",
"upload_soil_photo":"Upload Soil Photo","capture_soil_camera":"Capture Soil with Camera","analyze_soil_irrigation":"Analyze Soil & Irrigation",
"capture_soil_photo":"Capture Soil Photo","soil_camera_help":"Point the camera at the soil/ground and capture a clear photo.","capture":"Capture","flip_camera":"Flip Camera","close":"Close",
"soil_visual_disclaimer":"Photo analysis is a visual estimate. It cannot measure exact soil moisture, pH or N/P/K. Use a soil test for laboratory values."
})

EXTRA_HI = {
"nav_detection":"पहचान","nav_weather":"मौसम","nav_assistant":"सहायक","nav_plants":"हम जिन पौधों को पहचानते हैं","nav_contact":"संपर्क","nav_suggestions":"सुझाव","nav_install":"ऐप इंस्टॉल करें","nav_refresh":"रिफ्रेश",
"back":"वापस","close":"बंद करें","flip_camera":"कैमरा पलटें","offline":"आप ऑफलाइन हैं","back_online":"आप फिर ऑनलाइन हैं।","offline_browse":"कैश किए गए पेज देखे जा सकते हैं - पहचान, मौसम और चैट के लिए इंटरनेट चाहिए।",
"offline_detection":"आप ऑफलाइन हैं। फोटो अपलोड और कैमरा पहचान के लिए इंटरनेट चाहिए।","unrecognized":"पहचान नहीं हुई","confidence_label":"विश्वास","gemini_fallback":"Gemini AI सहायता","server_error":"सर्वर तक पहुंच नहीं हो सकी। कनेक्शन जांचें।",
"chat_you":"आप","chat_assistant":"स्मार्ट क्रॉप AI","chat_offline":"स्मार्ट क्रॉप AI ऑफलाइन सहायक","chat_offline_msg":"आप अभी ऑफलाइन हैं, इसलिए सहायक जवाब नहीं दे सकता। इंटरनेट आने पर फिर प्रयास करें।",
"chat_general_prompt":"फसल, पौधे, रोग, कीट, सिंचाई, खाद, मौसम, खेती, मिट्टी या किसी भी कृषि प्रश्न के बारे में पूछें।","weather_loading":"लोड हो रहा है...","weather_offline":"आप ऑफलाइन हैं - मौसम के लिए इंटरनेट चाहिए।","weather_rain":"बारिश","weather_wind":"हवा","humidity":"नमी","location_denied":"लोकेशन की अनुमति नहीं मिली।","geo_unsupported":"जियोलोकेशन उपलब्ध नहीं है।",
"timeline_title":"स्वचालित फसल टाइमलाइन","timeline_text":"बुवाई से कटाई तक फसल की अवस्था ट्रैक करें और स्थानीय मौसम के अनुसार अनुमानित अवधि व देखभाल रिमाइंडर पाएं।","log_crop":"नई फसल जोड़ें","crop":"फसल","nickname":"नाम (वैकल्पिक)","planted_on":"बुवाई की तारीख","city_weather":"शहर (मौसम के अनुसार समय)","start_tracking":"ट्रैकिंग शुरू करें","your_crops":"आपकी फसलें","planted":"बोई गई","stage":"अवस्था","reminders_due":"रिमाइंडर बाकी","no_crops":"अभी कोई फसल नहीं जोड़ी गई है।","growth_timeline":"विकास टाइमलाइन","stage_history":"अवस्था इतिहास","advance_to":"अगली अवस्था","mark_harvested":"कटाई पूरी हुई","done":"पूरा","all_crops":"सभी फसलें","optional_note":"इस बदलाव के बारे में वैकल्पिक नोट",
"hospital":"प्लांट हॉस्पिटल","hospital_farmer":"निदान में समस्या है? सत्यापित कृषि विशेषज्ञ को भेजें।","hospital_expert":"किसानों के खुले और असाइन किए गए निदान टिकट।","open_ticket":"निदान टिकट खोलें","attach_scan":"हाल की स्कैन जोड़ें (वैकल्पिक)","none":"कोई नहीं","city_region":"शहर / क्षेत्र","describe":"समस्या बताएं","submit_expert":"कृषि विशेषज्ञ को भेजें","your_tickets":"आपके टिकट","queue":"कतार","farmer":"किसान","prediction":"अनुमान","opened":"खोला गया","open":"खुला","nothing_here":"अभी कुछ नहीं है।","ticket":"टिकट","unclassified":"वर्गीकृत नहीं","no_location":"लोकेशन नहीं दी गई","says":"कहते हैं","claim":"टिकट लें","conversation":"बातचीत","no_messages":"अभी कोई संदेश नहीं है।","write_reply":"जवाब लिखें...","mark_resolved":"समाधान किया","reopen_claim":"फिर खोलें","send":"भेजें",
"iot_title":"स्मार्ट सिंचाई डैशबोर्ड","iot_text":"मिट्टी की नमी, तापमान, नमी या NPK सेंसर जोड़कर लाइव जानकारी देखें।","register_device":"नया डिवाइस जोड़ें","device_name":"डिवाइस का नाम","link_crop":"फसल से जोड़ें (वैकल्पिक)","create_device":"डिवाइस बनाएं","your_devices":"आपके डिवाइस","no_devices":"अभी कोई डिवाइस नहीं है।","ingesting":"डेटा प्राप्त करना","seven_day":"7 दिन का ट्रेंड","thirty_day":"30 दिन का ट्रेंड","soil_moisture":"मिट्टी की नमी %","temp":"तापमान °C","humidity_chart":"नमी %","alert_threshold":"25% से कम मिट्टी की नमी या 40°C से अधिक / 2°C से कम तापमान पर अलर्ट बनेगा।"
}
EXTRA_MR = {
"nav_detection":"ओळख","nav_weather":"हवामान","nav_assistant":"सहाय्यक","nav_plants":"आम्ही ओळखत असलेली झाडे","nav_contact":"संपर्क","nav_suggestions":"सूचना","nav_install":"अॅप इंस्टॉल करा","nav_refresh":"रिफ्रेश",
"back":"मागे","close":"बंद करा","flip_camera":"कॅमेरा बदला","offline":"तुम्ही ऑफलाइन आहात","back_online":"तुम्ही पुन्हा ऑनलाइन आहात.","unrecognized":"ओळख झाली नाही","confidence_label":"विश्वास","gemini_fallback":"Gemini AI मदत","server_error":"सर्व्हरशी संपर्क झाला नाही. कनेक्शन तपासा.",
"chat_you":"तुम्ही","chat_assistant":"स्मार्ट क्रॉप AI","chat_offline":"स्मार्ट क्रॉप AI ऑफलाइन सहाय्यक","chat_offline_msg":"तुम्ही सध्या ऑफलाइन आहात, त्यामुळे सहाय्यक उत्तर देऊ शकत नाही. इंटरनेट आल्यावर पुन्हा प्रयत्न करा.","chat_general_prompt":"पिके, झाडे, रोग, किडी, सिंचन, खत, हवामान, शेती, माती किंवा कोणत्याही कृषी प्रश्नाबद्दल विचारा.",
"weather_loading":"लोड होत आहे...","weather_offline":"तुम्ही ऑफलाइन आहात - हवामानासाठी इंटरनेट आवश्यक आहे.","weather_rain":"पाऊस","weather_wind":"वारा","humidity":"आर्द्रता","location_denied":"स्थानाची परवानगी मिळाली नाही.","geo_unsupported":"जिओलोकेशन उपलब्ध नाही.",
"timeline_title":"स्वयंचलित पीक टाइमलाइन","timeline_text":"लागवडीपासून काढणीपर्यंत पिकाची अवस्था ट्रॅक करा आणि स्थानिक हवामानानुसार कालावधी व काळजीचे स्मरणपत्र मिळवा.","log_crop":"नवीन पीक जोडा","crop":"पीक","nickname":"नाव (पर्यायी)","planted_on":"लागवडीची तारीख","city_weather":"शहर (हवामानानुसार वेळ)","start_tracking":"ट्रॅकिंग सुरू करा","your_crops":"तुमची पिके","planted":"लागवड","stage":"अवस्था","reminders_due":"स्मरणपत्रे बाकी","no_crops":"अजून कोणतेही पीक नोंदवलेले नाही.","growth_timeline":"वाढीची टाइमलाइन","stage_history":"अवस्था इतिहास","advance_to":"पुढील अवस्था","mark_harvested":"काढणी पूर्ण","done":"पूर्ण","all_crops":"सर्व पिके","optional_note":"या बदलाबद्दल पर्यायी नोंद",
"hospital":"प्लांट हॉस्पिटल","hospital_farmer":"निदानाबद्दल शंका आहे? सत्यापित कृषी तज्ज्ञाकडे पाठवा.","hospital_expert":"शेतकऱ्यांची खुले आणि नियुक्त निदान तिकिटे.","open_ticket":"निदान तिकीट उघडा","attach_scan":"अलीकडील स्कॅन जोडा (पर्यायी)","none":"काहीही नाही","city_region":"शहर / प्रदेश","describe":"समस्या सांगा","submit_expert":"कृषी तज्ज्ञाकडे पाठवा","your_tickets":"तुमची तिकिटे","queue":"रांग","farmer":"शेतकरी","prediction":"अंदाज","opened":"उघडले","open":"उघडे","nothing_here":"अजून काही नाही.","ticket":"तिकीट","unclassified":"वर्गीकृत नाही","no_location":"स्थान दिलेले नाही","says":"म्हणतात","claim":"तिकीट घ्या","conversation":"संवाद","no_messages":"अजून संदेश नाहीत.","write_reply":"उत्तर लिहा...","mark_resolved":"निराकरण केले","reopen_claim":"पुन्हा उघडा","send":"पाठवा",
"iot_title":"स्मार्ट सिंचन डॅशबोर्ड","iot_text":"मातीतील ओलावा, तापमान, आर्द्रता किंवा NPK सेन्सर जोडून थेट माहिती पहा.","register_device":"नवीन डिव्हाइस नोंदवा","device_name":"डिव्हाइसचे नाव","link_crop":"पिकाशी जोडा (पर्यायी)","create_device":"डिव्हाइस तयार करा","your_devices":"तुमची डिव्हाइस","no_devices":"अजून डिव्हाइस नाही.","ingesting":"डेटा घेणे","seven_day":"7 दिवसांचा ट्रेंड","thirty_day":"30 दिवसांचा ट्रेंड","soil_moisture":"मातीतील ओलावा %","temp":"तापमान °C","humidity_chart":"आर्द्रता %","alert_threshold":"25% पेक्षा कमी मातीतील ओलावा किंवा 40°C पेक्षा जास्त / 2°C पेक्षा कमी तापमान असल्यास अलर्ट तयार होईल."
}
TEXTS["hi"].update(EXTRA_HI)
TEXTS["mr"].update(EXTRA_MR)
TEXTS["en"].update({
"smart_farming_ai":"Smart Farming AI","choose_analysis":"Choose what you want to analyze","choose_analysis_help":"Scan any plant for identification and disease guidance, or analyze soil and irrigation.","scan_any_plant":"Scan Any Plant","plant_id_disease":"Plant identification + disease guidance","soil_analysis":"Soil Analysis","soil_sub":"Soil & irrigation","plant_scan_help":"Upload a clear plant/leaf/crop image or use your camera. Plant identification is separate from disease analysis.","soil_photo_help":"Upload or capture a soil/farm-ground photo. AI will visually screen moisture, drainage, texture and irrigation needs.","clear_soil":"Clear soil photo works best","ai_soil_title":"Soil & Irrigation","ai_soil_report":"Soil & Irrigation Report","disease_unavailable":"Disease analysis is not available for this plant model yet.","share_result":"Share Result","plant_profile":"Plant Profile","identified_plant":"Identified Plant","analysis_source":"Analysis source"})
TEXTS["hi"].update({"smart_farming_ai":"स्मार्ट फार्मिंग AI","choose_analysis":"आप क्या जांचना चाहते हैं?","choose_analysis_help":"किसी भी पौधे की पहचान और रोग संबंधी सलाह देखें या मिट्टी और सिंचाई का विश्लेषण करें।","scan_any_plant":"कोई भी पौधा स्कैन करें","plant_id_disease":"पौधे की पहचान + रोग सलाह","soil_analysis":"मिट्टी विश्लेषण","soil_sub":"मिट्टी और सिंचाई","plant_scan_help":"साफ पौधे/पत्ती/फसल की फोटो अपलोड करें या कैमरा इस्तेमाल करें।","soil_photo_help":"मिट्टी/खेत की फोटो अपलोड करें या कैमरे से लें।","clear_soil":"साफ मिट्टी की फोटो बेहतर रहेगी","ai_soil_title":"मिट्टी और सिंचाई","ai_soil_report":"मिट्टी और सिंचाई रिपोर्ट","disease_unavailable":"इस पौधे के लिए रोग मॉडल अभी उपलब्ध नहीं है।","share_result":"परिणाम साझा करें","plant_profile":"पौधे की जानकारी","identified_plant":"पहचाना गया पौधा","analysis_source":"विश्लेषण स्रोत"})
TEXTS["mr"].update({"smart_farming_ai":"स्मार्ट फार्मिंग AI","choose_analysis":"तुम्हाला काय तपासायचे आहे?","choose_analysis_help":"कोणताही वनस्पती स्कॅन करून ओळख व रोगाविषयी मार्गदर्शन मिळवा किंवा माती व सिंचनाचे विश्लेषण करा.","scan_any_plant":"कोणतीही वनस्पती स्कॅन करा","plant_id_disease":"वनस्पती ओळख + रोग मार्गदर्शन","soil_analysis":"मातीचे विश्लेषण","soil_sub":"माती व सिंचन","plant_scan_help":"स्वच्छ वनस्पती/पान/पिकाचा फोटो अपलोड करा किंवा कॅमेरा वापरा. वनस्पतीची ओळख आणि रोगाचे विश्लेषण वेगळे ठेवले आहे.","soil_photo_help":"माती/शेताच्या जमिनीचा फोटो अपलोड करा किंवा कॅमेऱ्याने घ्या. AI ओलावा, निचरा, पोत आणि सिंचनाची गरज तपासेल.","clear_soil":"स्वच्छ मातीचा फोटो अधिक चांगला राहील","ai_soil_title":"माती व सिंचन","ai_soil_report":"माती व सिंचन अहवाल","disease_unavailable":"या वनस्पतीसाठी रोगाचे मॉडेल सध्या उपलब्ध नाही.","share_result":"निकाल शेअर करा","plant_profile":"वनस्पती माहिती","identified_plant":"ओळखलेली वनस्पती","analysis_source":"विश्लेषण स्रोत"})

TEXTS["hi"].update({"nav_menu":"मेनू खोलें","custom_crop_option":"➕ अपनी फसल जोड़ें","custom_crop":"फसल का नाम","enter_crop_name":"फसल का नाम लिखें","custom_crop_required":"कृपया अपनी फसल का नाम लिखें।","farmer_ai_screening":"किसानों के लिए AI स्क्रीनिंग","ai_farming_tools":"AI खेती के उपकरण","ai_farmer_support":"AI किसान सहायता","upload_soil_photo":"मिट्टी की फोटो अपलोड करें","capture_soil_camera":"कैमरे से मिट्टी की फोटो लें","analyze_soil_irrigation":"मिट्टी और सिंचाई का विश्लेषण करें","capture_soil_photo":"मिट्टी की फोटो लें","soil_camera_help":"कैमरे को मिट्टी/जमीन की ओर रखें और साफ फोटो लें।","capture":"फोटो लें","flip_camera":"कैमरा पलटें","close":"बंद करें","soil_visual_disclaimer":"फोटो विश्लेषण केवल दृश्य अनुमान है। यह मिट्टी की सही नमी, pH या N/P/K नहीं मापता। प्रयोगशाला मिट्टी जांच करें।"})
TEXTS["mr"].update({"nav_menu":"मेनू उघडा","custom_crop_option":"➕ माझे पीक जोडा","custom_crop":"पिकाचे नाव","enter_crop_name":"पिकाचे नाव लिहा","custom_crop_required":"कृपया तुमच्या पिकाचे नाव लिहा.","farmer_ai_screening":"शेतकऱ्यांसाठी AI स्क्रीनिंग","ai_farming_tools":"AI शेती साधने","ai_farmer_support":"AI शेतकरी सहाय्य","upload_soil_photo":"मातीचा फोटो अपलोड करा","capture_soil_camera":"कॅमेऱ्याने मातीचा फोटो घ्या","analyze_soil_irrigation":"माती व सिंचनाचे विश्लेषण करा","capture_soil_photo":"मातीचा फोटो घ्या","soil_camera_help":"कॅमेरा माती/जमिनीच्या दिशेने ठेवा आणि स्वच्छ फोटो घ्या.","capture":"फोटो घ्या","flip_camera":"कॅमेरा उलटा करा","close":"बंद करा","soil_visual_disclaimer":"फोटो विश्लेषण हा फक्त दृश्य अंदाज आहे. तो मातीतील अचूक ओलावा, pH किंवा N/P/K मोजत नाही. प्रयोगशाळेतील माती तपासणी करा."})

def _translate_batch(texts, lang):
    """Translate a whole list of short strings with a single Google Translate
    request by joining them with a rare separator and splitting the result
    back apart. This is what actually makes a first-time language switch land
    in about one network round trip instead of dozens/hundreds of them.
    Returns None (never a mismatched/misaligned list) if anything about the
    joined translation looks untrustworthy, so the caller can fall back to
    translating those items one at a time instead of risking a scrambled UI."""
    _BATCH_SEP = "\n@@@\n"
    if not TRANSLATOR_AVAILABLE or not texts or _translator_down():
        return None
    joined = _BATCH_SEP.join(texts)
    try:
        result = GoogleTranslator(source="en", target=lang).translate(joined)
    except Exception:
        _translator_result(False)
        return None
    if not result:
        return None
    # The translator sometimes reflows whitespace/newlines around the
    # separator, so match it loosely instead of requiring an exact echo.
    parts = re.split(r"\s*@+\s*", result)
    parts = [p.strip() for p in parts if p.strip() != ""]
    if len(parts) != len(texts):
        return None
    return parts

def _translate_chunk(args):
    """Translate one batch of (key, text) pairs for `lang`: try the fast
    single-request batch translation first, and only fall back to translating
    items individually (still correct, just slower) if the batch call failed
    or its result couldn't be safely split back apart."""
    chunk, lang = args
    keys = [k for k, _ in chunk]
    texts = [t for _, t in chunk]
    batched = _translate_batch(texts, lang)
    out = {}
    if batched is not None:
        for k, translated in zip(keys, batched):
            out[k] = (translated, True)
    else:
        for k, t in chunk:
            translated = _translate(t, lang)
            out[k] = (translated or t, bool(translated))
    return out

TEXTS["en"].update({
 "result": "Scan Result", "assistant_title": "AI Farmer Assistant",
 "unknown_text": "This image could not be confidently recognized as a plant. Please use a clear photo of a leaf or crop.",
 "voice_start": "Speak", "voice_stop": "Stop", "voice_listening": "Listening...",
 "voice_unsupported": "Voice input is not supported on this browser. Please type your question.",
 "voice_denied": "Microphone permission is required for voice input. You can type your question instead.",
 "voice_error": "Could not understand the voice. Please try again or type your question.",
 "weather_forecast": "Forecast", "estimated_risk": "Estimated risk", "advisory": "Advisory",
 "risk_disclaimer": "Estimated from weather conditions only. It does not mean disease will definitely occur.",
 "risk_low": "Low", "risk_moderate": "Moderate", "risk_high": "High",
 "speak_answers": "Read answers aloud", "attach_photo": "Attach plant photo", "photo_attached": "Photo attached", "remove_photo": "Remove",
})

_WARMING = set()
_WARM_LOCK = threading.Lock()

def _warm_language_async(lang):
    """Fill the translation cache for `lang` in a background thread (never blocks a page)."""
    if _translator_down():
        return
    with _WARM_LOCK:
        if lang in _WARMING:
            return
        _WARMING.add(lang)
    def _run():
        try:
            translate_texts(lang, network=True)
        except Exception:
            app.logger.exception("Background translation warm-up failed for %s", lang)
        finally:
            with _WARM_LOCK:
                _WARMING.discard(lang)
    threading.Thread(target=_run, daemon=True).start()

def translate_texts(lang, network=True):
    """Return the full UI text dict for `lang`. On-disk + in-memory caches make
    every switch after the first one instant. The first switch to a brand-new
    language used to translate ~150 UI strings one network call at a time
    (even with 20 concurrent workers, that's several rounds of network
    latency - several seconds, not "within a second"). Strings are now
    grouped into small batches and each batch is translated with a single
    request (see _translate_batch), so a first-time switch takes roughly one
    network round trip instead of dozens, with per-string translation kept
    only as an automatic fallback if a batch can't be parsed back apart.

    Only *successful* translations are written to the on-disk/in-memory cache.
    Earlier code cached whatever came back even when the translate call had
    failed (network hiccup, rate limit, etc.) - the untranslated English text
    got written to disk as if it were the real translation, and because the
    cache was then considered "complete" for that key, it silently stayed in
    English forever, even after the network/service recovered. Failed keys
    are served in English for *this* request only, and are retried on the
    next request instead of being locked in."""
    cache = TEXTS.setdefault(lang, {})
    cache.update(_load_translation_cache(lang))
    if not TRANSLATOR_AVAILABLE:
        out = dict(cache)
        for k, v in TEXTS["en"].items(): out.setdefault(k, v)
        return out
    missing = [(k, v) for k, v in TEXTS["en"].items() if k not in cache]
    out = dict(cache)
    if not network:
        # Page-render mode: cached/bundled strings + English fallback for the rest, instantly.
        for k, v in TEXTS["en"].items(): out.setdefault(k, v)
        if missing and lang != "en":
            _warm_language_async(lang)
        return out
    if missing:
        newly_cached = {}
        CHUNK_SIZE = 25  # keeps each joined request comfortably short
        chunks = [missing[i:i + CHUNK_SIZE] for i in range(0, len(missing), CHUNK_SIZE)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, len(chunks))) as ex:
            for chunk_result in ex.map(_translate_chunk, [(c, lang) for c in chunks]):
                for k, (translated, ok) in chunk_result.items():
                    out[k] = translated
                    if ok:
                        newly_cached[k] = translated
        if newly_cached:
            cache.update(newly_cached)
            _save_translation_cache(lang, cache)
    return out

@app.route("/api/ui-translations/<lang>", methods=["GET"])
def ui_translations(lang):
    """Return the complete UI dictionary for a selected language.

    The browser uses this endpoint to warm/cache a language before navigation.
    Built-in Hindi/Marathi and any previously cached language are effectively
    instant; other languages are translated once and persisted in translation_cache/.
    """
    if lang not in LANG_MAP:
        return jsonify({"ok": False, "error": "Unsupported language."}), 400
    try:
        data = translate_texts(lang)
        return jsonify({"ok": True, "language": lang, "texts": data,
                        "direction": "rtl" if lang.split("-")[0].lower() in RTL_LANGS else "ltr"})
    except Exception:
        app.logger.exception("UI translation failed")
        # The endpoint still returns the English dictionary so the client can
        # fail gracefully without breaking the language selector.
        return jsonify({"ok": False, "language": lang, "texts": TEXTS.get("en", {})}), 200

def translate_phrase(text, lang=None):
    """Translate a short dynamic label into the selected UI language and cache it."""
    lang = lang or current_lang()
    if not text or lang == "en":
        return text
    if not TRANSLATOR_AVAILABLE:
        return text
    cache = _load_translation_cache(lang)
    key = "phrase::" + text
    if key in cache:
        return cache[key]
    value = _translate(text, lang)
    if not value:
        return text
    cache[key] = value
    _save_translation_cache(lang, cache)
    return value

# Template helper: instant (cache/bundled/English). Misses are translated in a background
# thread, so the NEXT page view is translated and no page ever waits on a translation API.
_PHRASE_QUEUE = {}
def tr_fast(text, lang=None):
    lang = lang or session.get("lang", "en")
    if not text or lang == "en" or not TRANSLATOR_AVAILABLE:
        return text
    cache = _load_translation_cache(lang)
    key = "phrase::" + text
    if key in cache:
        return cache[key]
    if not _translator_down():
        start = False
        with _WARM_LOCK:
            q = _PHRASE_QUEUE.setdefault(lang, set())
            q.add(text)
            if ("phrases", lang) not in _WARMING:
                _WARMING.add(("phrases", lang)); start = True
        if start:
            def _run(lg=lang):
                try:
                    while True:
                        with _WARM_LOCK:
                            batch = [x for _, x in zip(range(30), list(_PHRASE_QUEUE.get(lg, ())))]
                            for x in batch: _PHRASE_QUEUE[lg].discard(x)
                        if not batch or _translator_down(): break
                        for x in batch: translate_phrase(x, lg)
                except Exception:
                    app.logger.exception("Phrase translation worker failed")
                finally:
                    with _WARM_LOCK: _WARMING.discard(("phrases", lg))
            threading.Thread(target=_run, daemon=True).start()
    return text

# ---------- LANGUAGE ROUTE ----------
# Hinglish: Selected language session me save hoti hai aur UI direction/translation update hota hai.
@app.route("/set-language/<lang>")
def set_language(lang):
    # Keep the language in BOTH the signed Flask session and a small cookie.
    # The cookie is a recovery path for browsers/PWAs that restore a stale or
    # missing session cookie after a navigation.
    if lang not in LANG_MAP:
        lang = "en"
    session["lang"] = lang
    session.modified = True

    next_url = request.args.get("next")
    if not next_url and request.referrer:
        from urllib.parse import urlparse
        ref = urlparse(request.referrer)
        if ref.netloc == request.host:
            next_url = ref.path + (("?" + ref.query) if ref.query else "")
    next_url = _safe_next(next_url)

    response = redirect(next_url)
    response.set_cookie(
        "rbagriscan_lang", lang, max_age=31536000,
        httponly=False, samesite="Lax", secure=request.is_secure
    )
    # Never let a CDN/browser cache the redirect itself.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response

# ---------- HOME + PLANT/SOIL ANALYSIS ----------
# Hinglish: Main page par Plant Detection aur Soil Detection ke separate modes/results handle hote hain.
# ---------- SHARED PLANT ANALYSIS PIPELINE (upload + camera use THIS ONLY) ----------
# image quality -> plant validation (PlantNet/Gemini) -> does the LOCAL disease model
# cover this plant? -> local TFLite OR Gemini fallback -> honest "not confident" states.
PLANT_NOT_IDENTIFIED_MSG = "Plant Not Identified. Please take a clear, close photo of a plant leaf or crop."
LOW_CONFIDENCE_MSG = "Disease could not be confidently identified."

# Which words in the validator's plant name/scientific name prove the photo is the crop
# that the local TFLite class belongs to. Without this check a Mango leaf could be
# forced into a Tomato disease class.
LOCAL_CROP_KEYWORDS = {
    "Apple": ["malus", "apple"], "Corn_(maize)": ["zea mays", "maize", "corn", "sweetcorn"],
    "Grape": ["vitis", "grape"], "Peach": ["prunus persica", "peach"],
    "Pepper,_bell": ["capsicum", "bell pepper", "sweet pepper"], "Potato": ["solanum tuberosum", "potato"],
    "Strawberry": ["fragaria", "strawberry"], "Tomato": ["solanum lycopersicum", "lycopersicon", "tomato"],
}
_LOCAL_CROP_EXCLUDE = re.compile(r"(custard|rose|wood|star|thorn|may|love|sugar|oak)\s+apple|sweet potato|strawberry\s+(tree|guava)|corn\s+salad|sea\s+grape|oregon\s+grape")

def gate_matches_local_crop(gate, plant_key):
    parts = [gate.get(k) for k in ("supported_crop", "plant", "scientific_name", "genus", "best_match")]
    parts += list(gate.get("common_names") or [])
    text = " ".join(str(x) for x in parts if x).lower()
    if not text or _LOCAL_CROP_EXCLUDE.search(text):
        return False
    return any(re.search(r"\b" + re.escape(k) + r"\b", text) for k in LOCAL_CROP_KEYWORDS.get(plant_key, ()))

def save_scan(user_id, image_name, plant, disease, confidence, status, health_score=None, severity=None, result_json=None, field_id=None, weather_json=None):
    c = db()
    try:
        cur = c.execute("INSERT INTO scans(user_id,image,plant,disease,confidence,status,health_score,severity,result_json,field_id,weather_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (user_id, image_name, str(plant)[:255], str(disease)[:255], float(confidence or 0), str(status)[:255],
                         health_score, severity, result_json, field_id, weather_json))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()

def _gemini_usable(ai):
    return bool(ai) and str(ai.get("plant", "Unknown")).strip().lower() not in ("unknown", "unknown plant", "") and float(ai.get("confidence") or 0) >= 30

def _analyze_plant_image_core(path, lang="en"):
    """Returns a dict describing the final result. Never raises for 'normal' bad input.
    Keys: unknown, plant, class, confidence, source, message, info, explanation, save_status ..."""
    ok, qmsg, quality = image_quality(path)
    if not ok:
        return {"unknown": True, "plant": "Unknown", "class": "Not Identified", "confidence": 0, "source": "Quality Check",
                "message": qmsg, "quality": quality, "stage": "quality"}
    gate = validate_plant_image(path, lang)
    if not gate.get("ok"):
        unavailable = gate.get("reason") == "validator_unavailable"
        return {"unknown": True, "plant": "Unknown", "class": "Not Identified", "confidence": 0, "source": "Plant Validation",
                "validation_reason": gate.get("reason"), "stage": "gate",
                "message": "Plant validation is not available right now. Please try again in a moment." if unavailable else PLANT_NOT_IDENTIFIED_MSG}
    plant_name = gate.get("supported_crop") or gate.get("plant") or "Unknown plant"
    base = {"unknown": False, "plant": plant_name, "scientific_name": gate.get("scientific_name", ""),
            "family": gate.get("family", ""), "genus": gate.get("genus", ""), "gbif_id": gate.get("gbif_id", ""),
            "plant_confidence": gate.get("confidence", 0)}
    try:
        local = predict_strict(path)
    except RuntimeError:
        local = None
    covered = bool(local and local.get("supported") and gate_matches_local_crop(gate, local.get("plant_key")))
    if covered and local.get("accepted"):
        out = dict(base); out.update({"plant": local["plant"], "class": local["class"], "disease": local["class"],
            "confidence": local["confidence"], "margin": local.get("margin"), "stable": local.get("stable"),
            "source": "Local TFLite + Plant Validation", "info": disease_info(local["class"], lang), "save_status": "Local TFLite",
            "local_disease": True})
        return out
    if GEMINI_ENABLED:
        try:
            ai = gemini_detect(path, lang)
        except Exception:
            app.logger.exception("Gemini disease analysis failed"); ai = None
        if _gemini_usable(ai):
            out = dict(base); out.update({"plant": base["plant"] if gate.get("provider") == "plantnet" else ai.get("plant", base["plant"]),
                "class": ai.get("disease", "Unknown"), "disease": ai.get("disease", "Unknown"), "confidence": float(ai.get("confidence") or 0),
                "source": "Gemini broad plant analysis", "explanation": ai.get("explanation", ""), "info": normalize_ai_guidance(ai),
                "save_status": "Gemini broad plant analysis", "gemini": True})
            return out
    if covered:  # supported crop but the model was not sure -> never guess a disease
        out = dict(base); out.update({"plant": local["plant"], "class": LOW_CONFIDENCE_MSG, "disease": LOW_CONFIDENCE_MSG, "low_confidence": True,
            "confidence": local["confidence"], "source": "Local TFLite (low confidence)", "info": None,
            "message": LOW_CONFIDENCE_MSG + " Please retake a clear, close photo of one affected leaf.", "save_status": "Low confidence"})
        return out
    out = dict(base); out.update({"class": "Disease analysis unavailable for this plant", "disease": "Disease analysis unavailable for this plant",
        "confidence": gate.get("confidence", 0), "source": "Pl@ntNet plant identification" if gate.get("provider") == "plantnet" else "Plant validation",
        "explanation": "This plant was identified, but the local disease model does not cover this species.", "info": None,
        "save_status": "Plant identified only"})
    return out

# ---------- CROP HEALTH SCORE (real data only) ----------
# Indicative score 0-100 derived ONLY from the diagnosed condition and model confidence:
#   healthy            -> 100 - 0.3*(100-confidence)   (never below 70)
#   medium severity    -> 55   (spots, rust, mildew, scab, mold, scorch, mites ...)
#   high severity      -> 35   (blights, viruses, rots, wilts, cankers, bacterial, greening ...)
# Not scored (None -> "Not enough data yet"): unknown/low-confidence results, confidence < 50,
# or conditions we cannot classify. It is NOT a lab measurement.
_SEV_HIGH = ("blight", "virus", "mosaic", "curl", "wilt", "rot", "canker", "bacterial", "greening", "huanglongbing", "haunglongbing")
_SEV_MED = ("rust", "mildew", "scab", "spot", "mold", "scorch", "mite", "measles", "esca")

def classify_severity(disease):
    d = str(disease or "").lower().replace(" ", "_")
    if not d or any(x in d for x in ("unavailable", "confidently", "not_identified", "unknown")):
        return None
    if "healthy" in d or d in ("none", "no_disease", "no_disease_detected", "no_visible_disease"):
        return "none"
    if "early_blight" in d:
        return "medium"
    if any(k in d for k in _SEV_HIGH): return "high"
    if any(k in d for k in _SEV_MED): return "medium"
    return None

def health_assessment(result):
    if not result or result.get("unknown") or result.get("low_confidence"):
        return None, None
    conf = float(result.get("confidence") or 0)
    if conf < 50:
        return None, None
    sev = classify_severity(result.get("class") or result.get("disease"))
    if sev is None:
        return None, None
    if sev == "none":
        return int(round(max(70, 100 - 0.3 * (100 - min(conf, 100))))), sev
    return {"medium": 55, "high": 35}[sev], sev

def analyze_plant_image(path, lang="en"):
    r = _analyze_plant_image_core(path, lang)
    r["health_score"], r["severity"] = health_assessment(r)
    return r

def owned_field_id(raw, user_id=None):
    """Return the field id only if it belongs to the logged-in user; anything else (tampered/foreign/invalid) -> None."""
    try: fid = int(raw)
    except (TypeError, ValueError): return None
    c = db()
    try:
        row = c.execute("SELECT id FROM fields WHERE id=? AND user_id=?", (fid, user_id or session.get("user_id"))).fetchone()
    finally:
        c.close()
    return row["id"] if row else None

def _notify_health_change(user_id, scan_id, r, field_id):
    """Compare with the previous SCORED scan of the same plant (and same field when one is set). Never raises."""
    try:
        new = r.get("health_score")
        if new is None: return
        c = db()
        try:
            if field_id:
                prev = c.execute("SELECT health_score FROM scans WHERE user_id=? AND plant=? AND field_id=? AND health_score IS NOT NULL AND id<? ORDER BY id DESC LIMIT 1",
                                 (user_id, r.get("plant"), field_id, scan_id)).fetchone()
            else:
                prev = c.execute("SELECT health_score FROM scans WHERE user_id=? AND plant=? AND health_score IS NOT NULL AND id<? ORDER BY id DESC LIMIT 1",
                                 (user_id, r.get("plant"), scan_id)).fetchone()
        finally:
            c.close()
        if not prev: return
        diff = new - prev["health_score"]
        if abs(diff) < 15: return
        notify(user_id, "health_drop" if diff < 0 else "health_up", {"plant": r.get("plant"), "old": prev["health_score"], "new": new},
               link=f"/history/{scan_id}", dedupe_key=f"hc-{scan_id}")
    except Exception:
        app.logger.exception("health-change notification failed")

def save_scan_result(user_id, image_name, r, field_id=None, weather_json=None):
    snap = {k: r.get(k) for k in ("plant", "scientific_name", "class", "confidence", "source", "explanation", "info",
                                  "low_confidence", "message", "health_score", "severity", "plant_confidence")}
    sid = save_scan(user_id, image_name, r.get("plant", "Unknown"), r.get("class", "Unknown"), r.get("confidence", 0),
                    r.get("save_status", "Scan"), r.get("health_score"), r.get("severity"), json.dumps(snap, ensure_ascii=False, default=str), field_id, weather_json)
    _notify_health_change(user_id, sid, r, field_id)
    return sid

def _store_upload(f, user_id, prefix=""):
    """Save an uploaded image under a server-generated, user-prefixed name. Returns (name, path) or (None, None)."""
    ext = (f.filename.rsplit(".", 1)[1].lower() if f and f.filename and "." in f.filename else "jpg")
    if ext not in ALLOWED_EXTENSIONS: ext = "jpg"
    name = f"u{user_id}_{prefix}{int(time.time()*1000)}_{secrets.token_hex(6)}.{ext}"
    path = os.path.join(UPLOAD_FOLDER, name)
    f.save(path)
    if not validate_uploaded_image(path):
        try: os.remove(path)
        except OSError: pass
        return None, None
    return name, path

@app.route("/media/<path:name>")
@login_required
def media(name):
    """Private image delivery. Owner, or an expert/admin looking at a Plant Hospital ticket/scan."""
    name = os.path.basename(name)
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,200}", name) or name.startswith("."):
        abort(404)
    if not os.path.isfile(os.path.join(UPLOAD_FOLDER, name)):
        abort(404)
    uid = session["user_id"]
    allowed = name.startswith(f"u{uid}_")
    if not allowed:
        c = db()
        try:
            allowed = bool(c.execute("SELECT 1 AS ok FROM scans WHERE image=? AND user_id=? LIMIT 1", (name, uid)).fetchone()
                           or c.execute("SELECT 1 AS ok FROM diagnostic_tickets WHERE image=? AND user_id=? LIMIT 1", (name, uid)).fetchone()
                           or c.execute("SELECT 1 AS ok FROM community_posts WHERE image=? AND status='visible' LIMIT 1", (name,)).fetchone())
            if not allowed:
                me = c.execute("SELECT role,is_admin FROM users WHERE id=?", (uid,)).fetchone()
                if me and (me["is_admin"] or me["role"] == "agronomist"):
                    allowed = bool(c.execute("SELECT 1 AS ok FROM diagnostic_tickets WHERE image=? LIMIT 1", (name,)).fetchone())
                    if not allowed and me["is_admin"]:
                        allowed = bool(c.execute("SELECT 1 AS ok FROM scans WHERE image=? LIMIT 1", (name,)).fetchone()
                                       or c.execute("SELECT 1 AS ok FROM community_posts WHERE image=? LIMIT 1", (name,)).fetchone())
        finally:
            c.close()
    if not allowed:
        abort(404)
    resp = send_from_directory(UPLOAD_FOLDER, name)
    resp.headers["Cache-Control"] = "private, no-store"
    return resp

@app.route("/",methods=["GET","POST"])
def home():
    if "user_id" not in session:   # public, indexable landing page for visitors; the scanner itself needs an account
        if request.method=="POST": return redirect(url_for("login", next="/"))
        return render_template("landing.html")
    lang=current_lang(); texts=translate_texts(lang, network=False)
    result=None; info=None; image_url=None; error=None
    if request.method=="POST":
        f=request.files.get("image")
        if not f or not f.filename:
            error=texts["unknown"]
        elif not allowed_file(f.filename):
            error=translate_phrase(TEXTS["en"]["image_type_error"], lang)
        else:
            try:
                name,path=_store_upload(f, session["user_id"])
                if not name:
                    return render_template("index.html",result=None,disease_info=None,image_url=None,error=translate_phrase("The uploaded file is not a valid image.", lang))
                result=analyze_plant_image(path, lang)
                info=result.get("info")
                if result.get("message") and result.get("unknown"): error=None
                image_url=url_for("media",name=name)
                if not result.get("unknown"):
                    result["scan_id"]=save_scan_result(session["user_id"],name,result,owned_field_id(request.form.get("field_id")),scan_weather_snapshot())
                    if not result.get("low_confidence") and result.get("class") and "unavailable" not in str(result.get("class")):
                        session.update(last_class=result["class"],last_plant=result["plant"],last_confidence=round(result.get("confidence",0),2))
            except Exception:
                app.logger.exception("Plant scan failed")
                error="The AI service could not complete this image. Please try a clearer plant photo or try again."
                result={"unknown":True,"confidence":0,"plant":"Unknown","class":"Not Identified","error":"detection_failed"}
    return render_template("index.html",result=result,disease_info=info,image_url=image_url,error=error)

@app.route("/detect-camera",methods=["POST"])
def detect_camera():
    """Camera capture endpoint. Same pipeline as the upload form (analyze_plant_image)."""
    if "user_id" not in session:
        return jsonify({"ok":False,"error":"Please login first.","login_required":True}),401
    f=request.files.get("image")
    if not f or not f.filename or not allowed_file(f.filename):
        return jsonify({"ok":False,"error":"Please capture a valid JPG, PNG or WEBP plant image."}),400
    lang=session.get("lang","en")
    try:
        name,path=_store_upload(f, session["user_id"], prefix="camera_")
        if not name:
            return jsonify({"ok":False,"error":"The captured file is not a valid image."}),400
        r=analyze_plant_image(path, lang)
        payload={"ok":True,"unknown":bool(r.get("unknown")),"plant":r.get("plant"),"class":r.get("class"),"disease":r.get("disease") or r.get("class"),
                 "confidence":r.get("confidence",0),"source":r.get("source"),"message":r.get("message"),"explanation":r.get("explanation"),
                 "scientific_name":r.get("scientific_name"),"low_confidence":bool(r.get("low_confidence")),"gemini":bool(r.get("gemini")),
                 "guidance":r.get("info"),"image_url":url_for("media",name=name),
                 "health_score":r.get("health_score"),"severity":r.get("severity"),"health_label":tr_fast("Crop Health Score",lang)}
        if not r.get("unknown"):
            payload["scan_id"]=save_scan_result(session["user_id"],name,r,owned_field_id(request.form.get("field_id")),scan_weather_snapshot())
            if not r.get("low_confidence") and "unavailable" not in str(r.get("class")):
                session.update(last_class=r.get("class"),last_plant=r.get("plant"),last_confidence=round(r.get("confidence",0),2))
        return jsonify(payload)
    except Exception:
        app.logger.exception("Camera detection failed")
        return jsonify({"ok":False,"error":"Could not analyze the captured image. Please try again."}),500

def _safe_next(url):
    """Only allow same-site relative redirects (blocks //evil.com, /\\evil.com, absolute URLs)."""
    if not url or not url.startswith("/") or url.startswith("//") or "\\" in url or "://" in url or any(ord(ch) < 32 for ch in url):
        return url_for("home")
    return url

@app.route("/login",methods=["GET","POST"])
def login():
    lang=current_lang()
    next_url=_safe_next(request.values.get("next"))
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); pw=request.form.get("password","")
        c=db(); u=c.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone(); c.close()
        if u and check_password_hash(u["password"],pw):
            keep_lang=session.get("lang","en"); session.clear()  # fresh session on login (prevents session fixation)
            session.update(user_id=u["id"],user_name=u["name"],is_admin=bool(u["is_admin"] if "is_admin" in u.keys() else 0),lang=keep_lang); return redirect(next_url)
        flash(TEXTS[lang]["invalid_login"],"error")
    return render_template("auth.html",mode="login",next=next_url)

@app.route("/register",methods=["GET","POST"])
def register():
    lang=current_lang()
    next_url=_safe_next(request.values.get("next"))
    if request.method=="POST":
        name=request.form.get("name","").strip()[:100]; email=request.form.get("email","").strip().lower(); pw=request.form.get("password","")
        if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}", email) or len(email)>254 or len(pw)<8 or len(pw)>128:
            flash(translate_phrase("Please enter your name, a valid email and a password of at least 8 characters.", lang),"error")
            return render_template("auth.html",mode="register",next=next_url)
        try:
            c=db(); c.execute("INSERT INTO users(name,email,password) VALUES(?,?,?)",(name,email,generate_password_hash(pw))); c.commit()
            uid=c.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone()["id"]; c.close()
            keep_lang=session.get("lang","en"); session.clear()
            session.update(user_id=uid,user_name=name,is_admin=False,lang=keep_lang); return redirect(next_url)
        except IntegrityError: flash(TEXTS[lang]["email_exists"],"error")
    return render_template("auth.html",mode="register",next=next_url)

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("home"))

# ---------- PASSWORD RESET ----------
# Hinglish: Forgot password -> reset token/link -> new password flow.
@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    lang = current_lang()
    reset_link = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        c = db(); u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if u:
            token = secrets.token_urlsafe(32)
            expiry = datetime.utcnow() + timedelta(hours=1)
            c.execute("UPDATE users SET reset_token=?, reset_token_expiry=? WHERE id=?", (token, expiry, u["id"]))
            c.commit()
            reset_url = url_for("reset_password", token=token, _external=True)
            if SMTP_ENABLED:
                # If SMTP is configured but sending fails (bad App Password,
                # blocked login, network issue, etc.), still expose the token
                # link in local development instead of leaving the farmer
                # with a dead "link sent" message.
                email_sent = send_reset_email(email, reset_url, lang)
                if not email_sent:
                    reset_link = reset_url
                    flash("Email delivery failed, so the secure reset link is shown below for local testing.", "error")
            else:
                # No email service configured: show the link directly so the
                # feature remains usable during local development.
                reset_link = reset_url
        c.close()
        # Always show the same confirmation, whether or not the email exists,
        # so the form can't be used to check which emails are registered.
        flash(tx("reset_link_sent", lang), "success")
    return render_template("forgot_password.html", reset_link=reset_link)

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    lang = current_lang()
    c = db()
    u = c.execute("SELECT * FROM users WHERE reset_token=?", (token,)).fetchone()
    valid = False
    if u:
        expiry_value = u["reset_token_expiry"]
        try:
            if isinstance(expiry_value, str):
                expiry_value = datetime.fromisoformat(expiry_value.replace("Z", "+00:00").replace("T", " ").split("+")[0])
            valid = bool(expiry_value and expiry_value > datetime.utcnow())
        except (TypeError, ValueError):
            valid = False
    if not valid:
        c.close()
        flash(tx("reset_invalid", lang), "error")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        if len(pw) < 6 or pw != pw2:
            c.close()
            flash(tx("password_mismatch", lang), "error")
            return render_template("reset_password.html", token=token)
        c.execute("UPDATE users SET password=?, reset_token=NULL, reset_token_expiry=NULL WHERE id=?",
                   (generate_password_hash(pw), u["id"]))
        c.commit(); c.close()
        flash(tx("reset_success", lang), "success")
        return redirect(url_for("login"))
    c.close()
    return render_template("reset_password.html", token=token)

@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    lang = current_lang()
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("password", "")
        new_pw2 = request.form.get("password2", "")
        c = db(); u = c.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not u or not check_password_hash(u["password"], current_pw):
            c.close()
            flash(tx("invalid_login", lang), "error")
        elif len(new_pw) < 6 or new_pw != new_pw2:
            c.close()
            flash(tx("password_mismatch", lang), "error")
        else:
            c.execute("UPDATE users SET password=? WHERE id=?", (generate_password_hash(new_pw), u["id"]))
            c.commit(); c.close()
            flash(tx("password_changed", lang), "success")
            return redirect(url_for("dashboard"))
    return render_template("change_password.html")


# ---------- SUPPORTED PLANTS PAGE ----------
# Hinglish: Farmer ko available plant/model coverage dikhata hai.
@app.route("/supported-plants")
def supported_plants():
    """Show the crops and disease classes supported by the local TFLite model."""
    current_lang()
    crop_map = {}
    for raw in labels:
        parts = raw.split("___", 1)
        if len(parts) != 2:
            continue
        crop, disease = parts
        crop = crop.replace("_", " ").replace("(maize)", "(Maize)")
        crop = re.sub(r"\s*,\s*", ", ", crop)
        crop = re.sub(r"\s+", " ", crop).strip()
        disease = disease.replace("_", " ").strip()
        disease = re.sub(r"\s+", " ", disease)
        crop_map.setdefault(crop, []).append(disease)
    return render_template(
        "supported_plants.html",
        crop_map=crop_map,
        total_labels=len(labels),
    )

# ---------- FARMER DASHBOARD / ADMIN ----------
# Hinglish: Scan history, crop activity aur admin controls.
@app.route("/dashboard")
@login_required
def dashboard():
    c=db(); scans=c.execute("SELECT * FROM scans WHERE user_id=? ORDER BY id DESC LIMIT 50",(session["user_id"],)).fetchall(); c.close()
    stats=build_analytics(session["user_id"])
    return render_template("dashboard.html",scans=scans,stats=stats)



def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        c = db()
        u = c.execute("SELECT is_admin FROM users WHERE id=?", (session["user_id"],)).fetchone()
        c.close()
        if not u or not bool(u["is_admin"]):
            flash(translate_phrase(TEXTS["en"]["admin_required"], session.get("lang", "en")), "error")
            return redirect(url_for("home"))
        session["is_admin"] = True
        return fn(*a, **kw)
    return wrapper

def role_required(*roles):
    """Gate a route to users whose `users.role` is one of `roles` (admins always pass)."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if "user_id" not in session:
                return redirect(url_for("login", next=request.path))
            c = db()
            u = c.execute("SELECT role,is_admin FROM users WHERE id=?", (session["user_id"],)).fetchone()
            c.close()
            if not u or (u["role"] not in roles and not bool(u["is_admin"])):
                flash(translate_phrase(TEXTS["en"]["no_access"], session.get("lang", "en")), "error")
                return redirect(url_for("home"))
            return fn(*a, **kw)
        return wrapper
    return deco

@app.route("/admin")
@admin_required
def admin_panel():
    c = db()
    users = c.execute("SELECT id,name,email,is_admin,created_at FROM users ORDER BY id DESC").fetchall()
    scans = c.execute("SELECT scans.*, users.name AS user_name, users.email AS user_email FROM scans LEFT JOIN users ON users.id=scans.user_id ORDER BY scans.id DESC LIMIT 100").fetchall()
    contacts = c.execute("SELECT * FROM contact_messages ORDER BY id DESC LIMIT 30").fetchall()
    feedback = c.execute("SELECT * FROM feedback ORDER BY id DESC LIMIT 30").fetchall()
    suggestions = c.execute("SELECT * FROM suggestions ORDER BY id DESC LIMIT 30").fetchall()
    # Plant Hospital queue: admins can open a ticket directly from the admin panel.
    tickets = c.execute("""SELECT t.*, u.name AS user_name, u.email AS user_email,
                                  a.name AS expert_name
                           FROM diagnostic_tickets t
                           LEFT JOIN users u ON u.id=t.user_id
                           LEFT JOIN users a ON a.id=t.assigned_expert_id
                           ORDER BY (t.status='resolved') ASC, t.id DESC
                           LIMIT 100""").fetchall()
    stats = {
        "users": c.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()["cnt"],
        "scans": c.execute("SELECT COUNT(*) AS cnt FROM scans").fetchone()["cnt"],
        "feedback": c.execute("SELECT COUNT(*) AS cnt FROM feedback").fetchone()["cnt"],
        "contacts": c.execute("SELECT COUNT(*) AS cnt FROM contact_messages").fetchone()["cnt"],
        "suggestions": c.execute("SELECT COUNT(*) AS cnt FROM suggestions").fetchone()["cnt"],
        "tickets": c.execute("SELECT COUNT(*) AS cnt FROM diagnostic_tickets").fetchone()["cnt"],
        "open_tickets": c.execute("SELECT COUNT(*) AS cnt FROM diagnostic_tickets WHERE status!='resolved'").fetchone()["cnt"],
    }
    c.close()
    return render_template("admin.html", users=users, scans=scans, contacts=contacts,
                           feedback=feedback, suggestions=suggestions, tickets=tickets, stats=stats)

@app.route("/admin/delete-scan/<int:scan_id>", methods=["POST"])
@admin_required
def admin_delete_scan(scan_id):
    lang = current_lang()
    c = db()
    row = c.execute("SELECT image FROM scans WHERE id=?", (scan_id,)).fetchone()
    c.execute("DELETE FROM scans WHERE id=?", (scan_id,))
    c.commit(); c.close()
    if row and row["image"]:
        safe_name = os.path.basename(row["image"])
        file_path = os.path.join(UPLOAD_FOLDER, safe_name)
        if os.path.isfile(file_path):
            try: os.remove(file_path)
            except OSError: pass
    flash(translate_phrase(TEXTS["en"]["scan_deleted"], lang), "success")
    return redirect(url_for("admin_panel") + "#scans")

@app.route("/admin/delete-scans", methods=["POST"])
@admin_required
def admin_delete_scans():
    lang = current_lang()
    c = db()
    rows = c.execute("SELECT image FROM scans").fetchall()
    c.execute("DELETE FROM scans")
    c.commit(); c.close()
    for row in rows:
        if row["image"]:
            safe_name = os.path.basename(row["image"])
            file_path = os.path.join(UPLOAD_FOLDER, safe_name)
            if os.path.isfile(file_path):
                try: os.remove(file_path)
                except OSError: pass
    flash(translate_phrase(TEXTS["en"]["all_scans_deleted"], lang), "success")
    return redirect(url_for("admin_panel") + "#scans")

@app.route("/about")
def about(): current_lang(); return render_template("about.html")

@app.route("/feedback",methods=["GET","POST"])
def feedback():
    lang=current_lang()
    if request.method=="POST":
        name=request.form.get("name","Farmer").strip(); msg=request.form.get("message","").strip()
        try: rating=int(request.form.get("rating",5))
        except (TypeError,ValueError): rating=5
        rating=max(1,min(5,rating))
        if msg:
            c=db(); c.execute("INSERT INTO feedback(user_id,name,message,rating) VALUES(?,?,?,?)",(session.get("user_id"),name,msg,rating)); c.commit(); c.close()
            flash(TEXTS[lang]["feedback_saved"],"success"); return redirect(url_for("feedback"))
    c=db()
    recent=c.execute("SELECT * FROM feedback ORDER BY id DESC LIMIT 10").fetchall()
    stats=c.execute("SELECT AVG(rating) AS avg_rating, COUNT(*) AS cnt FROM feedback").fetchone()
    c.close()
    return render_template("feedback.html",recent=recent,avg_rating=stats["avg_rating"],feedback_count=stats["cnt"])

@app.route("/contact",methods=["GET","POST"])
def contact():
    lang=current_lang()
    if request.method=="POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip(); msg=request.form.get("message","").strip()
        if name and msg:
            c=db(); c.execute("INSERT INTO contact_messages(name,email,message) VALUES(?,?,?)",(name,email,msg)); c.commit(); c.close()
            flash(TEXTS[lang]["contact_saved"],"success"); return redirect(url_for("contact"))
    return render_template("contact.html")

@app.route("/suggestions",methods=["GET","POST"])
def suggestions():
    lang=current_lang()
    if request.method=="POST":
        name=request.form.get("name","Farmer").strip(); msg=request.form.get("message","").strip()
        if msg:
            c=db(); c.execute("INSERT INTO suggestions(user_id,name,message) VALUES(?,?,?)",(session.get("user_id"),name,msg)); c.commit(); c.close()
            flash(TEXTS[lang]["suggestion_saved"],"success"); return redirect(url_for("suggestions"))
    c=db(); items=c.execute("SELECT * FROM suggestions ORDER BY id DESC LIMIT 10").fetchall(); c.close()
    return render_template("suggestions.html",items=items)



def offline_farmer_answer(message, lang):
    """Small offline safety-net so the assistant still works when Gemini is unavailable/quota-limited."""
    q = message.lower()
    packs = {
        "summer": {
            "en": "🌱 Summer care: water early morning or evening, mulch around the root zone, provide shade for sensitive plants, and check leaves regularly for heat stress and pests.",
            "hi": "🌱 गर्मियों में: सुबह जल्दी या शाम को पानी दें, जड़ों के आसपास मल्च रखें, संवेदनशील पौधों को छाया दें और पत्तियों में गर्मी/कीट के लक्षण देखें।",
            "mr": "🌱 उन्हाळ्यात: सकाळी लवकर किंवा संध्याकाळी पाणी द्या, मुळांभोवती आच्छादन ठेवा, संवेदनशील झाडांना सावली द्या आणि पाने/किडींची नियमित तपासणी करा।"
        },
        "water": {
            "en": "💧 Irrigation: water according to soil moisture and crop stage rather than a fixed schedule. Avoid waterlogging and wetting leaves unnecessarily.",
            "hi": "💧 सिंचाई: तय समय के बजाय मिट्टी की नमी और फसल की अवस्था देखकर पानी दें। जलभराव और पत्तियों को अनावश्यक रूप से गीला करने से बचें।",
            "mr": "💧 सिंचन: ठराविक वेळेपेक्षा मातीतील ओलावा आणि पिकाची अवस्था पाहून पाणी द्या. पाणी साचणे आणि पानांवर अनावश्यक ओलावा टाळा."
        },
        "disease": {
            "en": "🛡️ Disease prevention: remove severely infected leaves, keep the field clean, improve air circulation, avoid prolonged leaf wetness, and monitor plants regularly. For pesticides, follow the locally approved product label.",
            "hi": "🛡️ रोग से बचाव: बहुत संक्रमित पत्तियाँ हटाएं, खेत साफ रखें, हवा का संचार बढ़ाएं, पत्तियों पर लंबे समय तक नमी से बचें और नियमित निगरानी करें। कीटनाशक के लिए स्थानीय रूप से स्वीकृत उत्पाद का लेबल मानें।",
            "mr": "🛡️ रोग प्रतिबंध: जास्त संक्रमित पाने काढा, शेत स्वच्छ ठेवा, हवेचे वहन वाढवा, पानांवर दीर्घकाळ ओलावा टाळा आणि नियमित पाहणी करा. कीटकनाशकासाठी स्थानिक मान्य उत्पादनाच्या लेबलवरील सूचना पाळा."
        },
        "fertilizer": {
            "en": "🌾 Fertilizer: use a soil-test or crop-stage-based recommendation where possible. Avoid excess fertilizer, especially when plants are heat- or water-stressed.",
            "hi": "🌾 खाद: संभव हो तो मिट्टी परीक्षण या फसल की अवस्था के अनुसार खाद दें। अधिक खाद न दें, खासकर जब पौधे गर्मी या पानी की कमी से तनाव में हों।",
            "mr": "🌾 खत: शक्य असल्यास माती परीक्षण किंवा पिकाच्या अवस्थेनुसार खत द्या. विशेषतः उष्णता किंवा पाण्याच्या ताणात जास्त खत देऊ नका."
        }
    }
    keys = []
    if any(x in q for x in ("summer","heat","गर्मी","गर्म","उन्हाळा","उन्हाळ्यात")): keys.append("summer")
    if any(x in q for x in ("water","irrigat","पानी","सिंच","पाणी","सिंचन")): keys.append("water")
    if any(x in q for x in ("disease","protect","prevention","रोग","बीमारी","बचाव","रोकथाम","रोगां")): keys.append("disease")
    if any(x in q for x in ("fertilizer","fertiliser","खाद","उर्वरक","खत")): keys.append("fertilizer")
    no_match_text = {
        "en": "🌱 RBAgriScan offline assistant: I can help with basic plant disease prevention, irrigation, summer care, field hygiene and fertilizer guidance. Gemini is currently unavailable, so please try again later for a full AI answer.",
        "hi": "🌱 स्मार्ट क्रॉप एआई ऑफ़लाइन सहायक: मैं बुनियादी पौधों की बीमारी की रोकथाम, सिंचाई, गर्मी की देखभाल, क्षेत्र की स्वच्छता और उर्वरक मार्गदर्शन में मदद कर सकता हूं। जेमिनी फिलहाल अनुपलब्ध है, इसलिए पूर्ण एआई उत्तर के लिए कृपया बाद में पुनः प्रयास करें।",
        "mr": "🌱 स्मार्ट क्रॉप एआय ऑफलाइन सहाय्यक: मी मूलभूत वनस्पती रोग प्रतिबंध, सिंचन, उन्हाळी काळजी, शेत स्वच्छता आणि खत मार्गदर्शनात मदत करू शकतो. जेमिनी सध्या उपलब्ध नाही, त्यामुळे संपूर्ण एआय उत्तरासाठी कृपया नंतर पुन्हा प्रयत्न करा."
    }
    # Built-in languages (en/hi/mr) already have hand-written translations for
    # every pack above — use those directly (instant, no network call) instead
    # of always building the English text and translating it live, which was
    # the bug making replies look "stuck" in one language.
    if lang in ("en","hi","mr"):
        if not keys:
            return no_match_text[lang]
        return "\n\n".join(packs[k].get(lang, packs[k]["en"]) for k in dict.fromkeys(keys))
    # Any other language: build the English answer, then translate on demand.
    answer = no_match_text["en"] if not keys else "\n\n".join(packs[k]["en"] for k in dict.fromkeys(keys))
    translated = _translate(answer, lang)
    return translated if translated else answer


# SOIL PHOTO AI
# Hinglish: Soil/farm-ground image ko Gemini Vision se visual-screen kiya jata hai. Exact lab pH/N/P/K claim nahi karna hai.
def gemini_soil_analysis(path, lang):
    """Vision-based soil/crop-ground screening. This estimates visible conditions only."""
    language_name = LANG_MAP.get(lang, "English")
    prompt = f"""
You are RBAgriScan Soil & Irrigation Vision Assistant.
Analyze the attached soil/farm-ground image. Reply ONLY with valid JSON using exactly:
soil_condition, moisture_estimate, texture_visual, drainage_risk, irrigation_advice,
organic_matter_visual, visible_problems, crop_suitability, confidence, explanation.
Use {language_name} for all text values.
Rules:
- This is visual screening, NOT laboratory soil testing.
- Never invent exact pH, N/P/K ppm, EC, moisture percentage, or temperature from an image.
- moisture_estimate must be one of: "Likely dry", "Moderate", "Likely moist", "Cannot tell".
- confidence is 0-100 and must reflect image quality and certainty.
- If the image is not soil/farm ground, set soil_condition to "Unknown", moisture_estimate to "Cannot tell", confidence below 30.
- Base observations only on visible evidence such as cracking, wet appearance, standing water, texture, mulch, stones, compaction or erosion.
- Give practical, safe irrigation/soil-care advice. For fertilizer, recommend soil testing before exact nutrient application.
"""
    text = _gemini_text(prompt, path)
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    try:
        data = json.loads(cleaned)
    except Exception:
        m = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not m:
            raise RuntimeError("Gemini soil response was not valid JSON.")
        data = json.loads(m.group(0))
    data.setdefault("soil_condition", "Unknown")
    data.setdefault("moisture_estimate", "Cannot tell")
    data.setdefault("texture_visual", "")
    data.setdefault("drainage_risk", "")
    data.setdefault("irrigation_advice", "")
    data.setdefault("organic_matter_visual", "")
    data.setdefault("visible_problems", [])
    data.setdefault("crop_suitability", "")
    data.setdefault("confidence", 0)
    data.setdefault("explanation", "")
    if not isinstance(data["visible_problems"], list):
        data["visible_problems"] = [str(data["visible_problems"])]
    try:
        data["confidence"] = max(0, min(100, float(data["confidence"] or 0)))
    except Exception:
        data["confidence"] = 0
    return data

# SOIL ANALYSIS API
# Hinglish: Browser se soil photo receive karta hai, Gemini vision analysis chalata hai aur JSON report frontend ko return karta hai.
@app.route("/api/soil-analysis", methods=["POST"])
@login_required
def soil_analysis():
    lang = current_lang()
    f = request.files.get("image")
    if not f or not f.filename or not allowed_file(f.filename):
        return jsonify({"ok": False, "error": "Please upload a JPG, PNG or WEBP soil image."}), 400
    if not GEMINI_ENABLED:
        return jsonify({"ok": False, "error": "AI soil analysis needs GEMINI_API_KEY configured."}), 503
    unique = f"soil_{secrets.token_hex(10)}.jpg"  # never reuse the client's filename
    path = os.path.join(UPLOAD_FOLDER, unique)
    f.save(path)
    if not validate_uploaded_image(path):
        try: os.remove(path)
        except OSError: pass
        return jsonify({"ok": False, "error": "The uploaded file is not a valid image."}), 400
    try:
        result = gemini_soil_analysis(path, lang)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        # Print the FULL traceback (not just str(e)) so the real cause shows
        # up in the terminal / Render logs, then send the farmer a specific,
        # actionable reason instead of one generic line for every failure.
        print("Gemini soil analysis error:")
        traceback.print_exc()
        reason, msg = _classify_gemini_error(e)
        return jsonify({"ok": False, "error": msg, "reason": reason}), 502
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

def _guidance_lines(info, lang):
    ui = translate_texts(lang, network=False)
    lines = []
    for key, label in (("home", "home_remedies"), ("natural", "natural"), ("field", "field"), ("chemical", "chemical"), ("prevention", "prevention")):
        items = (info or {}).get(key) or []
        if items: lines += ["", ui[label] + ":"] + [f"• {x}" for x in items]
    return lines

def _chat_with_photo(message, f, lang):
    """Question + photo. The photo goes through plant validation FIRST; non-plants never reach Gemini. The file is not kept."""
    if not allowed_file(f.filename):
        return jsonify({"ok": False, "error": "Please attach a JPG, PNG or WEBP image."}), 400
    name, path = _store_upload(f, session["user_id"], prefix="chat_")
    if not name:
        return jsonify({"ok": False, "error": "The attached file is not a valid image."}), 400
    try:
        gate = validate_plant_image(path, lang)
        if not gate.get("ok"):
            msg = ("Plant validation is not available right now. Please try again in a moment." if gate.get("reason") == "validator_unavailable"
                   else PLANT_NOT_IDENTIFIED_MSG)
            return jsonify({"ok": True, "answer": tr_fast(msg, lang), "source": "plant_validation", "plant_identified": False})
        plant = gate.get("supported_crop") or gate.get("plant") or "plant"
        question = message or "What is wrong with this plant and what should I do?"
        if GEMINI_ENABLED:
            try:
                prompt = (f"You are the RBAgriScan farmer assistant. Reply in {LANG_MAP.get(lang, 'English')} in simple words for a farmer. "
                          f"The attached photo was validated as: {plant}. Answer the farmer's question about this photo. Do not claim certainty from a photo alone; "
                          "give practical, safe advice, mention a local agriculture expert for serious problems, and use only locally approved products as labelled.\n"
                          f"Question: {question}")
                return jsonify({"ok": True, "answer": _gemini_text(prompt, image_path=path), "plant_identified": True, "detected_plant": plant})
            except Exception:
                app.logger.exception("Gemini photo chat failed; using local analysis")
        r = analyze_plant_image(path, lang)
        lines = [f"{tr_fast('Plant', lang)}: {r.get('plant', plant)}"]
        if r.get("low_confidence"): lines.append(tr_fast(LOW_CONFIDENCE_MSG, lang))
        elif r.get("class"): lines.append(f"{translate_texts(lang, network=False)['disease']}: {pretty_disease(r['class'])} ({r.get('confidence', 0):.0f}%)")
        lines += _guidance_lines(r.get("info"), lang)
        return jsonify({"ok": True, "answer": "\n".join(lines), "source": "local_guidance", "plant_identified": True, "detected_plant": plant})
    finally:
        try: os.remove(path)
        except OSError: pass

@app.route("/api/chat",methods=["POST"])
def api_chat():
    if "user_id" not in session:
        return jsonify({"ok":False,"error":"Please log in to use the assistant.","login_required":True}),401
    if request.mimetype == "multipart/form-data":   # question + photo
        data={"message":request.form.get("message",""),"history":[],"language":request.form.get("language","")}
        try:
            h=json.loads(request.form.get("history") or "[]"); data["history"]=h if isinstance(h,list) else []
        except ValueError:
            pass
    else:
        data=request.get_json(silent=True) or {}
    message=str(data.get("message","")).strip()[:2000]
    if isinstance(data.get("history"), list): data["history"]=data["history"][-12:]
    if not message:
        return jsonify({"ok":False,"error":"Message is required."}),400
    try:
        # Behave like Gemini/ChatGPT: reply in whatever language the farmer
        # actually typed the question in, not just the language picked in the
        # site's dropdown. detect_message_language() only overrides the site
        # language when it is confident (long-enough, unambiguous text) - for
        # short/romanized messages it safely falls back to the site language
        # instead of guessing wrong.
        site_lang=session.get("lang","en")
        req_lang=str(data.get("language") or "").strip()
        lang=req_lang if req_lang in LANG_MAP else site_lang   # language chosen in the Voice Assistant wins over the page language
        low=message.lower()
        photo=request.files.get("image")
        if photo and photo.filename:
            return _chat_with_photo(message, photo, lang)
        precaution_words=("precaution","precautions","treatment","remedy","remedies","prevent","prevention","care","क्या सावधानी","उपचार","काळजी","उपाय")
        last_cls=session.get("last_class")
        if last_cls and any(w in low for w in precaution_words):
            info=disease_info(last_cls,lang)
            ui = translate_texts(lang)
            lines=[info.get("name",last_cls), "", ui["home_remedies"] + ":"] + [f"• {x}" for x in info.get("home",[])]
            lines += ["", ui["natural"] + ":"] + [f"• {x}" for x in info.get("natural",[])]
            lines += ["", ui["field"] + ":"] + [f"• {x}" for x in info.get("field",[])]
            lines += ["", ui["chemical"] + ":"] + [f"• {x}" for x in info.get("chemical",[])]
            lines += ["", ui["prevention"] + ":"] + [f"• {x}" for x in info.get("prevention",[])]
            return jsonify({"ok":True,"answer":"\n".join(lines),"source":"local_guidance"})
        if not GEMINI_ENABLED:
            return jsonify({"ok":True,"answer":offline_farmer_answer(message,lang),"source":"offline_assistant","detected_lang":lang})
        answer=gemini_chat(message,lang,data.get("history") or [])
        return jsonify({"ok":True,"answer":answer,"detected_lang":lang,"context":{"plant":session.get("last_plant"),"disease":session.get("last_class"),"confidence":session.get("last_confidence")}})
    except Exception as e:
        # Keep the existing assistant behavior, but show a friendly message
        # when Gemini free-tier quota is exhausted (HTTP 429).
        err_text = str(e)
        print("Gemini chat error (falling back to offline assistant):", err_text)
        # Never break the farmer UI on quota/network/server failures.
        return jsonify({"ok":True,"answer":offline_farmer_answer(message,lang),"source":"offline_assistant","fallback_reason":"ai_unavailable"})

@app.route("/api/plantnet-test")
@admin_required
def plantnet_test():
    """Safe server-side diagnostic: reports configuration and quota without exposing the API key."""
    if not PLANTNET_ENABLED:
        return jsonify({"ok":False,"configured":False,"error":"PLANTNET_API_KEY is missing from .env"}),400
    try:
        r=requests.get("https://my-api.plantnet.org/v2/quota/daily",params={"api-key":PLANTNET_API_KEY},timeout=15)
        if r.status_code in (401,403):
            return jsonify({"ok":False,"configured":True,"error":"PlantNet API key is invalid or not authorized."}),401
        r.raise_for_status()
        return jsonify({"ok":True,"configured":True,"quota":r.json()})
    except Exception as e:
        return jsonify({"ok":False,"configured":True,"error":"Could not reach PlantNet quota service."}),502

@app.route("/api/health")
def api_health():
    # Public liveness probe: no configuration details are exposed here.
    return jsonify({"ok":True,"service":"RBAgriScan"})

def allowed_file(filename):
    return bool(filename and "." in filename and filename.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS)

def validate_uploaded_image(path):
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            if im.width > 6000 or im.height > 6000:
                return False
            im.convert("RGB").load()
        return True
    except Exception:
        return False

# ===========================================================================
# 1) CROP TIMELINE & STAGE TRACKER
# ===========================================================================
def _current_temp_for_city(city):
    """Best-effort current temperature (°C) for a city, used to speed up/slow
    down the stage-duration estimate. Returns None on any failure (offline,
    unknown city, etc.) so callers just fall back to the baseline duration."""
    if not city:
        return None
    try:
        import urllib.request, urllib.parse
        q = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": city, "count": 1, "format": "json"})
        geo = json.loads(urllib.request.urlopen(q, timeout=6).read().decode())
        if not geo.get("results"):
            return None
        r = geo["results"][0]
        q2 = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({"latitude": r["latitude"], "longitude": r["longitude"], "current": "temperature_2m", "timezone": "auto"})
        data = json.loads(urllib.request.urlopen(q2, timeout=6).read().decode())
        return data.get("current", {}).get("temperature_2m")
    except Exception:
        return None

def _seed_reminders(conn, crop_log_id, plan):
    """Watering every 3 days through the whole plan, plus a fertilizing /
    stage-check reminder at the start of each stage."""
    end_date = plan[-1]["end"]
    d = plan[0]["start"]
    while d <= end_date:
        conn.execute("INSERT INTO care_reminders(crop_log_id,kind,due_date) VALUES(?,?,?)", (crop_log_id, "watering", d))
        d += timedelta(days=3)
    for stage in plan:
        conn.execute("INSERT INTO care_reminders(crop_log_id,kind,due_date) VALUES(?,?,?)", (crop_log_id, f"fertilize_{stage['stage'].lower()}", stage["start"]))
        conn.execute("INSERT INTO care_reminders(crop_log_id,kind,due_date) VALUES(?,?,?)", (crop_log_id, f"check_{stage['stage'].lower()}", stage["end"] - timedelta(days=1)))

@app.route("/crops")
@login_required
def crop_list():
    c = db()
    logs = c.execute("SELECT * FROM crop_logs WHERE user_id=? ORDER BY status='active' DESC, id DESC", (session["user_id"],)).fetchall()
    today = date.today()
    for lg in logs:
        temp = _current_temp_for_city(lg.get("city")) if lg["status"] == "active" else None
        plan = estimate_crop_plan(lg["crop_key"], lg["planted_on"], temp)
        lg["plan"] = plan
        cur = plan[min(lg["current_stage_order"], len(plan)) - 1]
        lg["percent_in_stage"] = min(100, max(0, int(100 * (today - cur["start"]).days / max(1, (cur["end"] - cur["start"]).days))))
        lg["current_stage_name"] = cur["stage"]
        due = c.execute("SELECT COUNT(*) n FROM care_reminders WHERE crop_log_id=? AND done=0 AND due_date<=?", (lg["id"], today)).fetchone()
        lg["due_count"] = due["n"] if due else 0
    c.close()
    return render_template("crop_timeline_list.html", logs=logs, crop_options=sorted(CROP_STAGE_TEMPLATES.keys()))

@app.route("/crops/new", methods=["POST"])
@login_required
def crop_new():
    lang = current_lang()
    crop_key = request.form.get("crop_key", "").strip()
    custom_crop = request.form.get("custom_crop", "").strip()
    nickname = request.form.get("nickname", "").strip()
    city = request.form.get("city", "").strip()
    planted_on_raw = request.form.get("planted_on", "")
    if crop_key == "__custom__":
        # Farmers can track any crop even when its exact agronomy template is
        # not in the built-in list. A safe default 5-stage plan is used.
        custom_crop = re.sub(r"\s+", " ", custom_crop)[:80]
        if not custom_crop:
            flash(translate_phrase(TEXTS["en"]["custom_crop_required"], lang), "error"); return redirect(url_for("crop_list"))
        crop_key = "custom:" + custom_crop
    elif crop_key not in CROP_STAGE_TEMPLATES:
        flash(translate_phrase(TEXTS["en"]["crop_supported_error"], lang), "error"); return redirect(url_for("crop_list"))
    try:
        planted_on = datetime.strptime(planted_on_raw, "%Y-%m-%d").date()
    except Exception:
        planted_on = date.today()
    temp = _current_temp_for_city(city)
    plan = estimate_crop_plan(crop_key, planted_on, temp)
    c = db()
    c.execute("INSERT INTO crop_logs(user_id,crop_key,nickname,planted_on,city,current_stage_order,current_stage_started_on) VALUES(?,?,?,?,?,1,?)",
              (session["user_id"], crop_key, nickname or crop_key, planted_on, city, planted_on))
    log_id = c.lastrowid
    c.execute("INSERT INTO crop_stage_events(crop_log_id,stage_order,stage_name,note) VALUES(?,?,?,?)", (log_id, 1, plan[0]["stage"], "Planted"))
    _seed_reminders(c, log_id, plan)
    c.commit(); c.close()
    flash(translate_phrase(TEXTS["en"]["crop_added"], lang), "success")
    return redirect(url_for("crop_detail", log_id=log_id))

@app.route("/crops/<int:log_id>")
@login_required
def crop_detail(log_id):
    lang = current_lang()
    c = db()
    lg = c.execute("SELECT * FROM crop_logs WHERE id=? AND user_id=?", (log_id, session["user_id"])).fetchone()
    if not lg:
        c.close(); flash(translate_phrase(TEXTS["en"]["crop_not_found"], lang), "error"); return redirect(url_for("crop_list"))
    temp = _current_temp_for_city(lg.get("city"))
    plan = estimate_crop_plan(lg["crop_key"], lg["planted_on"], temp)
    today = date.today()
    events = c.execute("SELECT * FROM crop_stage_events WHERE crop_log_id=? ORDER BY logged_at DESC", (log_id,)).fetchall()
    reminders = c.execute("SELECT * FROM care_reminders WHERE crop_log_id=? AND done=0 AND due_date<=? ORDER BY due_date", (log_id, today + timedelta(days=2))).fetchall()
    c.close()
    return render_template("crop_timeline_detail.html", lg=lg, plan=plan, events=events, reminders=reminders, today=today,
                            next_stage=plan[lg["current_stage_order"]] if lg["current_stage_order"] < len(plan) else None)

@app.route("/crops/<int:log_id>/advance", methods=["POST"])
@login_required
def crop_advance(log_id):
    lang = current_lang()
    c = db()
    lg = c.execute("SELECT * FROM crop_logs WHERE id=? AND user_id=?", (log_id, session["user_id"])).fetchone()
    if not lg:
        c.close(); flash(translate_phrase(TEXTS["en"]["crop_not_found"], lang), "error"); return redirect(url_for("crop_list"))
    plan = estimate_crop_plan(lg["crop_key"], lg["planted_on"])
    next_order = lg["current_stage_order"] + 1
    if next_order > len(plan):
        c.execute("UPDATE crop_logs SET status='harvested' WHERE id=?", (log_id,))
        flash("🎉 " + translate_phrase(TEXTS["en"]["harvested"], lang), "success")
    else:
        stage = plan[next_order - 1]
        c.execute("UPDATE crop_logs SET current_stage_order=?,current_stage_started_on=? WHERE id=?", (next_order, date.today(), log_id))
        c.execute("INSERT INTO crop_stage_events(crop_log_id,stage_order,stage_name,note) VALUES(?,?,?,?)", (log_id, next_order, stage["stage"], request.form.get("note", "")))
        flash("🌱 " + translate_phrase(TEXTS["en"]["stage_moved"].format(stage=stage["stage"]), lang), "success")
    c.commit(); c.close()
    return redirect(url_for("crop_detail", log_id=log_id))

@app.route("/crops/reminder/<int:rid>/done", methods=["POST"])
@login_required
def reminder_done(rid):
    c = db()
    r = c.execute("""SELECT cr.id,cr.crop_log_id FROM care_reminders cr JOIN crop_logs cl ON cl.id=cr.crop_log_id
                      WHERE cr.id=? AND cl.user_id=?""", (rid, session["user_id"])).fetchone()
    if r:
        c.execute("UPDATE care_reminders SET done=1 WHERE id=?", (rid,)); c.commit()
        log_id = r["crop_log_id"]
    else:
        log_id = None
    c.close()
    return redirect(url_for("crop_detail", log_id=log_id) if log_id else url_for("crop_list"))

# ===========================================================================
# 2) IOT SENSOR & SMART IRRIGATION DASHBOARD
# ===========================================================================
# IOT ALERT RULES
# Hinglish: Physical sensor readings me low soil moisture/high-low temperature ke alerts yahan define hote hain.
SENSOR_THRESHOLDS = {"soil_moisture_low": 25.0, "temp_high": 40.0, "temp_low": 2.0}

def _check_sensor_thresholds(conn, device_id, reading):
    alerts = []
    sm = reading.get("soil_moisture")
    if sm is not None and sm < SENSOR_THRESHOLDS["soil_moisture_low"]:
        alerts.append(("dry_soil", f"Soil moisture is low ({sm:.0f}%). Consider irrigating soon."))
    t = reading.get("temperature")
    if t is not None and t >= SENSOR_THRESHOLDS["temp_high"]:
        alerts.append(("heat", f"Extreme heat detected ({t:.1f}°C). Watch for heat stress."))
    if t is not None and t <= SENSOR_THRESHOLDS["temp_low"]:
        alerts.append(("cold", f"Near-freezing temperature detected ({t:.1f}°C). Frost risk."))
    for kind, msg in alerts:
        conn.execute("INSERT INTO sensor_alerts(device_id,kind,message) VALUES(?,?,?)", (device_id, kind, msg))
    return alerts

# IOT DASHBOARD ROUTE
# Hinglish: Farmer ke registered sensor devices aur crop links ko dashboard template me bhejta hai.
@app.route("/iot")
@login_required
def iot_dashboard():
    c = db()
    devices = c.execute("SELECT * FROM sensor_devices WHERE user_id=? ORDER BY id DESC", (session["user_id"],)).fetchall()
    crops = c.execute("SELECT id,nickname,crop_key FROM crop_logs WHERE user_id=? AND status='active'", (session["user_id"],)).fetchall()
    fields = c.execute("SELECT id,name,crop FROM fields WHERE user_id=? ORDER BY name", (session["user_id"],)).fetchall()
    c.close()
    return render_template("iot_dashboard.html", devices=devices, crops=crops, fields=fields)

@app.route("/iot/devices/new", methods=["POST"])
@login_required
def iot_device_new():
    lang = current_lang()
    name = request.form.get("name", "").strip() or "Field sensor"
    crop_log_id = request.form.get("crop_log_id") or None
    key = secrets.token_hex(16)
    c = db()
    if crop_log_id:
        try:
            owned = c.execute("SELECT id FROM crop_logs WHERE id=? AND user_id=?", (int(crop_log_id), session["user_id"])).fetchone()
        except (TypeError, ValueError):
            owned = None
        if not owned:
            c.close(); flash("Selected crop was not found in your account.", "error"); return redirect(url_for("iot_dashboard"))
        crop_log_id = owned["id"]
    field_id = None
    if request.form.get("field_id"):
        c.close(); field_id = owned_field_id(request.form.get("field_id"))
        if not field_id:
            flash("Selected field was not found in your account.", "error"); return redirect(url_for("iot_dashboard"))
        c = db()
    c.execute("INSERT INTO sensor_devices(user_id,device_key,name,crop_log_id,field_id) VALUES(?,?,?,?,?)", (session["user_id"], key, name[:255], crop_log_id, field_id))
    c.commit(); c.close()
    flash("🔑 " + translate_phrase(TEXTS["en"]["device_created"].format(key=key), lang), "success")
    return redirect(url_for("iot_dashboard"))

@app.route("/api/iot/ingest", methods=["POST"])
def iot_ingest():
    """Public webhook a field sensor (or a bridge script) POSTs readings to.
    Auth is a per-device key, not a login session, matching how ESP32/Wi-Fi or
    an MQTT-to-HTTP bridge would call this in production. See
    docs/iot-sensor-dashboard.md for the MQTT-broker version of this pipeline.
    """
    payload = request.get_json(silent=True) or request.form
    device_key = payload.get("device_key", "")
    if not device_key:
        return jsonify({"ok": False, "error": "device_key is required"}), 400
    c = db()
    dev = c.execute("SELECT id,user_id,name FROM sensor_devices WHERE device_key=?", (device_key,)).fetchone()
    if not dev:
        c.close(); return jsonify({"ok": False, "error": "Unknown device key"}), 401
    reading = {}
    try:
        for k in ["soil_moisture", "temperature", "humidity", "light_lux", "n_ppm", "p_ppm", "k_ppm"]:
            value = payload.get(k)
            reading[k] = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        c.close(); return jsonify({"ok":False,"error":"Sensor values must be numeric."}),400
    if reading["soil_moisture"] is not None and not 0 <= reading["soil_moisture"] <= 100:
        c.close(); return jsonify({"ok":False,"error":"soil_moisture must be between 0 and 100."}),400
    if reading["humidity"] is not None and not 0 <= reading["humidity"] <= 100:
        c.close(); return jsonify({"ok":False,"error":"humidity must be between 0 and 100."}),400
    c.execute("""INSERT INTO sensor_readings(device_id,soil_moisture,temperature,humidity,light_lux,n_ppm,p_ppm,k_ppm)
                 VALUES(?,?,?,?,?,?,?,?)""", (dev["id"], reading["soil_moisture"], reading["temperature"], reading["humidity"],
                                               reading["light_lux"], reading["n_ppm"], reading["p_ppm"], reading["k_ppm"]))
    alerts = _check_sensor_thresholds(c, dev["id"], reading)
    c.commit(); c.close()
    if any(a[0] == "dry_soil" for a in alerts):
        now = datetime.now()  # at most one notification per device per 6-hour window
        notify(dev["user_id"], "low_moisture", {"device": dev["name"], "value": round(reading["soil_moisture"])}, link="/iot",
               dedupe_key=f"dry-{dev['id']}-{now:%Y%m%d}-{now.hour // 6}")
    return jsonify({"ok": True, "stored": True, "alerts": [a[1] for a in alerts]})

@app.route("/api/iot/readings/<int:device_id>")
@login_required
def iot_readings(device_id):
    days = 30 if request.args.get("range") == "30d" else 7
    c = db()
    owns = c.execute("SELECT id FROM sensor_devices WHERE id=? AND user_id=?", (device_id, session["user_id"])).fetchone()
    if not owns:
        c.close(); return jsonify({"ok": False, "error": "Not found"}), 404
    rows = c.execute("""SELECT soil_moisture,temperature,humidity,light_lux,recorded_at FROM sensor_readings
                         WHERE device_id=? AND recorded_at >= ? ORDER BY recorded_at""",
                      (device_id, datetime.now() - timedelta(days=days))).fetchall()
    c.close()
    for r in rows: r["recorded_at"] = str(r["recorded_at"])
    return jsonify({"ok": True, "readings": rows})

@app.route("/api/iot/alerts/<int:device_id>")
@login_required
def iot_alerts(device_id):
    c = db()
    owns = c.execute("SELECT id FROM sensor_devices WHERE id=? AND user_id=?", (device_id, session["user_id"])).fetchone()
    if not owns:
        c.close(); return jsonify({"ok": False, "error": "Not found"}), 404
    rows = c.execute("SELECT kind,message,created_at,acknowledged FROM sensor_alerts WHERE device_id=? ORDER BY created_at DESC LIMIT 20", (device_id,)).fetchall()
    c.close()
    for r in rows: r["created_at"] = str(r["created_at"])
    return jsonify({"ok": True, "alerts": rows})

# ===========================================================================
# 3) PLANT HOSPITAL & EXPERT MARKETPLACE
# ===========================================================================
# PLANT HOSPITAL / EXPERT TICKETS
# Hinglish: Farmer expert help request create/list kar sakta hai; detailed ticket routes niche hain.
def _num_or_none(v, lo, hi):
    try:
        f = float(v)
        return f if lo <= f <= hi else None
    except (TypeError, ValueError):
        return None

@app.route("/experts/tickets", methods=["GET", "POST"])
@login_required
def ticket_list():
    lang = current_lang()
    c = db()
    me = c.execute("SELECT role,is_admin FROM users WHERE id=?", (session["user_id"],)).fetchone()
    is_expert = bool(me and (me["role"] == "agronomist" or me["is_admin"]))
    if request.method == "POST":
        notes = request.form.get("notes", "").strip()
        if not notes:
            c.close()
            flash(translate_phrase(TEXTS["en"]["ticket_not_found"], lang), "error")
            return redirect(url_for("ticket_list"))
        scan_id_raw = request.form.get("scan_id") or None
        verified_scan = None
        if scan_id_raw:
            try:
                verified_scan = c.execute("SELECT id,image,plant,disease,confidence FROM scans WHERE id=? AND user_id=?", (int(scan_id_raw), session["user_id"])).fetchone()
            except (TypeError, ValueError):
                verified_scan = None
            if not verified_scan:
                c.close(); flash("Selected scan was not found in your account.", "error"); return redirect(url_for("ticket_list"))
        client_ref = (request.form.get("client_ref") or secrets.token_hex(16)).strip()[:64]
        existing = c.execute("SELECT id FROM diagnostic_tickets WHERE client_ref=?", (client_ref,)).fetchone()
        if not existing:
            c.execute("""INSERT INTO diagnostic_tickets(user_id,scan_id,image,model_prediction,confidence,lat,lon,city,notes,client_ref)
                         VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      (session["user_id"], verified_scan["id"] if verified_scan else None,
                       verified_scan["image"] if verified_scan else "",
                       verified_scan["disease"] if verified_scan else "",
                       verified_scan["confidence"] if verified_scan else None, _num_or_none(request.form.get("lat"), -90, 90),
                       _num_or_none(request.form.get("lon"), -180, 180), request.form.get("city", "").strip(), notes, client_ref))
        c.commit()
        c.close()
        flash(translate_phrase(TEXTS["en"]["ticket_created"], lang), "success")
        return redirect(url_for("ticket_list"))

    if is_expert:
        tickets = c.execute("""SELECT t.*, u.name AS user_name, u.email AS user_email,
                                      a.name AS expert_name
                               FROM diagnostic_tickets t
                               JOIN users u ON u.id=t.user_id
                               LEFT JOIN users a ON a.id=t.assigned_expert_id
                               WHERE t.status!='resolved' OR t.assigned_expert_id=?
                               ORDER BY (t.status='open') DESC, t.id DESC""",
                            (session["user_id"],)).fetchall()
    else:
        tickets = c.execute("""SELECT * FROM diagnostic_tickets
                               WHERE user_id=? ORDER BY id DESC""",
                            (session["user_id"],)).fetchall()
    recent_scans = c.execute("""SELECT id,plant,disease,confidence,image
                                FROM scans WHERE user_id=? ORDER BY id DESC LIMIT 10""",
                             (session["user_id"],)).fetchall()
    c.close()
    return render_template("expert_tickets.html", tickets=tickets,
                           is_expert=is_expert, recent_scans=recent_scans)


@app.route("/experts/tickets/<int:tid>", methods=["GET", "POST"])
@login_required
def ticket_detail(tid):
    lang = current_lang()
    c = db()
    me = c.execute("SELECT role,is_admin,name FROM users WHERE id=?", (session["user_id"],)).fetchone()
    is_expert = bool(me and (me["role"] == "agronomist" or me["is_admin"]))
    ticket = c.execute("""SELECT t.*, u.name AS user_name, u.email AS user_email,
                                 a.name AS expert_name
                          FROM diagnostic_tickets t
                          JOIN users u ON u.id=t.user_id
                          LEFT JOIN users a ON a.id=t.assigned_expert_id
                          WHERE t.id=?""", (tid,)).fetchone()
    if not ticket or (not is_expert and ticket["user_id"] != session["user_id"]):
        c.close()
        flash(translate_phrase(TEXTS["en"]["ticket_not_found"], lang), "error")
        return redirect(url_for("ticket_list"))

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        message = request.form.get("message", "").strip()
        if action == "message" and message:
            message = message[:3000]
            c.execute("""INSERT INTO ticket_messages(ticket_id,sender_id,message)
                         VALUES(?,?,?)""", (tid, session["user_id"], message))
            if is_expert and ticket["user_id"] != session["user_id"]:
                notify(ticket["user_id"], "expert_reply", {"ticket": tid}, link=f"/experts/tickets/{tid}")
            # A farmer replying to a resolved ticket automatically reopens it.
            if not is_expert and ticket["status"] == "resolved":
                c.execute("""UPDATE diagnostic_tickets
                             SET status='in_review', resolved_at=NULL
                             WHERE id=?""", (tid,))
            elif is_expert and ticket["status"] == "open":
                c.execute("""UPDATE diagnostic_tickets
                             SET assigned_expert_id=?, status='in_review'
                             WHERE id=? AND assigned_expert_id IS NULL""",
                          (session["user_id"], tid))
            c.commit()
        elif action == "claim" and is_expert:
            c.execute("""UPDATE diagnostic_tickets
                         SET assigned_expert_id=?, status='in_review'
                         WHERE id=?""", (session["user_id"], tid))
            c.commit()
        elif action == "resolve" and is_expert:
            resolution = request.form.get("resolution", "").strip()
            if resolution:
                c.execute("""INSERT INTO ticket_messages(ticket_id,sender_id,message)
                             VALUES(?,?,?)""", (tid, session["user_id"], resolution))
            c.execute("""UPDATE diagnostic_tickets
                         SET assigned_expert_id=?, status='resolved',
                             resolution=?, resolved_at=NOW()
                         WHERE id=?""",
                      (session["user_id"], resolution or ticket.get("resolution"), tid))
            c.commit()
            if ticket["user_id"] != session["user_id"]:
                notify(ticket["user_id"], "ticket_resolved", {"ticket": tid}, link=f"/experts/tickets/{tid}")
        elif action == "reopen" and is_expert:
            c.execute("""UPDATE diagnostic_tickets
                         SET status='in_review', resolved_at=NULL
                         WHERE id=?""", (tid,))
            c.commit()
        c.close()
        return redirect(url_for("ticket_detail", tid=tid))

    messages = c.execute("""SELECT m.*, u.name AS sender_name, u.is_admin
                            FROM ticket_messages m
                            JOIN users u ON u.id=m.sender_id
                            WHERE ticket_id=? ORDER BY m.created_at, m.id""",
                         (tid,)).fetchall()
    c.close()
    return render_template("expert_ticket_detail.html", ticket=ticket,
                           messages=messages, is_expert=is_expert)


# ======================= PHASE 1: SCAN HISTORY, HEALTH TIMELINE, ANALYTICS, PDF =======================
from markupsafe import Markup, escape as _esc

def pretty_disease(name):
    return str(name or "").replace("___", " - ").replace("_", " ").strip()

def _fmt_date(v, fmt="%d %b %Y"):
    try: return v.strftime(fmt)
    except AttributeError: return str(v)[:10]

def svg_line_chart(points, y_min=0, y_max=100, color="#15803d", label=""):
    """Responsive inline-SVG line chart. points = [(x_label, value)], values must be numbers. No JS, no CDN, works offline."""
    W, H, PL, PR, PT, PB = 520, 210, 36, 14, 14, 34
    n = len(points)
    def X(i): return PL + (W - PL - PR) * (i / (n - 1) if n > 1 else 0.5)
    def Y(v): return PT + (H - PT - PB) * (1 - (float(v) - y_min) / ((y_max - y_min) or 1))
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{_esc(label)}" style="width:100%;height:auto;max-width:100%">']
    for g in range(5):
        gv = y_min + (y_max - y_min) * g / 4
        out.append(f'<line x1="{PL}" x2="{W-PR}" y1="{Y(gv):.1f}" y2="{Y(gv):.1f}" stroke="#d1d5db" stroke-width="1"/>'
                   f'<text x="{PL-6}" y="{Y(gv)+4:.1f}" font-size="11" text-anchor="end" fill="#6b7280">{gv:g}</text>')
    coords = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(points))
    out.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round"/>')
    for i, (lab, v) in enumerate(points):
        out.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="4" fill="{color}"><title>{_esc(lab)}: {float(v):g}</title></circle>')
    out.append(f'<text x="{PL}" y="{H-10}" font-size="11" fill="#6b7280">{_esc(points[0][0])}</text>'
               f'<text x="{W-PR}" y="{H-10}" font-size="11" text-anchor="end" fill="#6b7280">{_esc(points[-1][0])}</text></svg>')
    return Markup("".join(out))

_SEV_LEVEL = {"none": 0, "medium": 1, "high": 2}

def timeline_charts(rows):
    """rows: oldest->newest scans of ONE user. Returns charts only when there are >=2 real data points."""
    charts = {"health": None, "confidence": None, "severity": None}
    h = [(_fmt_date(r["created_at"], "%d %b"), r["health_score"]) for r in rows if r["health_score"] is not None]
    cf = [(_fmt_date(r["created_at"], "%d %b"), float(r["confidence"] or 0)) for r in rows if r["severity"] is not None]
    sv = [(_fmt_date(r["created_at"], "%d %b"), _SEV_LEVEL[r["severity"]]) for r in rows if r["severity"] in _SEV_LEVEL]
    if len(h) >= 2:  charts["health"] = svg_line_chart(h, 0, 100, "#15803d", "Crop health trend")
    if len(cf) >= 2: charts["confidence"] = svg_line_chart(cf, 0, 100, "#2563eb", "Confidence trend")
    if len(sv) >= 2: charts["severity"] = svg_line_chart(sv, 0, 2, "#dc2626", "Severity trend")
    charts["health_points"] = len(h)
    return charts

def build_analytics(user_id):
    c = db()
    try:
        rows = c.execute("SELECT id,plant,disease,confidence,status,health_score,severity,created_at FROM scans "
                         "WHERE user_id=? ORDER BY id DESC LIMIT 300", (user_id,)).fetchall()
    finally:
        c.close()
    rows = list(reversed(rows))
    diagnosed = [r for r in rows if r["severity"] is not None]
    dist = {}
    for r in diagnosed: dist[pretty_disease(r["disease"])] = dist.get(pretty_disease(r["disease"]), 0) + 1
    top = sorted(dist.items(), key=lambda kv: -kv[1])[:8]
    mx = max([v for _, v in top] or [1])
    sev = {k: sum(1 for r in diagnosed if r["severity"] == k) for k in ("none", "medium", "high")}
    out = {"scan_count": len(rows), "diagnosed_count": len(diagnosed),
           "distribution": [(k, v, int(100 * v / mx)) for k, v in top],
           "severity": sev, "charts": timeline_charts(diagnosed)}
    scored = [r["health_score"] for r in rows if r["health_score"] is not None]
    out["avg_health"] = round(sum(scored) / len(scored)) if scored else None
    out["fields"] = field_health_summary(user_id)
    return out

@app.route("/history")
@login_required
def scan_history():
    uid = session["user_id"]
    plant = (request.args.get("plant") or "").strip()[:100]
    try: page = max(1, int(request.args.get("page", 1)))
    except ValueError: page = 1
    per = 12
    c = db()
    try:
        plants = [r["plant"] for r in c.execute("SELECT DISTINCT plant FROM scans WHERE user_id=? AND plant<>'' ORDER BY plant", (uid,)).fetchall()]
        if plant and plant not in plants: plant = ""
        where, args = ("WHERE user_id=?", [uid]) if not plant else ("WHERE user_id=? AND plant=?", [uid, plant])
        total = c.execute(f"SELECT COUNT(*) AS n FROM scans {where}", tuple(args)).fetchone()["n"]
        scans = c.execute(f"SELECT id,image,plant,disease,confidence,status,health_score,severity,created_at FROM scans {where} "
                          "ORDER BY id DESC LIMIT ? OFFSET ?", tuple(args + [per, (page - 1) * per])).fetchall()
        tl = []
        if plant:
            tl = c.execute("SELECT confidence,health_score,severity,created_at FROM scans WHERE user_id=? AND plant=? AND severity IS NOT NULL ORDER BY id DESC LIMIT 60",
                           (uid, plant)).fetchall()
            tl = list(reversed(tl))
    finally:
        c.close()
    return render_template("history.html", scans=scans, plants=plants, plant=plant, page=page, pages=max(1, -(-total // per)),
                           total=total, charts=timeline_charts(tl) if plant else None, pretty=pretty_disease, fmt=_fmt_date)

def _load_scan_or_404(sid):
    """Server-side ownership: a scan is only ever loaded WHERE id AND user_id match the session (no IDOR)."""
    c = db()
    try:
        row = c.execute("SELECT * FROM scans WHERE id=? AND user_id=?", (sid, session["user_id"])).fetchone()
    finally:
        c.close()
    if not row:
        abort(404)
    return row

def _wx_of(row):
    try: return json.loads(row["weather_json"]) if row["weather_json"] else None
    except (ValueError, TypeError): return None

def _scan_view_model(row, lang):
    saved = {}
    try: saved = json.loads(row["result_json"] or "{}")
    except (ValueError, TypeError): saved = {}
    cls = row["disease"]
    low = bool(saved.get("low_confidence"))
    info = None
    if not low and cls in DISEASE_DATA:
        info = disease_info(cls, lang)
    elif saved.get("info"):
        info = saved["info"]
    return {"saved": saved, "info": info, "low": low}

@app.route("/history/<int:sid>")
@login_required
def scan_detail(sid):
    row = _load_scan_or_404(sid)
    lang = session.get("lang", "en")
    vm = _scan_view_model(row, lang)
    c = db()
    try:
        hist = c.execute("SELECT id,health_score,disease,confidence,created_at FROM scans WHERE user_id=? AND plant=? AND health_score IS NOT NULL ORDER BY id DESC LIMIT 10",
                         (session["user_id"], row["plant"])).fetchall()
    finally:
        c.close()
    try: wx = json.loads(row["weather_json"]) if row["weather_json"] else None
    except (ValueError, TypeError): wx = None
    return render_template("history_detail.html", s=row, vm=vm, hist=hist, pretty=pretty_disease, fmt=_fmt_date, user_fields=my_fields(), wx=wx,
                           can_explain=(row["status"] == "Local TFLite" and row["disease"] in labels))

@app.route("/history/<int:sid>/report.pdf")
@login_required
def scan_report_pdf(sid):
    if _rate_hit(("pdf", session["user_id"]), 20, 60):
        return _error_response(429, "Too many requests. Please try again later.")
    row = _load_scan_or_404(sid)
    lang = session.get("lang", "en")
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
    except ImportError:
        return _error_response(500, "PDF generation is not available on this server.")
    from xml.sax.saxutils import escape as xesc

    def enc_ok(t):
        try: str(t).encode("cp1252"); return True
        except UnicodeEncodeError: return False
    def pt(en):  # translate only when the built-in PDF font (Latin) can render it; otherwise keep English
        t = tr_fast(en, lang)
        return t if enc_ok(t) else en
    vm = _scan_view_model(row, lang)
    info = vm["info"]
    if info and not all(enc_ok(x) for v in info.values() for x in (v if isinstance(v, list) else [v])):
        info = disease_info(row["disease"], "en") if row["disease"] in DISEASE_DATA else vm["saved"].get("info")
    c = db()
    try:
        hist = c.execute("SELECT health_score,disease,created_at FROM scans WHERE user_id=? AND plant=? AND health_score IS NOT NULL ORDER BY id DESC LIMIT 10",
                         (session["user_id"], row["plant"])).fetchall()
    finally:
        c.close()
    st = getSampleStyleSheet()
    H1 = ParagraphStyle("h1", parent=st["Title"], textColor=colors.HexColor("#14532d"), fontSize=20)
    H2 = ParagraphStyle("h2", parent=st["Heading2"], textColor=colors.HexColor("#14532d"), fontSize=13, spaceBefore=10)
    B = st["BodyText"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=16*mm,
                            title="RBAgriScan crop report", author="RBAgriScan")
    story = [Paragraph("RBAgriScan - " + xesc(pt("Crop Report")), H1)]
    score = row["health_score"]
    facts = [[pt("Plant"), xesc(str(row["plant"]))],
             [pt("Scientific name"), xesc(str(vm["saved"].get("scientific_name") or "-"))],
             [pt("Disease"), xesc(pretty_disease(row["disease"]))],
             [pt("Confidence"), f'{float(row["confidence"] or 0):.1f}%'],
             [pt("Crop Health Score"), f"{score}/100" if score is not None else pt("Not enough data yet")],
             [pt("Scan date/time"), str(row["created_at"])[:19]],
             [pt("Weather at scan time"), (lambda w: f"{w.get('temp')} C, {w.get('humidity')}% RH, rain {w.get('rain')} mm, wind {w.get('wind')} km/h" if w else pt("Not recorded for this scan"))(_wx_of(row))]]
    t = Table([[Paragraph(a, B), Paragraph(b, B)] for a, b in facts], colWidths=[55*mm, 110*mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#d1d5db")), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0fdf4")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [t, Spacer(1, 6)]
    try:
        from PIL import Image as PILImage
        ipath = os.path.join(UPLOAD_FOLDER, os.path.basename(row["image"] or ""))
        if os.path.isfile(ipath):
            im = PILImage.open(ipath).convert("RGB"); im.thumbnail((900, 900))
            ib = io.BytesIO(); im.save(ib, "JPEG", quality=82); ib.seek(0)
            w, h = im.size; scale = min(80*mm / w, 80*mm / h)
            story += [RLImage(ib, width=w*scale, height=h*scale), Spacer(1, 6)]
    except Exception:
        app.logger.exception("PDF image embed failed")
    if vm["low"]:
        story.append(Paragraph(xesc(pt("Disease could not be confidently identified.")), B))
    expl = vm["saved"].get("explanation")
    if expl and enc_ok(expl):
        story += [Paragraph(xesc(pt("Notes")), H2), Paragraph(xesc(expl), B)]
    if info:
        for key, title in (("home", "Basic care / home remedies"), ("natural", "Natural treatment advisory"), ("field", "Field treatment advisory"),
                           ("chemical", "Chemical advisory"), ("prevention", "Prevention")):
            items = info.get(key) or []
            if items:
                story.append(Paragraph(xesc(pt(title)), H2))
                story += [Paragraph("&bull; " + xesc(str(x)), B) for x in items]
    story.append(Paragraph(xesc(pt("Crop health history")) + " - " + xesc(str(row["plant"])), H2))
    if len(hist) >= 2:
        data = [[pt("Date"), pt("Disease"), pt("Health score")]] + [[_fmt_date(r["created_at"]), pretty_disease(r["disease"]), str(r["health_score"])] for r in reversed(hist)]
        ht = Table([[Paragraph(xesc(x), B) for x in rw] for rw in data], colWidths=[35*mm, 100*mm, 30*mm])
        ht.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#d1d5db")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dcfce7"))]))
        story.append(ht)
    else:
        story.append(Paragraph(xesc(pt("Not enough data yet")), B))
    story += [Spacer(1, 10), Paragraph("<i>" + xesc(pt("Disclaimer: This report is AI-generated guidance for information only. The health score is indicative and not a laboratory measurement. Confirm serious problems with a local agriculture expert before applying any treatment.")) + "</i>", B)]
    doc.build(story)
    buf.seek(0)
    resp = app.response_class(buf.getvalue(), mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'attachment; filename="RBAgriScan-report-{int(row["id"])}.pdf"'
    resp.headers["Cache-Control"] = "private, no-store"
    return resp



# ======================= PHASE 2: FARMS / FIELDS =======================
MAX_FIELDS_PER_USER = 50

def _clean_text(v, n):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(v or "")).strip()[:n]

def _parse_area(v):
    v = str(v or "").strip().replace(",", ".")
    if not v: return None, True
    try: a = round(float(v), 2)
    except ValueError: return None, False
    return (a, True) if 0.01 <= a <= 100000 else (None, False)

def _field_form_values():
    """Validate the create/edit form. Returns (values, error_message)."""
    name = _clean_text(request.form.get("name"), 120)
    crop = _clean_text(request.form.get("crop"), 120)
    farm = _clean_text(request.form.get("farm_name"), 120)
    notes = _clean_text(request.form.get("notes"), 500)
    area, ok = _parse_area(request.form.get("area_acres"))
    if not name or not crop: return None, "Please enter a field name and a crop."
    if not ok: return None, "Area must be a number between 0.01 and 100000 acres."
    return (farm, name, crop, area, notes or None), None

def get_field_or_404(fid):
    c = db()
    try:
        row = c.execute("SELECT * FROM fields WHERE id=? AND user_id=?", (fid, session["user_id"])).fetchone()
    finally:
        c.close()
    if not row: abort(404)
    return row

def field_health_summary(user_id):
    """Per-field scan count and latest score from real scans only."""
    c = db()
    try:
        fields = c.execute("SELECT id,farm_name,name,crop,area_acres FROM fields WHERE user_id=? ORDER BY farm_name,name", (user_id,)).fetchall()
        scans = c.execute("SELECT field_id,health_score,severity,created_at FROM scans WHERE user_id=? AND field_id IS NOT NULL ORDER BY id", (user_id,)).fetchall()
    finally:
        c.close()
    by = {}
    for r in scans: by.setdefault(r["field_id"], []).append(r)
    out = []
    for f in fields:
        rows = by.get(f["id"], [])
        scored = [r for r in rows if r["health_score"] is not None]
        out.append({"id": f["id"], "farm_name": f["farm_name"], "name": f["name"], "crop": f["crop"], "area": f["area_acres"],
                    "scans": len(rows), "latest": scored[-1]["health_score"] if scored else None,
                    "avg": round(sum(r["health_score"] for r in scored) / len(scored)) if scored else None,
                    "last_scan": rows[-1]["created_at"] if rows else None})
    return out

def my_fields():
    """Jinja helper (only queries when a template calls it): the logged-in user's fields for <select> boxes."""
    if "user_id" not in session: return []
    c = db()
    try:
        return c.execute("SELECT id,name,crop FROM fields WHERE user_id=? ORDER BY name LIMIT 100", (session["user_id"],)).fetchall()
    finally:
        c.close()
app.jinja_env.globals["my_fields"] = my_fields

@app.route("/fields", methods=["GET", "POST"])
@login_required
def fields_list():
    lang = session.get("lang", "en")
    uid = session["user_id"]
    if request.method == "POST":
        vals, err = _field_form_values()
        c = db()
        try:
            n = c.execute("SELECT COUNT(*) AS n FROM fields WHERE user_id=?", (uid,)).fetchone()["n"]
            if not err and n >= MAX_FIELDS_PER_USER: err = "You can add up to %d fields." % MAX_FIELDS_PER_USER
            if err:
                flash(tr_fast(err, lang), "error")
            else:
                c.execute("INSERT INTO fields(user_id,farm_name,name,crop,area_acres,notes) VALUES(?,?,?,?,?,?)", (uid,) + vals)
                c.commit()
                flash(tr_fast("Field added.", lang), "success")
        finally:
            c.close()
        return redirect(url_for("fields_list"))
    summary = field_health_summary(uid)
    farms = {}
    for f in summary: farms.setdefault(f["farm_name"] or "", []).append(f)
    total_area = sum(float(f["area"]) for f in summary if f["area"] is not None)
    return render_template("fields.html", farms=farms, total=len(summary), total_area=total_area)

@app.route("/fields/<int:fid>")
@login_required
def field_detail(fid):
    f = get_field_or_404(fid)
    uid = session["user_id"]
    c = db()
    try:
        scans = c.execute("SELECT id,image,plant,disease,confidence,health_score,severity,created_at FROM scans WHERE user_id=? AND field_id=? ORDER BY id DESC LIMIT 60", (uid, fid)).fetchall()
        devices = c.execute("SELECT id,name FROM sensor_devices WHERE user_id=? AND field_id=?", (uid, fid)).fetchall()
        sensors = []
        for d in devices:
            # age is computed by the DB itself (same clock as recorded_at) so server/DB timezone differences cannot fake "connected"
            r = c.execute("SELECT soil_moisture,temperature,humidity,light_lux,n_ppm,p_ppm,k_ppm,recorded_at,TIMESTAMPDIFF(MINUTE,recorded_at,NOW()) AS age_min FROM sensor_readings WHERE device_id=? ORDER BY recorded_at DESC LIMIT 1", (d["id"],)).fetchone()
            live = bool(r and r["age_min"] is not None and r["age_min"] < 30)
            sensors.append({"id": d["id"], "name": d["name"], "reading": r, "live": live})
    finally:
        c.close()
    diagnosed = [r for r in reversed(scans) if r["severity"] is not None]
    counts = {}
    for r in diagnosed: counts[pretty_disease(r["disease"])] = counts.get(pretty_disease(r["disease"]), 0) + 1
    scored = [r["health_score"] for r in scans if r["health_score"] is not None]
    return render_template("field_detail.html", f=f, scans=scans[:12], scan_total=len(scans), charts=timeline_charts(diagnosed),
                           diseases=sorted(counts.items(), key=lambda kv: -kv[1]), sensors=sensors,
                           latest=scored[0] if scored else None, avg=round(sum(scored) / len(scored)) if scored else None,
                           pretty=pretty_disease, fmt=_fmt_date)

@app.route("/fields/<int:fid>/edit", methods=["POST"])
@login_required
def field_edit(fid):
    get_field_or_404(fid)
    lang = session.get("lang", "en")
    vals, err = _field_form_values()
    if err:
        flash(tr_fast(err, lang), "error")
    else:
        c = db()
        try:
            c.execute("UPDATE fields SET farm_name=?,name=?,crop=?,area_acres=?,notes=? WHERE id=? AND user_id=?", vals + (fid, session["user_id"]))
            c.commit()
        finally:
            c.close()
        flash(tr_fast("Field updated.", lang), "success")
    return redirect(url_for("field_detail", fid=fid))

@app.route("/fields/<int:fid>/delete", methods=["POST"])
@login_required
def field_delete(fid):
    get_field_or_404(fid)
    c = db()
    try:
        # Scans and sensors are kept (just unlinked); only the field record is removed.
        c.execute("UPDATE scans SET field_id=NULL WHERE field_id=? AND user_id=?", (fid, session["user_id"]))
        c.execute("UPDATE sensor_devices SET field_id=NULL WHERE field_id=? AND user_id=?", (fid, session["user_id"]))
        c.execute("DELETE FROM fields WHERE id=? AND user_id=?", (fid, session["user_id"]))
        c.commit()
    finally:
        c.close()
    flash(tr_fast("Field deleted. Its scans were kept in your history.", session.get("lang", "en")), "success")
    return redirect(url_for("fields_list"))

@app.route("/history/<int:sid>/field", methods=["POST"])
@login_required
def scan_set_field(sid):
    _load_scan_or_404(sid)
    raw = request.form.get("field_id", "")
    fid = owned_field_id(raw) if raw else None
    if raw and not fid:
        abort(404)
    c = db()
    try:
        c.execute("UPDATE scans SET field_id=? WHERE id=? AND user_id=?", (fid, sid, session["user_id"]))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("scan_detail", sid=sid))

@app.route("/fields/<int:fid>/report.pdf")
@login_required
def field_report_pdf(fid):
    if _rate_hit(("pdf", session["user_id"]), 20, 60):
        return _error_response(429, "Too many requests. Please try again later.")
    f = get_field_or_404(fid)
    lang = session.get("lang", "en")
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    except ImportError:
        return _error_response(500, "PDF generation is not available on this server.")
    from xml.sax.saxutils import escape as xesc
    def pt(en):
        t = tr_fast(en, lang)
        try: t.encode("cp1252"); return t
        except UnicodeEncodeError: return en
    c = db()
    try:
        scans = c.execute("SELECT plant,disease,confidence,health_score,severity,created_at FROM scans WHERE user_id=? AND field_id=? ORDER BY id DESC LIMIT 200", (session["user_id"], fid)).fetchall()
        devs = c.execute("SELECT id,name FROM sensor_devices WHERE user_id=? AND field_id=?", (session["user_id"], fid)).fetchall()
        sens = []
        for d in devs:
            r = c.execute("SELECT soil_moisture,temperature,humidity,recorded_at FROM sensor_readings WHERE device_id=? ORDER BY recorded_at DESC LIMIT 1", (d["id"],)).fetchone()
            sens.append((d["name"], r))
    finally:
        c.close()
    st = getSampleStyleSheet()
    H1 = ParagraphStyle("h1", parent=st["Title"], textColor=colors.HexColor("#14532d"), fontSize=20)
    H2 = ParagraphStyle("h2", parent=st["Heading2"], textColor=colors.HexColor("#14532d"), fontSize=13, spaceBefore=10)
    B = st["BodyText"]
    GRID = [("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#d1d5db")), ("VALIGN", (0, 0), (-1, -1), "TOP")]
    scored = [r["health_score"] for r in scans if r["health_score"] is not None]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=16*mm, title="RBAgriScan field report")
    story = [Paragraph("RBAgriScan - " + xesc(pt("Field Report")), H1)]
    facts = [[pt("Farm"), f["farm_name"] or "-"], [pt("Field"), f["name"]], [pt("Crop"), f["crop"]],
             [pt("Area (acres)"), str(f["area_acres"]) if f["area_acres"] is not None else "-"], [pt("Total scans"), str(len(scans))],
             [pt("Latest health score"), f"{scored[0]}/100" if scored else pt("Not enough data yet")],
             [pt("Average health score"), f"{round(sum(scored)/len(scored))}/100" if scored else pt("Not enough data yet")],
             [pt("Weather"), pt("Not recorded for this field")]]
    t = Table([[Paragraph(xesc(str(a)), B), Paragraph(xesc(str(b)), B)] for a, b in facts], colWidths=[55*mm, 110*mm])
    t.setStyle(TableStyle(GRID + [("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0fdf4"))]))
    story += [t, Paragraph(xesc(pt("Disease history")), H2)]
    if scans:
        rows = [[pt("Date"), pt("Plant"), pt("Disease"), pt("Health score")]] + [
            [_fmt_date(r["created_at"]), r["plant"], pretty_disease(r["disease"]), "-" if r["health_score"] is None else str(r["health_score"])] for r in scans[:25]]
        ht = Table([[Paragraph(xesc(str(x)), B) for x in rw] for rw in rows], colWidths=[28*mm, 30*mm, 80*mm, 27*mm])
        ht.setStyle(TableStyle(GRID + [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dcfce7"))]))
        story.append(ht)
    else:
        story.append(Paragraph(xesc(pt("Not enough data yet")), B))
    story.append(Paragraph(xesc(pt("Sensors")), H2))
    if not sens:
        story.append(Paragraph(xesc(pt("No live sensor data available")), B))
    for name, r in sens:
        if r: story.append(Paragraph(xesc(f"{name}: moisture {r['soil_moisture']}, temp {r['temperature']}, humidity {r['humidity']} ({str(r['recorded_at'])[:16]})"), B))
        else: story.append(Paragraph(xesc(f"{name}: " + pt("No live sensor data available")), B))
    story += [Spacer(1, 10), Paragraph("<i>" + xesc(pt("Disclaimer: AI-generated guidance for information only. Health scores are indicative, not laboratory measurements.")) + "</i>", B)]
    doc.build(story); buf.seek(0)
    resp = app.response_class(buf.getvalue(), mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'attachment; filename="RBAgriScan-field-{int(f["id"])}.pdf"'
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


# ======================= PHASE 3: NOTIFICATIONS (in-app) =======================
# Architecture: producers call notify(user, kind, params). The row stores kind + params only; the text is
# rendered when the page is shown, in the viewer's language (templates below are the translatable English source).
# Delivery channels: in-app today. NOTIFICATION_CHANNELS is the extension point for email / web-push later.
# Not produced yet (need weather data, Phase 6): disease_risk and weather_advisory notifications.
WEATHER_ADVICE = {   # code -> (notification kind, English advisory text with {value})
    "heat": ("wx_heat", "Very hot weather expected (up to {value}°C). Irrigate early morning or evening and watch for heat stress."),
    "frost": ("wx_frost", "Cold weather expected (down to {value}°C). Protect young plants from frost."),
    "rain": ("wx_rain", "Heavy rain expected ({value} mm in 3 days). Avoid spraying and make sure fields drain well."),
    "wind": ("wx_wind", "Strong wind expected (up to {value} km/h). Avoid spraying and support tall plants."),
    "dry": ("wx_dry", "Hot and dry days ahead with almost no rain. Plan irrigation."),
}
NOTIF_TEMPLATES = {
    "scan_reminder":   {"icon": "🔔", "pref": "scan_reminders", "title": "Time to scan your plants",
                        "body": "You have not scanned any plant for {days} days. A quick scan helps catch problems early."},
    "field_reminder":  {"icon": "📷", "pref": "scan_reminders", "title": "Scan reminder: {field}",
                        "body": "{field} has not been scanned for {days} days."},
    "low_moisture":    {"icon": "💧", "pref": "soil_moisture", "title": "Low soil moisture",
                        "body": "Sensor {device} reports {value}% soil moisture. Consider irrigating soon."},
    "expert_reply":    {"icon": "👨‍🌾", "pref": "expert_reply", "title": "An expert replied to your ticket",
                        "body": "There is a new reply on your Plant Hospital ticket #{ticket}."},
    "ticket_resolved": {"icon": "✅", "pref": "expert_reply", "title": "Your ticket was resolved",
                        "body": "Your Plant Hospital ticket #{ticket} was marked resolved."},
    "health_drop":     {"icon": "⚠️", "pref": "health_change", "title": "Crop health dropped",
                        "body": "{plant} health score fell from {old} to {new}. Please review the latest scan."},
    "disease_risk":    {"icon": "🌦️", "pref": "disease_risk", "title": "Estimated disease risk: {level}",
                        "body": "Weather conditions suggest an estimated {level} risk of {disease} for {crop}. This is an advisory, not a guarantee."},
    "disease_risk_generic": {"icon": "🌦️", "pref": "disease_risk", "title": "Estimated disease risk: {level}",
                        "body": "Weather conditions suggest an estimated {level} risk of {disease} for your crops. This is an advisory, not a guarantee."},
    "wx_heat":         {"icon": "🔥", "pref": "weather_advisory", "title": "Weather advisory", "body": WEATHER_ADVICE["heat"][1]},
    "wx_frost":        {"icon": "❄️", "pref": "weather_advisory", "title": "Weather advisory", "body": WEATHER_ADVICE["frost"][1]},
    "wx_rain":         {"icon": "🌧️", "pref": "weather_advisory", "title": "Weather advisory", "body": WEATHER_ADVICE["rain"][1]},
    "wx_wind":         {"icon": "💨", "pref": "weather_advisory", "title": "Weather advisory", "body": WEATHER_ADVICE["wind"][1]},
    "wx_dry":          {"icon": "☀️", "pref": "weather_advisory", "title": "Weather advisory", "body": WEATHER_ADVICE["dry"][1]},
    "health_up":       {"icon": "📈", "pref": "health_change", "title": "Crop health improved",
                        "body": "{plant} health score improved from {old} to {new}."},
}
NOTIFICATION_CHANNELS = []   # future: callables(user_id, kind, params, link) for email / push
REMINDER_DAY_CHOICES = (3, 7, 14, 30)
PREF_DEFAULTS = {"scan_reminders": 1, "soil_moisture": 1, "expert_reply": 1, "health_change": 1, "disease_risk": 1, "weather_advisory": 1, "reminder_days": 7}
MAX_NOTIFICATIONS_PER_USER = 200

def get_prefs(user_id):
    c = db()
    try:
        row = c.execute("SELECT scan_reminders,soil_moisture,expert_reply,health_change,disease_risk,weather_advisory,reminder_days FROM notification_prefs WHERE user_id=?", (user_id,)).fetchone()
    finally:
        c.close()
    out = dict(PREF_DEFAULTS)
    if row: out.update({k: row[k] for k in PREF_DEFAULTS})
    return out

def _internal_link(link):
    return link if link and link.startswith("/") and not link.startswith("//") and "\\" not in link and "://" not in link else None

def notify(user_id, kind, params=None, link=None, dedupe_key=None):
    """Create an in-app notification if the user has that kind enabled. Duplicates (same dedupe_key) are ignored. Never raises."""
    tpl = NOTIF_TEMPLATES.get(kind)
    if not tpl: return False
    try:
        if not get_prefs(user_id).get(tpl["pref"]): return False
        c = db()
        try:
            c.execute("INSERT INTO notifications(user_id,kind,params_json,link,dedupe_key) VALUES(?,?,?,?,?)",
                      (user_id, kind, json.dumps(params or {}, ensure_ascii=False)[:1000], _internal_link(link), dedupe_key))
            c.commit()
        finally:
            c.close()
        for ch in NOTIFICATION_CHANNELS:
            try: ch(user_id, kind, params, link)
            except Exception: app.logger.exception("notification channel failed")
        return True
    except IntegrityError:
        return False   # already notified for this event
    except Exception:
        app.logger.exception("notify failed")
        return False

_PLACEHOLDER = re.compile(r"\{\s*(\w+)\s*\}")
def _fill(text, params):
    """Safe placeholder substitution (NOT str.format: translated text is untrusted)."""
    return _PLACEHOLDER.sub(lambda m: str(params.get(m.group(1), m.group(0))), text)

def render_notification(row, lang):
    tpl = NOTIF_TEMPLATES.get(row["kind"])
    try: params = json.loads(row["params_json"] or "{}")
    except (ValueError, TypeError): params = {}
    if not tpl: return None
    def one(en):
        t = tr_fast(en, lang)
        # a translator may mangle {placeholders}; if any placeholder disappeared fall back to English
        if set(_PLACEHOLDER.findall(en)) - set(_PLACEHOLDER.findall(t)): t = en
        return _fill(t, params)
    return {"id": row["id"], "icon": tpl["icon"], "title": one(tpl["title"]), "body": one(tpl["body"]),
            "unread": not row["is_read"], "created_at": row["created_at"], "has_link": bool(row["link"])}

def generate_due_notifications(user_id):
    """Lazy 'scheduler' (works on hosts without background workers): scan reminders based on the DB clock, deduped per ISO week."""
    prefs = get_prefs(user_id)
    if prefs["scan_reminders"]: _gen_scan_reminders(user_id, prefs)
    if prefs["disease_risk"] or prefs["weather_advisory"]:
        try: _gen_weather_alerts(user_id, prefs)
        except Exception: app.logger.exception("weather alerts failed")
    _prune_notifications(user_id)

def _gen_scan_reminders(user_id, prefs):
    days = prefs["reminder_days"] if prefs["reminder_days"] in REMINDER_DAY_CHOICES else 7
    wk = "%d-%02d" % date.today().isocalendar()[:2]
    c = db()
    try:
        fields = c.execute("SELECT f.id,f.name,TIMESTAMPDIFF(DAY,COALESCE((SELECT MAX(s.created_at) FROM scans s WHERE s.field_id=f.id AND s.user_id=f.user_id),f.created_at),NOW()) AS age "
                           "FROM fields f WHERE f.user_id=?", (user_id,)).fetchall()
        tot = c.execute("SELECT COUNT(*) AS n, TIMESTAMPDIFF(DAY,MAX(created_at),NOW()) AS age FROM scans WHERE user_id=?", (user_id,)).fetchone()
    finally:
        c.close()
    for f in fields:
        if f["age"] is not None and f["age"] >= days:
            notify(user_id, "field_reminder", {"field": f["name"], "days": f["age"]}, link=f"/fields/{f['id']}", dedupe_key=f"rem-f{f['id']}-{wk}")
    if not fields and tot and tot["n"] and tot["age"] is not None and tot["age"] >= days:
        notify(user_id, "scan_reminder", {"days": tot["age"]}, link="/", dedupe_key=f"rem-all-{wk}")

def _prune_notifications(user_id):
    c = db()
    try:   # keep the table small: newest MAX_NOTIFICATIONS_PER_USER only
        c.execute("DELETE FROM notifications WHERE user_id=? AND id < (SELECT m FROM (SELECT id AS m FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 1 OFFSET ?) t)",
                  (user_id, user_id, MAX_NOTIFICATIONS_PER_USER - 1))
        c.commit()
    finally:
        c.close()

def _maybe_refresh(user_id, force=False):
    last = session.get("notif_checked", 0)
    if force or time.time() - last > 3600:
        session["notif_checked"] = time.time()
        try: generate_due_notifications(user_id)
        except Exception: app.logger.exception("generate_due_notifications failed")

@app.route("/api/notifications/unread")
def notifications_unread():
    if "user_id" not in session:
        return jsonify({"ok": False, "count": 0}), 401
    _maybe_refresh(session["user_id"])
    c = db()
    try:
        n = c.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND is_read=0", (session["user_id"],)).fetchone()["n"]
    finally:
        c.close()
    resp = jsonify({"ok": True, "count": n}); resp.headers["Cache-Control"] = "private, no-store"
    return resp

@app.route("/notifications")
@login_required
def notifications_page():
    uid = session["user_id"]; lang = session.get("lang", "en")
    _maybe_refresh(uid)
    try: page = max(1, int(request.args.get("page", 1)))
    except ValueError: page = 1
    per = 20
    c = db()
    try:
        total = c.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id=?", (uid,)).fetchone()["n"]
        rows = c.execute("SELECT id,kind,params_json,link,is_read,created_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?", (uid, per, (page - 1) * per)).fetchall()
    finally:
        c.close()
    items = [x for x in (render_notification(r, lang) for r in rows) if x]
    return render_template("notifications.html", items=items, page=page, pages=max(1, -(-total // per)), total=total, fmt=_fmt_date)

@app.route("/notifications/<int:nid>/open")
@login_required
def notification_open(nid):
    c = db()
    try:
        row = c.execute("SELECT link FROM notifications WHERE id=? AND user_id=?", (nid, session["user_id"])).fetchone()
        if not row: abort(404)
        c.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?", (nid, session["user_id"])); c.commit()
    finally:
        c.close()
    return redirect(_safe_next(row["link"]) if row["link"] else url_for("notifications_page"))

@app.route("/notifications/read-all", methods=["POST"])
@login_required
def notifications_read_all():
    c = db()
    try:
        c.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (session["user_id"],)); c.commit()
    finally:
        c.close()
    return redirect(url_for("notifications_page"))

@app.route("/notifications/clear", methods=["POST"])
@login_required
def notifications_clear():
    c = db()
    try:
        c.execute("DELETE FROM notifications WHERE user_id=? AND is_read=1", (session["user_id"],)); c.commit()
    finally:
        c.close()
    return redirect(url_for("notifications_page"))

@app.route("/notifications/settings", methods=["GET", "POST"])
@login_required
def notification_settings():
    uid = session["user_id"]; lang = session.get("lang", "en")
    if request.method == "POST":
        try: days = int(request.form.get("reminder_days", 7))
        except ValueError: days = 7
        if days not in REMINDER_DAY_CHOICES: days = 7
        vals = [1 if request.form.get(k) else 0 for k in ("scan_reminders", "soil_moisture", "expert_reply", "health_change", "disease_risk", "weather_advisory")]
        c = db()
        try:
            c.execute("INSERT INTO notification_prefs(user_id,scan_reminders,soil_moisture,expert_reply,health_change,disease_risk,weather_advisory,reminder_days) VALUES(?,?,?,?,?,?,?,?) "
                      "ON DUPLICATE KEY UPDATE scan_reminders=VALUES(scan_reminders),soil_moisture=VALUES(soil_moisture),expert_reply=VALUES(expert_reply),"
                      "health_change=VALUES(health_change),disease_risk=VALUES(disease_risk),weather_advisory=VALUES(weather_advisory),reminder_days=VALUES(reminder_days)", (uid, *vals, days))
            c.commit()
        finally:
            c.close()
        flash(tr_fast("Notification settings saved.", lang), "success")
        return redirect(url_for("notification_settings"))
    return render_template("notification_settings.html", prefs=get_prefs(uid), day_choices=REMINDER_DAY_CHOICES, has_location=get_location(uid) is not None)


# ======================= PHASE 4: FARMER COMMUNITY =======================
REPORT_HIDE_THRESHOLD = 3          # a post/comment is hidden automatically after this many distinct reports (until an admin reviews it)
REPORT_REASONS = ("spam", "abuse", "misleading", "other")

def _clean_multiline(v, n):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(v or "").replace("\r\n", "\n")).strip()[:n]

def _too_many_links(text):
    return len(re.findall(r"https?://|www\.", text, re.I)) > 2

def _is_admin_session():
    return bool(session.get("is_admin"))

def _post_row(c, pid):
    return c.execute("SELECT p.*,u.name AS author,u.role,u.is_admin AS author_admin FROM community_posts p JOIN users u ON u.id=p.user_id WHERE p.id=?", (pid,)).fetchone()

def _can_see_post(post):
    return bool(post) and (post["status"] == "visible" or post["user_id"] == session["user_id"] or _is_admin_session())

@app.route("/community")
@login_required
def community_feed():
    uid = session["user_id"]
    kind = request.args.get("kind", "")
    kind = kind if kind in ("post", "question") else ""
    try: page = max(1, int(request.args.get("page", 1)))
    except ValueError: page = 1
    per = 10
    where, args = ["(p.status='visible' OR p.user_id=? OR ?=1)"], [uid, 1 if _is_admin_session() else 0]
    if kind: where.append("p.kind=?"); args.append(kind)
    c = db()
    try:
        total = c.execute("SELECT COUNT(*) AS n FROM community_posts p WHERE " + " AND ".join(where), tuple(args)).fetchone()["n"]
        posts = c.execute("SELECT p.id,p.user_id,p.kind,p.title,p.body,p.plant,p.image,p.status,p.created_at,u.name AS author,u.role,u.is_admin AS author_admin,"
                          "(SELECT COUNT(*) FROM community_likes l WHERE l.post_id=p.id) AS likes,"
                          "(SELECT COUNT(*) FROM community_comments cm WHERE cm.post_id=p.id AND cm.status='visible') AS comments,"
                          "EXISTS(SELECT 1 FROM community_likes l2 WHERE l2.post_id=p.id AND l2.user_id=?) AS liked "
                          "FROM community_posts p JOIN users u ON u.id=p.user_id WHERE " + " AND ".join(where) + " ORDER BY p.id DESC LIMIT ? OFFSET ?",
                          tuple([uid] + args + [per, (page - 1) * per])).fetchall()
    finally:
        c.close()
    return render_template("community.html", posts=posts, kind=kind, page=page, pages=max(1, -(-total // per)), fmt=_fmt_date)

@app.route("/community/new", methods=["POST"])
@login_required
def community_new():
    uid = session["user_id"]; lang = session.get("lang", "en")
    def back(msg, cat="error"):
        flash(tr_fast(msg, lang), cat); return redirect(url_for("community_feed"))
    if _rate_hit(("cpost", uid), 5, 600):
        return back("You are posting too fast. Please wait a few minutes.")
    title = _clean_text(request.form.get("title"), 150)
    body = _clean_multiline(request.form.get("body"), 4000)
    plant = _clean_text(request.form.get("plant"), 100) or None
    kind = request.form.get("kind") if request.form.get("kind") in ("post", "question") else "post"
    if len(title) < 3 or len(body) < 5:
        return back("Please write a title (3+ characters) and a message (5+ characters).")
    if _too_many_links(title + " " + body):
        return back("Too many links. Please remove some links and try again.")
    image = None
    f = request.files.get("image")
    if f and f.filename:
        if not allowed_file(f.filename): return back("Please attach a JPG, PNG or WEBP image.")
        image, _p = _store_upload(f, uid, prefix="post_")
        if not image: return back("The attached file is not a valid image.")
    c = db()
    try:
        if c.execute("SELECT 1 AS x FROM community_posts WHERE user_id=? AND body=? AND created_at > NOW() - INTERVAL 1 HOUR", (uid, body)).fetchone():
            if image:
                try: os.remove(os.path.join(UPLOAD_FOLDER, image))
                except OSError: pass
            return back("You already posted this. Please do not repeat posts.")
        cur = c.execute("INSERT INTO community_posts(user_id,kind,title,body,plant,image) VALUES(?,?,?,?,?,?)", (uid, kind, title, body, plant, image))
        c.commit(); pid = cur.lastrowid
    finally:
        c.close()
    return redirect(url_for("community_post", pid=pid))

@app.route("/community/<int:pid>")
@login_required
def community_post(pid):
    uid = session["user_id"]
    c = db()
    try:
        post = _post_row(c, pid)
        if not _can_see_post(post): abort(404)
        likes = c.execute("SELECT COUNT(*) AS n FROM community_likes WHERE post_id=?", (pid,)).fetchone()["n"]
        liked = bool(c.execute("SELECT 1 AS x FROM community_likes WHERE post_id=? AND user_id=?", (pid, uid)).fetchone())
        rows = c.execute("SELECT cm.id,cm.user_id,cm.parent_id,cm.body,cm.status,cm.created_at,u.name AS author,u.role,u.is_admin AS author_admin "
                         "FROM community_comments cm JOIN users u ON u.id=cm.user_id WHERE cm.post_id=? AND (cm.status='visible' OR ?=1) ORDER BY cm.id", (pid, 1 if _is_admin_session() else 0)).fetchall()
    finally:
        c.close()
    top = [r for r in rows if r["parent_id"] is None]
    replies = {}
    for r in rows:
        if r["parent_id"] is not None: replies.setdefault(r["parent_id"], []).append(r)
    return render_template("community_post.html", p=post, likes=likes, liked=liked, top=top, replies=replies, reasons=REPORT_REASONS, fmt=_fmt_date)

@app.route("/community/<int:pid>/comment", methods=["POST"])
@login_required
def community_comment(pid):
    uid = session["user_id"]; lang = session.get("lang", "en")
    body = _clean_multiline(request.form.get("body"), 1000)
    if _rate_hit(("ccomment", uid), 20, 600):
        flash(tr_fast("You are commenting too fast. Please wait a few minutes.", lang), "error"); return redirect(url_for("community_post", pid=pid))
    c = db()
    try:
        post = _post_row(c, pid)
        if not post or post["status"] != "visible": abort(404)
        parent = None
        if request.form.get("parent_id"):
            try: parent = int(request.form["parent_id"])
            except ValueError: abort(400)
            if not c.execute("SELECT 1 AS x FROM community_comments WHERE id=? AND post_id=? AND parent_id IS NULL AND status='visible'", (parent, pid)).fetchone():
                abort(404)
        if len(body) < 1 or _too_many_links(body):
            flash(tr_fast("Please write a comment (without too many links).", lang), "error")
        elif c.execute("SELECT 1 AS x FROM community_comments WHERE user_id=? AND body=? AND created_at > NOW() - INTERVAL 10 MINUTE", (uid, body)).fetchone():
            flash(tr_fast("You already posted this comment.", lang), "error")
        else:
            c.execute("INSERT INTO community_comments(post_id,user_id,parent_id,body) VALUES(?,?,?,?)", (pid, uid, parent, body)); c.commit()
    finally:
        c.close()
    return redirect(url_for("community_post", pid=pid))

@app.route("/community/<int:pid>/like", methods=["POST"])
@login_required
def community_like(pid):
    uid = session["user_id"]
    if _rate_hit(("clike", uid), 60, 60): abort(429)
    c = db()
    try:
        post = _post_row(c, pid)
        if not post or post["status"] != "visible": abort(404)
        try:
            c.execute("INSERT INTO community_likes(post_id,user_id) VALUES(?,?)", (pid, uid))
        except IntegrityError:
            c.execute("DELETE FROM community_likes WHERE post_id=? AND user_id=?", (pid, uid))
        c.commit()
    finally:
        c.close()
    return redirect(request.referrer if request.referrer and urlparse_host(request.referrer) == request.host else url_for("community_post", pid=pid))

def urlparse_host(u):
    from urllib.parse import urlparse
    return urlparse(u).netloc

def _report(target_type, target_id, table):
    uid = session["user_id"]; lang = session.get("lang", "en")
    if _rate_hit(("creport", uid), 20, 600): abort(429)
    reason = request.form.get("reason") if request.form.get("reason") in REPORT_REASONS else "other"
    c = db()
    try:
        row = c.execute(f"SELECT id,user_id,status FROM {table} WHERE id=?", (target_id,)).fetchone()
        if not row or row["status"] == "deleted" or (row["status"] == "hidden" and not _is_admin_session()): abort(404)
        if row["user_id"] == uid:
            flash(tr_fast("You cannot report your own content.", lang), "error")
        else:
            try:
                c.execute("INSERT INTO community_reports(user_id,target_type,target_id,reason) VALUES(?,?,?,?)", (uid, target_type, target_id, reason))
                n = c.execute("SELECT COUNT(*) AS n FROM community_reports WHERE target_type=? AND target_id=?", (target_type, target_id)).fetchone()["n"]
                c.execute(f"UPDATE {table} SET report_count=?, status=IF(?>=?,'hidden',status) WHERE id=?", (n, n, REPORT_HIDE_THRESHOLD, target_id))
                c.commit()
                flash(tr_fast("Thank you. Our moderators will review this.", lang), "success")
            except IntegrityError:
                flash(tr_fast("You already reported this.", lang), "error")
    finally:
        c.close()

@app.route("/community/<int:pid>/report", methods=["POST"])
@login_required
def community_report_post(pid):
    _report("post", pid, "community_posts"); return redirect(url_for("community_feed"))

@app.route("/community/comment/<int:cid>/report", methods=["POST"])
@login_required
def community_report_comment(cid):
    _report("comment", cid, "community_comments")
    c = db()
    try: row = c.execute("SELECT post_id FROM community_comments WHERE id=?", (cid,)).fetchone()
    finally: c.close()
    return redirect(url_for("community_post", pid=row["post_id"]) if row else url_for("community_feed"))

def _delete_post_rows(c, pid):
    post = c.execute("SELECT image FROM community_posts WHERE id=?", (pid,)).fetchone()
    cids = [r["id"] for r in c.execute("SELECT id FROM community_comments WHERE post_id=?", (pid,)).fetchall()]
    for cid in cids: c.execute("DELETE FROM community_reports WHERE target_type='comment' AND target_id=?", (cid,))
    c.execute("DELETE FROM community_comments WHERE post_id=?", (pid,))
    c.execute("DELETE FROM community_likes WHERE post_id=?", (pid,))
    c.execute("DELETE FROM community_reports WHERE target_type='post' AND target_id=?", (pid,))
    c.execute("DELETE FROM community_posts WHERE id=?", (pid,))
    if post and post["image"]:
        try: os.remove(os.path.join(UPLOAD_FOLDER, os.path.basename(post["image"])))
        except OSError: pass

@app.route("/community/<int:pid>/delete", methods=["POST"])
@login_required
def community_delete_post(pid):
    c = db()
    try:
        post = c.execute("SELECT user_id FROM community_posts WHERE id=?", (pid,)).fetchone()
        if not post or (post["user_id"] != session["user_id"] and not _is_admin_session()): abort(404)
        _delete_post_rows(c, pid); c.commit()
    finally:
        c.close()
    flash(tr_fast("Post deleted.", session.get("lang", "en")), "success")
    return redirect(url_for("community_feed"))

@app.route("/community/comment/<int:cid>/delete", methods=["POST"])
@login_required
def community_delete_comment(cid):
    c = db()
    try:
        cm = c.execute("SELECT user_id,post_id FROM community_comments WHERE id=?", (cid,)).fetchone()
        if not cm or (cm["user_id"] != session["user_id"] and not _is_admin_session()): abort(404)
        for r in c.execute("SELECT id FROM community_comments WHERE parent_id=?", (cid,)).fetchall():
            c.execute("DELETE FROM community_reports WHERE target_type='comment' AND target_id=?", (r["id"],))
        c.execute("DELETE FROM community_comments WHERE parent_id=?", (cid,))
        c.execute("DELETE FROM community_reports WHERE target_type='comment' AND target_id=?", (cid,))
        c.execute("DELETE FROM community_comments WHERE id=?", (cid,)); c.commit()
        pid = cm["post_id"]
    finally:
        c.close()
    return redirect(url_for("community_post", pid=pid))

@app.route("/admin/community")
@admin_required
def admin_community():
    c = db()
    try:
        posts = c.execute("SELECT p.id,p.title,p.body,p.status,p.report_count,u.name AS author FROM community_posts p JOIN users u ON u.id=p.user_id WHERE p.report_count>0 OR p.status='hidden' ORDER BY p.report_count DESC,p.id DESC LIMIT 100").fetchall()
        comments = c.execute("SELECT cm.id,cm.post_id,cm.body,cm.status,cm.report_count,u.name AS author FROM community_comments cm JOIN users u ON u.id=cm.user_id WHERE cm.report_count>0 OR cm.status='hidden' ORDER BY cm.report_count DESC,cm.id DESC LIMIT 100").fetchall()
    finally:
        c.close()
    return render_template("admin_community.html", posts=posts, comments=comments)

@app.route("/admin/community/<kind>/<int:tid>/<action>", methods=["POST"])
@admin_required
def admin_community_action(kind, tid, action):
    if kind not in ("post", "comment") or action not in ("restore", "delete"): abort(404)
    table = "community_posts" if kind == "post" else "community_comments"
    c = db()
    try:
        if not c.execute(f"SELECT 1 AS x FROM {table} WHERE id=?", (tid,)).fetchone(): abort(404)
        if action == "restore":
            c.execute(f"UPDATE {table} SET status='visible', report_count=0 WHERE id=?", (tid,))
            c.execute("DELETE FROM community_reports WHERE target_type=? AND target_id=?", (kind, tid))
        elif kind == "post":
            _delete_post_rows(c, tid)
        else:
            c.execute("DELETE FROM community_comments WHERE parent_id=?", (tid,))
            c.execute("DELETE FROM community_reports WHERE target_type='comment' AND target_id=?", (tid,))
            c.execute("DELETE FROM community_comments WHERE id=?", (tid,))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("admin_community"))


# ======================= PHASE 5: WEATHER, ESTIMATED DISEASE RISK, EXPLAINABLE AI, IMAGERY-READY =======================
_WEATHER_CACHE = {}
_WEATHER_LOCK = threading.Lock()

def fetch_weather(lat, lon, timeout=8):
    """Current conditions + 3-day forecast from Open-Meteo. Cached 30 min per ~1 km cell. Returns None on any failure (never raises)."""
    import urllib.request, urllib.parse
    key = (round(float(lat), 2), round(float(lon), 2))
    now = time.time()
    with _WEATHER_LOCK:
        hit = _WEATHER_CACHE.get(key)
        if hit and now - hit[0] < 1800: return hit[1]
    try:
        q = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": key[0], "longitude": key[1], "timezone": "auto", "forecast_days": 3,
            "current": "temperature_2m,relative_humidity_2m,precipitation,rain,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max"})
        data = json.loads(urllib.request.urlopen(q, timeout=timeout).read().decode())
        d = data.get("daily") or {}
        daily = [{"date": d["time"][i], "tmax": d["temperature_2m_max"][i], "tmin": d["temperature_2m_min"][i],
                  "rain": d["precipitation_sum"][i], "rain_prob": d.get("precipitation_probability_max", [None] * 9)[i],
                  "wind": d.get("wind_speed_10m_max", [None] * 9)[i]} for i in range(len(d.get("time", [])))]
        out = {"current": data.get("current") or {}, "daily": daily}
        with _WEATHER_LOCK:
            if len(_WEATHER_CACHE) > 500: _WEATHER_CACHE.clear()
            _WEATHER_CACHE[key] = (now, out)
        return out
    except Exception:
        app.logger.warning("Weather fetch failed")
        return None

# crop keyword -> (disease group, min temp, max temp, min humidity %). Transparent rule-of-thumb thresholds, NOT a prediction model.
CROP_RISK_RULES = {
    "tomato": ("late and early blight", 10, 28, 80), "potato": ("late blight", 10, 25, 85), "wheat": ("rust", 10, 25, 75),
    "rice": ("blast", 20, 32, 85), "maize": ("leaf blight", 18, 30, 80), "corn": ("leaf blight", 18, 30, 80),
    "grape": ("downy mildew", 15, 28, 80), "apple": ("apple scab", 8, 24, 80), "cotton": ("bacterial blight and leaf spot", 25, 35, 80),
    "mango": ("anthracnose", 22, 32, 80), "banana": ("sigatoka leaf spot", 24, 32, 85), "chilli": ("anthracnose and leaf spot", 22, 32, 80),
    "pepper": ("anthracnose and leaf spot", 22, 32, 80), "onion": ("purple blotch and downy mildew", 15, 28, 80), "soybean": ("rust and leaf spot", 20, 30, 80),
}
GENERIC_RISK_RULE = ("fungal leaf diseases", 12, 30, 80)

def _crop_rule(crop):
    c = (crop or "").lower()
    for k, v in CROP_RISK_RULES.items():
        if k in c: return v
    return GENERIC_RISK_RULE

def estimate_risks(weather, crop=None):
    """Estimated (never guaranteed) disease risk + weather advisories from a weather dict. Returns a list of dicts."""
    if not weather or not weather.get("current"): return []
    cur, daily = weather["current"], weather.get("daily") or []
    t, rh = cur.get("temperature_2m"), cur.get("relative_humidity_2m")
    out = []
    if t is not None and rh is not None:
        disease, tmin, tmax, rhmin = _crop_rule(crop)
        rain_prob = max([d.get("rain_prob") or 0 for d in daily[:2]] or [0])
        rain_sum = sum((d.get("rain") or 0) for d in daily[:2])
        wet = (cur.get("rain") or 0) > 0 or rain_prob >= 50 or rain_sum >= 2
        points = int(rh >= rhmin) + int(tmin <= t <= tmax) + int(wet)
        level = {3: "high", 2: "moderate"}.get(points, "low")
        out.append({"type": "disease_risk", "level": level, "disease": disease, "crop": crop or None,
                    "basis": {"temperature": t, "humidity": rh, "wet_weather": wet}})
    tmaxs = [d["tmax"] for d in daily if d.get("tmax") is not None]; tmins = [d["tmin"] for d in daily if d.get("tmin") is not None]
    rain3 = sum((d.get("rain") or 0) for d in daily); winds = [d["wind"] for d in daily if d.get("wind") is not None]
    if tmaxs and max(tmaxs) >= 38: out.append({"type": "weather", "code": "heat", "value": round(max(tmaxs))})
    if tmins and min(tmins) <= 2: out.append({"type": "weather", "code": "frost", "value": round(min(tmins))})
    if rain3 >= 25: out.append({"type": "weather", "code": "rain", "value": round(rain3)})
    if winds and max(winds) >= 40: out.append({"type": "weather", "code": "wind", "value": round(max(winds))})
    if tmaxs and max(tmaxs) >= 32 and rain3 < 1: out.append({"type": "weather", "code": "dry", "value": 0})
    return out

def risks_for_display(risks, lang):
    shown = []
    for r in risks:
        if r["type"] == "disease_risk":
            txt = f"{r['disease']}".capitalize()
            shown.append({"type": "disease_risk", "level": r["level"], "text": txt, "crop": r.get("crop")})
        else:
            shown.append({"type": "weather", "level": "advisory", "text": _fill(tr_fast(WEATHER_ADVICE[r["code"]][1], lang), {"value": r["value"]})})
    return shown

@app.route("/api/weather")
def weather():
    import urllib.request, urllib.parse
    if _rate_hit(("weather", request.remote_addr or "?"), 40, 60): return jsonify({"ok": False, "error": "Too many requests. Please try again later."}), 429
    city = request.args.get("city", "").strip()[:100]
    lat = request.args.get("lat"); lon = request.args.get("lon")
    crop = request.args.get("crop", "").strip()[:60] or None
    try:
        if lat and lon:
            qlat, qlon = float(lat), float(lon)
            if not (-90 <= qlat <= 90 and -180 <= qlon <= 180): return jsonify({"ok": False, "error": "Invalid coordinates"}), 400
            place = "Your location"
        elif city:
            q = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": city, "count": 1, "language": "en", "format": "json"})
            data = json.loads(urllib.request.urlopen(q, timeout=8).read().decode())
            if not data.get("results"): return jsonify({"ok": False, "error": "City not found"}), 404
            r = data["results"][0]; qlat, qlon = r["latitude"], r["longitude"]; place = r.get("name", city)
        else:
            return jsonify({"ok": False, "error": "Location or city required"}), 400
        w = fetch_weather(qlat, qlon)
        if not w: return jsonify({"ok": False, "error": "Weather service is temporarily unavailable."}), 502
        lang = session.get("lang", "en")
        return jsonify({"ok": True, "place": place, "current": w["current"], "forecast": w["daily"],
                        "risk": risks_for_display(estimate_risks(w, crop), lang),
                        "risk_note": tr_fast("Estimated from weather conditions only. It does not mean disease will definitely occur.", lang)})
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid coordinates"}), 400
    except Exception:
        app.logger.exception("Weather lookup failed")
        return jsonify({"ok": False, "error": "Weather service is temporarily unavailable."}), 502

def scan_weather_snapshot():
    """Optional weather summary stored with a scan. Only when the browser sent coordinates (user allowed location).
    The coordinates themselves are NEVER stored - only the numbers below."""
    try:
        lat, lon = float(request.form.get("lat", "")), float(request.form.get("lon", ""))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180): return None
    except (TypeError, ValueError):
        return None
    w = fetch_weather(lat, lon, timeout=4)
    if not w: return None
    c = w["current"]
    return json.dumps({"temp": c.get("temperature_2m"), "humidity": c.get("relative_humidity_2m"), "rain": c.get("rain"), "wind": c.get("wind_speed_10m")})

# ---- coarse, opt-in location for weather alerts
def get_location(user_id):
    c = db()
    try:
        r = c.execute("SELECT loc_lat,loc_lon FROM notification_prefs WHERE user_id=?", (user_id,)).fetchone()
    finally:
        c.close()
    return (float(r["loc_lat"]), float(r["loc_lon"])) if r and r["loc_lat"] is not None and r["loc_lon"] is not None else None

@app.route("/notifications/location", methods=["POST"])
@login_required
def notification_location_save():
    try:
        lat, lon = float(request.form.get("lat", "")), float(request.form.get("lon", ""))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180): raise ValueError
    except (TypeError, ValueError):
        flash(tr_fast("Invalid location.", session.get("lang", "en")), "error"); return redirect(url_for("notification_settings"))
    c = db()
    try:   # rounded to 0.1 degree (~11 km): good enough for regional weather, not a precise position
        c.execute("INSERT INTO notification_prefs(user_id,loc_lat,loc_lon) VALUES(?,?,?) ON DUPLICATE KEY UPDATE loc_lat=VALUES(loc_lat),loc_lon=VALUES(loc_lon)",
                  (session["user_id"], round(lat, 1), round(lon, 1)))
        c.commit()
    finally:
        c.close()
    flash(tr_fast("Approximate location saved for weather alerts.", session.get("lang", "en")), "success")
    return redirect(url_for("notification_settings"))

@app.route("/notifications/location/delete", methods=["POST"])
@login_required
def notification_location_delete():
    c = db()
    try:
        c.execute("UPDATE notification_prefs SET loc_lat=NULL,loc_lon=NULL WHERE user_id=?", (session["user_id"],)); c.commit()
    finally:
        c.close()
    flash(tr_fast("Saved location removed.", session.get("lang", "en")), "success")
    return redirect(url_for("notification_settings"))

def _gen_weather_alerts(user_id, prefs):
    loc = get_location(user_id)
    if not loc: return
    w = fetch_weather(*loc)
    if not w: return
    c = db()
    try:
        crops = [r["crop"] for r in c.execute("SELECT DISTINCT crop FROM fields WHERE user_id=? LIMIT 5", (user_id,)).fetchall()]
    finally:
        c.close()
    day = date.today().isoformat()
    seen_weather = set()
    for crop in (crops or [None]):
        for r in estimate_risks(w, crop):
            if r["type"] == "disease_risk" and prefs["disease_risk"] and r["level"] in ("moderate", "high"):
                p = {"level": r["level"], "disease": r["disease"], "crop": crop}
                notify(user_id, "disease_risk" if crop else "disease_risk_generic", p, link="/", dedupe_key=f"dr-{(crop or 'any')[:30]}-{day}")
            elif r["type"] == "weather" and prefs["weather_advisory"] and r["code"] not in seen_weather:
                seen_weather.add(r["code"])
                notify(user_id, WEATHER_ADVICE[r["code"]][0], {"value": r["value"]}, link="/", dedupe_key=f"{r['code']}-{day}")

# ---- Explainable AI: occlusion-sensitivity heatmap (works with the shipped TFLite model; no gradients needed)
EXPLAINERS = {}   # extension point: name -> callable(path, class_idx) ; a Grad-CAM explainer can be registered here when a Keras model file is shipped

def occlusion_heatmap(path, class_idx=None, grid=7):
    """Slide a neutral patch over the leaf and measure how much the predicted-class probability drops.
    Returns {"heat": HxW float 0..1, "focused": bool, "class_idx": int, "confidence": float, "image": PIL image}."""
    im = Image.open(path).convert("RGB"); im.thumbnail((512, 512))
    base = raw_predict(_prepare_pil(im))
    idx = int(np.argmax(base)) if class_idx is None else int(class_idx)
    p0 = float(base[idx]); W, H = im.size; pw, ph = max(8, W // 3), max(8, H // 3)
    arr = np.asarray(im).copy(); fill = arr.reshape(-1, 3).mean(0).astype(np.uint8)
    xs = sorted(set(list(range(0, W - pw + 1, max(1, (W - pw) // (grid - 1)))) + [W - pw]))
    ys = sorted(set(list(range(0, H - ph + 1, max(1, (H - ph) // (grid - 1)))) + [H - ph]))
    heat = np.zeros((H, W), np.float32); cnt = np.zeros((H, W), np.float32)
    for y in ys:
        for x in xs:
            occ = arr.copy(); occ[y:y + ph, x:x + pw] = fill
            drop = max(0.0, p0 - float(raw_predict(_prepare_pil(Image.fromarray(occ)))[idx]))
            heat[y:y + ph, x:x + pw] += drop; cnt[y:y + ph, x:x + pw] += 1
    heat /= np.maximum(cnt, 1); m = float(heat.max())
    return {"heat": heat / m if m > 0 else heat, "focused": m >= 0.05, "class_idx": idx, "confidence": p0, "image": im}

def heatmap_overlay(result):
    n = result["heat"]; base = np.asarray(result["image"], dtype=np.float32)
    col = np.stack([np.clip(3 * n, 0, 1), np.clip(3 * n - 1, 0, 1), np.clip(3 * n - 2, 0, 1)], -1) * 255
    a = (0.65 * n)[..., None] if result["focused"] else np.zeros_like(n)[..., None]
    return Image.fromarray(np.clip(base * (1 - a) + col * a, 0, 255).astype(np.uint8))
EXPLAINERS["occlusion"] = occlusion_heatmap

@app.route("/history/<int:sid>/explain", methods=["POST"])
@login_required
def scan_explain(sid):
    row = _load_scan_or_404(sid); lang = session.get("lang", "en")
    if _rate_hit(("explain", session["user_id"]), 10, 60): return _error_response(429, "Too many requests. Please try again later.")
    path = os.path.join(UPLOAD_FOLDER, os.path.basename(row["image"] or ""))
    if row["status"] != "Local TFLite" or row["disease"] not in labels or not os.path.isfile(path) or interpreter is None:
        flash(tr_fast("An explanation is only available for scans analysed by the built-in disease model.", lang), "error")
        return redirect(url_for("scan_detail", sid=sid))
    try:
        res = EXPLAINERS["occlusion"](path, labels.index(row["disease"]))
        name = f"u{session['user_id']}_explain_{sid}.jpg"
        heatmap_overlay(res).save(os.path.join(UPLOAD_FOLDER, name), "JPEG", quality=85)
        c = db()
        try:
            c.execute("UPDATE scans SET explain_image=? WHERE id=? AND user_id=?", (name, sid, session["user_id"])); c.commit()
        finally:
            c.close()
        if not res["focused"]: flash(tr_fast("No single region dominated this prediction.", lang), "success")
    except Exception:
        app.logger.exception("Explanation failed")
        flash(tr_fast("Could not create the explanation for this scan.", lang), "error")
    return redirect(url_for("scan_detail", sid=sid))

# ---- Satellite / drone readiness (architecture only: NO data is ever fabricated)
class ImageryProvider:
    """Interface a real satellite/drone integration (Sentinel Hub, Planet, a drone upload pipeline ...) will implement."""
    name = "none"; configured = False
    def vegetation_index(self, field): raise NotImplementedError      # e.g. NDVI raster / statistics
    def crop_stress_map(self, field): raise NotImplementedError       # stress heatmap for the field boundary
    def affected_area(self, field): raise NotImplementedError         # % area affected, polygons

class NullImageryProvider(ImageryProvider):
    pass

IMAGERY_PROVIDERS = {"none": NullImageryProvider}   # register real providers here: IMAGERY_PROVIDERS["sentinel"] = SentinelProvider

def get_imagery_provider():
    return IMAGERY_PROVIDERS.get(os.environ.get("IMAGERY_PROVIDER", "none").strip().lower(), NullImageryProvider)()

IMAGERY_NOT_CONFIGURED = "Satellite/drone analysis is not configured yet."

@app.route("/fields/<int:fid>/imagery")
@login_required
def field_imagery(fid):
    f = get_field_or_404(fid)
    return render_template("field_imagery.html", f=f, provider=get_imagery_provider())

@app.route("/api/fields/<int:fid>/imagery")
@login_required
def field_imagery_api(fid):
    get_field_or_404(fid)
    prov = get_imagery_provider()
    if not prov.configured:
        return jsonify({"ok": False, "configured": False, "message": IMAGERY_NOT_CONFIGURED}), 501
    return jsonify({"ok": False, "configured": True, "message": "Provider is registered but has no data for this field yet."}), 501


if __name__=="__main__":
    init_db()
    print("RBAgriScan | model:",os.path.exists(MODEL_PATH),"labels:",len(labels),"Gemini:",GEMINI_ENABLED,"model:",GEMINI_MODEL)
    # Werkzeug's debugger allows remote code execution: it is OFF unless FLASK_DEBUG=1,
    # and even then it only listens on localhost unless HOST is set explicitly.
    app.run(debug=os.environ.get("FLASK_DEBUG","0")=="1", host="0.0.0.0", port=int(os.environ.get("PORT","5000")))
