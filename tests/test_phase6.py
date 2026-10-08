import os, sys, io, re, json
import tempfile
TMP = tempfile.gettempdir()
for _k, _v in dict(MYSQL_HOST="127.0.0.1", MYSQL_PORT="3306", MYSQL_USER="root", MYSQL_PASSWORD="", MYSQL_DATABASE="smart_crop_ai_test").items():
    os.environ.setdefault(_k, _v)   # override with environment variables
if not os.environ["MYSQL_DATABASE"].endswith("_test"):
    sys.exit("Refusing to run: these tests DELETE data. Set MYSQL_DATABASE to a dedicated empty test database whose name ends with _test.")
os.environ.update(SECRET_KEY="test-secret-key-for-tests-only", ADMIN_EMAIL="admin@example.test", ADMIN_PASSWORD="AdminPass#12345",
                  APP_RATE_LIMIT_PER_MINUTE="1000")
os.environ.pop("PLANTNET_API_KEY", None); os.environ.pop("GEMINI_API_KEY", None)
sys.path.insert(0, os.getcwd())
import numpy as np
from PIL import Image
import app as A

PASS=FAIL=0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS+=1; print("PASS", name)
    else: FAIL+=1; print("FAIL", name, extra)

def png(color=(60,140,60), size=(400,400), noise=True):
    arr=np.zeros((size[1],size[0],3),dtype=np.uint8); arr[:]=color
    if noise: arr=np.clip(arr.astype(int)+np.random.randint(-40,40,arr.shape),0,255).astype(np.uint8)
    b=io.BytesIO(); Image.fromarray(arr).save(b,"PNG"); b.seek(0); return b

def client():
    c=A.app.test_client(); return c
def token(c):
    r=c.get("/login"); m=re.search(r'name="csrf-token" content="([^"]+)"', r.get_data(as_text=True)); return m.group(1)
def post(c,url,data=None,**kw):
    t=token(c); data=dict(data or {}); data["_csrf"]=t
    return c.post(url,data=data,**kw)
def register(c,email,pw="Password123"):
    return post(c,"/register",{"name":"T "+email.split("@")[0],"email":email,"password":pw})

A.app.config["TESTING"]=False  # keep production error handlers active
# --- clean DB
cn=A.db(); 
for t in ("ticket_messages","diagnostic_tickets","sensor_readings","sensor_alerts","sensor_devices","care_reminders","crop_stage_events","crop_logs","scans"): cn.execute("DELETE FROM "+t)
cn.execute("DELETE FROM users WHERE email LIKE ?",("%@t.test",)); cn.commit(); cn.close()


import time, json, subprocess, shutil
APP=os.path.dirname(A.__file__)
cn=A.db()
for t in ("community_reports","community_likes","community_comments","community_posts","notifications","notification_prefs","ticket_messages","diagnostic_tickets","sensor_devices","scans","fields"): cn.execute("DELETE FROM "+t)
cn.execute("DELETE FROM users WHERE email LIKE ?",("%@t.test",)); cn.commit(); cn.close()
rd=lambda p: open(os.path.join(APP,p),encoding="utf-8").read()
user=client(); register(user,"nav@t.test"); adm=client(); post(adm,"/login",{"email":"admin@example.test","password":"AdminPass#12345"}); anon=client()
def nav_of(h): return h[h.index('<nav class="rbnav"'):h.index('</nav>',h.index('<nav class="rbnav"'))]
def hrefs(h): return set(re.findall(r'href="([^"]+)"',h))
check("no .env file ships inside the project folder", not os.path.exists(os.path.join(APP,".env")))

# ---------- NAVBAR ----------
h=user.get("/").get_data(as_text=True); nav=nav_of(h); main=nav[nav.index('rbnav-main'):nav.index('rbnav-more')]
check("navbar: only 5 important links are visible (+ Features)", len(re.findall(r"<a ",main))==5, len(re.findall(r"<a ",main)))
for label in ("Scan Plant","Voice Assistant","Weather","My Fields","Expert Help"): check(f"navbar primary: {label}", label in main)
check("navbar: Features dropdown exists", 'id="rbnavMoreBtn"' in nav and "Features" in nav and 'id="rbnavDrop"' in nav)
drop=nav[nav.index('id="rbnavDrop"'):nav.index('rbnav-right')]
need=["/history","/crops","/iot","/dashboard","/community","/supported-plants","/suggestions","/feedback","/contact","/about"]
check("ALL other features are inside the Features dropdown", all(any(x==u or u.startswith(x) for u in hrefs(drop)) for x in need), [x for x in need if not any(u.startswith(x) for u in hrefs(drop))])
every=set(re.findall(r'href="([^"#]+)',nav))
allf=["/","/fields","/experts/tickets","/history","/crops","/iot","/dashboard","/community","/notifications","/supported-plants","/suggestions","/feedback","/contact","/about","/logout"]
check("no feature was removed from the navigation (old 21-link set is all reachable)", all(x in every for x in allf), [x for x in allf if x not in every])
check("language dropdown removed from navbar", "<select" not in nav and 'languageSwitcher' not in nav)
check("Google Translate widget stays in navbar", 'id="google_translate_element"' in nav and "Google Translate" in nav)
check("logout + notification bell present when logged in", "/logout" in nav and 'id="notifBadge"' in nav)
check("non-admin sees no admin links", "/admin" not in nav)
check("admin sees Admin + Moderation inside Features", all(x in nav_of(adm.get("/").get_data(as_text=True)) for x in ("/admin","/admin/community")))
na=nav_of(anon.get("/login").get_data(as_text=True))
check("visitor navbar: Login + Create account, no Scan/Voice", "/login" in na and "/register" in na and "Voice Assistant" not in na and 'id="notifBadge"' not in na and "google_translate_element" in na)
check("old nav classes gone (no .nav-lang select anywhere in layout)", 'class="nav-lang"' not in h)
check("language prefetch (6 background translation calls per page) removed", "prefetchLanguage(code)" not in h)
check("bottom tab bar only for logged-in users", 'class="rbbottom"' in h and 'class="rbbottom"' not in anon.get("/").get_data(as_text=True))

# ---------- FARMER-FIRST HOME ----------
check("home: 6 big tiles", h.count('class="qs-tile')==6)
for href in ("#plant-detection","#assistant","#weather-section","/fields","/experts/tickets","/crops"): check(f"tile -> {href}", f'class="qs-tile' in h and f'href="{href}"' in h or f'href="{href}"' in h)
check("home: 3-step guide + 'see all features'", h.count("<li><i>")==3 and "data-open-features" in h)
check("home: welcome card with language buttons (Hindi, Marathi, Gujarati, Tamil, Telugu, English)", 'id="welcomeCard"' in h and h.count("data-gt=")==6)
check("visitors see the public landing (no tiles)", 'class="qs-tile' not in anon.get("/").get_data(as_text=True))

# ---------- VOICE ASSISTANT: markup ----------
for el in ("vaMic","vaLang","vaStatus","chatBox","vaChips","vaApps","chatForm","chatInput","chatAttach","chatCameraBtn","chatCameraInput","chatSpeak"): check(f"assistant element #{el}", f'id="{el}"' in h)
check("assistant is called Voice Assistant (not 'Assistant')", "Voice Assistant" in h)
check("voice replies ON by default", re.search(r'id="chatSpeak"[^>]*checked',h) is not None)
check("assistant: language picker is inside the assistant (speaking language)", 'id="vaLang"' in h)
js=rd("static/js/app.js"); vc=rd("static/js/voice_commands.js")
blk=js[js.index("Voice Assistant: speak"):js.index("AI Soil & Irrigation Photo")]
# ---------- VOICE ASSISTANT: behaviour (static checks; real mic/speaker need a browser) ----------
check("speaks answers automatically (speakReply after every answer)", blk.count("speakReply(")>=6 and 'localStorage.getItem("rb_voice_replies") !== "0"' in blk)
check("iOS speech unlock inside the user gesture", "unlockSpeech()" in blk and "function unlockSpeech" in blk)
check("TTS picks a voice for the chosen language + Marathi->Hindi fallback", "pickVoice" in blk and 'mr: "hi-IN"' in blk)
check("long answers are spoken in sentence-sized chunks", "speechChunks" in blk)
check("speech recognition with live transcript + permission/no-speech/network errors", all(x in blk for x in ("interimResults = true","not-allowed","no-speech",'"network"',"voice_denied")))
check("typed fallback when voice unsupported", "voice_unsupported" in blk and "input.focus()" in blk)
check("app commands: ChatGPT/Gemini/Google/Gmail/YouTube/WhatsApp/Maps links", all(k in blk for k in ("https://chatgpt.com/","https://gemini.google.com/app","https://www.google.com/","https://mail.google.com/","https://www.youtube.com/","https://wa.me/","https://www.google.com/maps")))
check("one-tap fallback card + always-visible app buttons (popup blockers)", "showAppCard" in blk and "va-app" in blk)
check("'Ask ChatGPT' button on answers and on errors, question prefilled", blk.count("chatGPTUrl(q)")>=1 and "https://chatgpt.com/?q=" in blk and 'addBot(escapeHtml(m), m, q, "va-err")' in blk)
check("voice command parser loaded before app.js", h.index("voice_commands.js")<h.index("js/app.js"))
check("assistant sends the spoken language to the server", 'fd.append("language", langCode())' in blk and "language: langCode()" in blk)
# camera fix
cam=blk[blk.index("function openCameraModal"):blk.index("function initChat")]
check("assistant camera: live getUserMedia popup (laptop + phone), back camera first, flip, retake", all(x in cam for x in ("getUserMedia",'facingMode: { ideal: facing }',"data-flip","data-retake","data-use")))
check("assistant camera: permission denied message + gallery fallback", "Camera permission is required. You can upload an image instead." in cam and "data-pick" in cam and "NotAllowedError" in cam)
check("assistant camera: stream stopped on close/Escape (no stuck camera light)", "getTracks().forEach" in cam and "keydown" in cam)
check("assistant camera: falls back to phone camera input when getUserMedia is missing (http)", "fallbackInput.click()" in cam)
check("photo kept in a JS variable (old DataTransfer hack removed)", "DataTransfer" not in blk and "photoFile" in blk)

# ---------- parser (Node) ----------
node=shutil.which("node")
if node:
    cases=[("open ChatGPT","external","chatgpt"),("chatgpt kholo","external","chatgpt"),("चैटजीपीटी खोलो","external","chatgpt"),("Gmail kholo","external","gmail"),("जीमेल खोलो","external","gmail"),
     ("open my email","external","gmail"),("Google kholo","external","google"),("गूगल खोलो","external","google"),("open google maps","external","maps"),("youtube chalao","external","youtube"),
     ("WhatsApp kholo","external","whatsapp"),("व्हाट्सएप खोलो","external","whatsapp"),("weather dikhao","internal","weather"),("mausam","internal","weather"),("मौसम दिखाओ","internal","weather"),
     ("scan plant","internal","scan"),("scan history","internal","history"),("open my fields","internal","fields"),("khet dikhao","internal","fields"),("community kholo","internal","community"),
     ("plant hospital kholo","internal","expert"),("my crops","internal","crops"),("sensor dikhao","internal","iot"),("dashboard","internal","dashboard"),
     ("mere tamatar ke patte peele ho rahe hain google par dekha par samajh nahi aaya","none",None),("how can I protect my tomato from google blight in rainy season","none",None),
     ("tamatar me kaun si dawa dalu","none",None),("hello","none",None),("","none",None)]
    script="const P=require(process.argv[1]).parse;const c=JSON.parse(process.argv[2]);const bad=[];for(const [t,ty,k] of c){const r=P(t);if(!(r.type===ty&&(k===null||r.key===k)))bad.push([t,r]);}"+\
           "const a=P('ask chatgpt how to save tomato from blight');if(!(a.type==='chatgpt_ask'&&/save tomato/.test(a.query)))bad.push(['ask',a]);"+\
           "const b=P('chatgpt se pucho tamatar ki bimari');if(!(b.type==='chatgpt_ask'&&/tamatar/.test(b.query)))bad.push(['ask2',b]);console.log(JSON.stringify(bad));"
    out=subprocess.run([node,"-e",script,os.path.join(APP,"static/js/voice_commands.js"),json.dumps(cases)],capture_output=True,text=True).stdout.strip()
    check(f"voice command parser: {len(cases)+2} English/Hindi/Hinglish/Devanagari phrases (incl. long questions NOT hijacked)", out=="[]", out)
    for f in ("static/js/app.js","static/js/voice_commands.js","static/sw.js"):
        check(f"{f} passes node --check", subprocess.run([node,"--check",os.path.join(APP,f)],capture_output=True).returncode==0)
else:
    print("INFO node not found: parser tests skipped")

# ---------- server: language chosen in the assistant ----------
A.GEMINI_ENABLED=True; seen=[]
A.gemini_chat=lambda message,lang,history: (seen.append(lang), "ok answer")[1]
r=user.post("/api/chat",json={"message":"my wheat has brown tips","language":"hi"},headers={"X-CSRF-Token":token(user)}); check("api chat: language from the assistant (hi) is used while page language is en", r.get_json()["ok"] and seen[-1]=="hi", seen)
user.post("/api/chat",json={"message":"my wheat has brown tips","language":"zz"},headers={"X-CSRF-Token":token(user)}); check("api chat: invalid language ignored -> page language", seen[-1]=="en", seen)
user.post("/api/chat",json={"message":"my wheat has brown tips"},headers={"X-CSRF-Token":token(user)}); check("api chat: no language -> page language", seen[-1]=="en")
prompts=[]; A._gemini_text=lambda prompt,image_path=None:(prompts.append(prompt),"photo answer")[1]
A.validate_plant_image=lambda p,l="en": {"ok":True,"provider":"plantnet","supported_crop":"Tomato","plant":"Tomato","scientific_name":"Solanum lycopersicum","confidence":88,"common_names":["Tomato"]}
r=user.post("/api/chat",data={"message":"what is this","image":(png(),"x.png"),"language":"ta"},headers={"X-CSRF-Token":token(user)},content_type="multipart/form-data")
check("api chat photo: spoken language (Tamil) used in the AI prompt", r.get_json()["ok"] and prompts and "Tamil" in prompts[-1], prompts[-1][:100] if prompts else None)

# ---------- result page: Listen / Do this first / expert ----------
A.validate_plant_image=lambda p,l="en": {"ok":True,"provider":"plantnet","supported_crop":"Tomato","plant":"Tomato","scientific_name":"Solanum lycopersicum","confidence":88,"common_names":["Tomato"]}
A.predict_strict=lambda p: {"class":"Tomato___Late_blight","confidence":97.0,"margin":90,"stable":100,"accepted":True,"plant":"Tomato","plant_key":"Tomato","supported":True}; A.GEMINI_ENABLED=False
rh=user.post("/",data={"_csrf":token(user),"image":(png(),"l.png")},content_type="multipart/form-data").get_data(as_text=True)
check("scan result: Listen button reads result + guidance aloud", 'data-listen="#plant-result,#precautions"' in rh)
check("scan result: 'Do this first' shows 3 real steps from the guidance", "Do this first" in rh and (lambda i: rh[i:rh.index("</ol>",i)].count("<li>")==3)(rh.index('class="firststeps"')))
check("scan result: 'Not sure? Ask an expert' links to Plant Hospital", "Not sure? Ask an expert" in rh and "/experts/tickets" in rh)
A.validate_plant_image=lambda p,l="en": {"ok":False,"reason":"not_a_plant"}
rn=user.post("/",data={"_csrf":token(user),"image":(png(),"l.png")},content_type="multipart/form-data").get_data(as_text=True)
check("non-plant result: no Listen / Do-this-first (nothing invented)", "Do this first" not in rn and 'data-listen="#plant-result' not in rn)
check("camera result JS also adds Do this first + Listen + expert", all(x in js for x in ("renderFirstSteps(d.guidance)",'data-listen="#cameraDetectionResult"',"/experts/tickets")))
check("Listen reads the text as translated on screen (works with Google Translate)", 'cloneNode(true)' in js and "RB_speechTag" in js and "googtrans" in js)
check("welcome language buttons drive Google Translate cookie", 'googtrans=' in js and "setGoogleLang" in js)
check("service worker caches the parser + new version", "/static/js/voice_commands.js" in rd("static/sw.js") and "voice-nav" in rd("static/sw.js"))
check("static parser served", user.get("/static/js/voice_commands.js").status_code==200)
print(f"\nRESULT: {PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
