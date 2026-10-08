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

# ---------------- AUTH ----------------
c=client()
r=c.get("/"); check("unauth / is the public landing page", r.status_code==200 and "<h1>" in r.get_data(as_text=True)); r=c.get("/dashboard"); check("unauth /dashboard redirects to login", r.status_code==302 and "/login" in r.headers["Location"])
r=c.post("/login",data={"email":"a@t.test","password":"x"}); check("POST without CSRF -> 403 page", r.status_code==403 and "<h1>" in r.get_data(as_text=True))
r=register(c,"a@t.test","short"); check("weak password rejected (no redirect)", r.status_code==200)
r=register(c,"a@t.test"); check("register ok", r.status_code==302)
r=c.get("/"); check("home after register 200", r.status_code==200)
c.get("/logout"); r=c.get("/dashboard"); check("logout works", r.status_code==302)
r=post(c,"/login",{"email":"a@t.test","password":"wrong"}); check("bad login stays on page", r.status_code==200)
for nxt in ("//evil.com","/\\evil.com","https://evil.com","javascript:alert(1)","/\\\\evil.com"):
    r=post(c,"/login",{"email":"a@t.test","password":"Password123","next":nxt})
    loc=r.headers.get("Location","")
    check(f"open redirect blocked {nxt!r}", r.status_code==302 and loc.startswith("/") and not loc.startswith("//") and "evil" not in loc, loc)
    c.get("/logout")
r=post(c,"/login",{"email":"a@t.test","password":"Password123","next":"/dashboard"}); check("safe next honoured", r.headers.get("Location")=="/dashboard")

# ---------------- ERROR PAGES ----------------
r=c.get("/nope"); check("404 page no traceback", r.status_code==404 and "Traceback" not in r.get_data(as_text=True))
r=c.get("/api/nope"); check("404 api json", r.status_code==404 and r.is_json)
r=c.get("/media/../app.py"); check("media traversal blocked", r.status_code in (404,308))
r=c.put("/dashboard"); check("405 page", r.status_code==405)
big=io.BytesIO(b"0"*(9*1024*1024))
t=token(c); r=c.post("/",data={"_csrf":t,"image":(big,"x.png")},content_type="multipart/form-data"); check("413 oversized upload", r.status_code==413)
r=c.get("/api/health"); check("health minimal", r.get_json()=={"ok":True,"service":"RBAgriScan"})
r=c.get("/api/plantnet-test"); check("plantnet-test not public", r.status_code in (302,403))
r=c.get("/api/weather?lat=999&lon=0"); check("weather invalid coords 400", r.status_code==400)
r=client().post("/api/chat",json={"message":"hi"}); check("chat requires login (CSRF or 401)", r.status_code in (401,403))
r=c.get("/robots.txt"); check("robots.txt", "Sitemap" in r.get_data(as_text=True))
r=c.get("/sitemap.xml"); check("sitemap.xml", "<urlset" in r.get_data(as_text=True))

# ---------------- PIPELINE (validators mocked; local model result mocked deterministically) ----------------
def run_scan(c, gate, local, gemini=None, cam=False):
    A.validate_plant_image=lambda path,lang="en": gate
    A.predict_strict=lambda path: local
    A.GEMINI_ENABLED = gemini is not None
    if gemini is not None: A.gemini_detect=lambda path,lang: gemini
    t=token(c)
    if cam:
        r=c.post("/detect-camera",data={"image":(png(),"cam.jpg")},headers={"X-CSRF-Token":t},content_type="multipart/form-data"); return r.get_json()
    r=c.post("/",data={"_csrf":t,"image":(png(),"leaf.png")},content_type="multipart/form-data"); return r

TOM_OK={"class":"Tomato___Late_blight","confidence":97.0,"margin":90,"stable":100,"accepted":True,"plant":"Tomato","plant_key":"Tomato","supported":True}
gate_tomato={"ok":True,"provider":"plantnet","supported_crop":"Tomato","plant":"Tomato","scientific_name":"Solanum lycopersicum","confidence":88,"common_names":["Tomato"]}
gate_mango={"ok":True,"provider":"plantnet","supported_crop":"Mango","plant":"Mango","scientific_name":"Mangifera indica","confidence":81,"common_names":["Mango"]}
gate_sweetpot={"ok":True,"provider":"plantnet","supported_crop":"Sweet potato","plant":"Sweet potato","scientific_name":"Ipomoea batatas","confidence":70,"common_names":["Sweet potato"]}
gate_none={"ok":False,"reason":"not_a_plant","plant":"Unknown","confidence":0}

r=run_scan(c,gate_tomato,TOM_OK,cam=True)
check("camera: tomato supported -> local result", r["ok"] and not r["unknown"] and r["class"]=="Tomato___Late_blight" and r["guidance"] and r["scan_id"], r)
r=run_scan(c,gate_mango,TOM_OK,cam=True)
check("camera: MANGO is NOT forced into Tomato disease", r["ok"] and "Tomato" not in str(r["class"]) and r["class"]=="Disease analysis unavailable for this plant" and r["plant"]=="Mango", r)
r=run_scan(c,gate_sweetpot,{**TOM_OK,"class":"Potato___Late_blight","plant":"Potato","plant_key":"Potato"},cam=True)
check("camera: sweet potato not treated as Potato", "Potato___" not in str(r["class"]), r)
r=run_scan(c,gate_none,TOM_OK,cam=True)
check("camera: non-plant -> Plant Not Identified, no disease, conf 0", r["unknown"] and r["confidence"]==0 and "Not Identified" in r["message"], r)
r=run_scan(c,{"ok":False,"reason":"validator_unavailable"},TOM_OK,cam=True)
check("camera: validator down -> rejected, not forced", r["unknown"] and "Tomato" not in json.dumps(r), r)
LOWC={**TOM_OK,"confidence":41.0,"accepted":False}
r=run_scan(c,gate_tomato,LOWC,cam=True)
check("camera: low confidence -> honest message", r["low_confidence"] and "could not be confidently" in r["message"] and not r.get("guidance"), r)
r=run_scan(c,gate_mango,TOM_OK,gemini={"plant":"Mango","disease":"Anthracnose","confidence":72,"explanation":"x","home_remedies":["a"],"natural":[],"field":[],"chemical":[],"prevention":["p"]},cam=True)
check("camera: unsupported plant -> Gemini fallback w/ guidance, plant from validator", r["plant"]=="Mango" and r["class"]=="Anthracnose" and r["guidance"]["prevention"]==["p"], r)
# upload route = same pipeline
r=run_scan(c,gate_tomato,TOM_OK); h=r.get_data(as_text=True)
check("upload: tomato result page", r.status_code==200 and "Late_blight" in h and 'id="precautions"' in h)
r=run_scan(c,gate_mango,TOM_OK); h=r.get_data(as_text=True)
check("upload: mango not Tomato", r.status_code==200 and "Tomato___" not in h)
r=run_scan(c,gate_none,TOM_OK); h=r.get_data(as_text=True)
check("upload: non-plant message shown", r.status_code==200 and "Plant Not Identified" in h)
cn=A.db(); n=cn.execute("SELECT COUNT(*) AS n FROM scans").fetchone()["n"]; rows=cn.execute("SELECT plant,disease FROM scans").fetchall(); cn.close()
check("non-plant/validator-failed scans never stored", all("Unknown" != x["plant"] for x in rows) and n>=5, rows)

# ---------------- IDOR ----------------
cn=A.db(); scan=cn.execute("SELECT id,image FROM scans ORDER BY id LIMIT 1").fetchone(); cn.close()
img=scan["image"]
r=c.get("/media/"+img); check("owner can load own image", r.status_code==200 and r.headers["Cache-Control"].startswith("private"))
r=c.get("/static/uploads/"+img); check("direct /static/uploads closed", r.status_code==404)
b=client(); register(b,"b@t.test")
r=b.get("/media/"+img); check("other user cannot load image (IDOR)", r.status_code==404)
r=client().get("/media/"+img); check("anonymous cannot load image", r.status_code==302)
# ticket: B tries to attach A's scan
rb=post(b,"/experts/tickets",{"notes":"help","scan_id":str(scan["id"])}); 
cn=A.db(); tk=cn.execute("SELECT COUNT(*) AS n FROM diagnostic_tickets").fetchone()["n"]; cn.close()
check("ticket cannot attach someone else's scan", tk==0, tk)
ra=post(c,"/experts/tickets",{"notes":"my leaf","scan_id":str(scan["id"]),"lat":"abc","lon":"500"})
cn=A.db(); t=cn.execute("SELECT id,lat,lon FROM diagnostic_tickets").fetchone(); cn.close()
check("ticket created with owner scan; bad lat/lon dropped", t and t["lat"] is None and t["lon"] is None, t)
r=b.get(f"/experts/tickets/{t['id']}"); check("other farmer cannot open ticket", r.status_code==302)
r=b.get("/media/"+img); check("ticket image still private for other farmers", r.status_code==404)
# expert/admin access
adm=client(); post(adm,"/login",{"email":"admin@example.test","password":"AdminPass#12345"})
r=adm.get(f"/experts/tickets/{t['id']}"); check("admin opens ticket", r.status_code==200)
r=adm.get("/media/"+img); check("admin/expert can view ticket image", r.status_code==200)
r=adm.get("/api/plantnet-test"); check("admin may call plantnet-test (400 = not configured)", r.status_code==400)
# IoT crop link IDOR
cn=A.db(); cn.execute("INSERT INTO crop_logs(user_id,crop_key,nickname,planted_on) SELECT id,'Tomato','A-field',CURDATE() FROM users WHERE email='a@t.test'"); cn.commit()
clog=cn.execute("SELECT id FROM crop_logs LIMIT 1").fetchone()["id"]; cn.close()
post(b,"/iot/devices/new",{"name":"evil","crop_log_id":str(clog)})
cn=A.db(); d=cn.execute("SELECT COUNT(*) AS n FROM sensor_devices").fetchone()["n"]; cn.close()
check("IoT device cannot link to someone else's crop", d==0, d)
post(c,"/iot/devices/new",{"name":"mine","crop_log_id":str(clog)})
cn=A.db(); dev=cn.execute("SELECT id,device_key FROM sensor_devices").fetchone(); cn.close()
check("IoT device links to own crop", dev is not None)
r=b.get(f"/api/iot/readings/{dev['id']}"); check("IoT readings IDOR blocked", r.status_code==404)
# no fabricated sensor data
r=c.get(f"/api/iot/readings/{dev['id']}"); check("no sensor data -> empty list (not fabricated)", r.get_json()["readings"]==[])
r=A.app.test_client().post("/api/iot/ingest",json={"device_key":dev["device_key"],"soil_moisture":150}); check("IoT ingest validates range", r.status_code==400)

# ---------------- AUTH THROTTLE ----------------
z=client(); codes=[]
for i in range(14): codes.append(post(z,"/login",{"email":"a@t.test","password":"bad%d"%i}).status_code)
check("login brute-force throttled with 429", 429 in codes, codes)

# ---------------- LANGUAGES ----------------
import time
langs=["en","hi","mr","gu","ta","te","bn","ar","fr","es","de","ja","zh-CN"]
L=client(); register(L,"l@t.test")
en_home=A.TEXTS["en"]["home"]
for lg in langs:
    t0=time.time()
    r=L.get("/set-language/"+lg, headers={"Referer":"http://localhost/dashboard"})
    check(f"language {lg}: switch redirects back to the same page", r.status_code==302 and r.headers["Location"]=="/dashboard", r.headers.get("Location"))
    r=L.get("/dashboard"); h=r.get_data(as_text=True); dt=time.time()-t0
    ok = r.status_code==200 and f'lang="{lg}"' in h
    if lg=="ar": ok = ok and 'dir="rtl"' in h
    check(f"language {lg}: renders, html lang set, persists ({dt:.1f}s)", ok and dt<5, dt)
    if lg in ("hi","mr","bn","ar"):
        check(f"language {lg}: bundled translation really shown (no English nav 'Home')", f">\U0001F3E0 {en_home}<" not in h and f"\U0001F3E0 {en_home}" not in h)
    if lg in ("gu","ta","te","fr","es","de","ja","zh-CN"):
        print("INFO", lg, "has no bundled translation -> English fallback unless the live translator is reachable:", f"\U0001F3E0 {en_home}" in h)
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
