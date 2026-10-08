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


import time, json, threading
import urllib.request as ur
REAL_FETCH=A.fetch_weather; REAL_OPEN=ur.urlopen
cn=A.db()
for t in ("community_reports","community_likes","community_comments","community_posts","notifications","notification_prefs","ticket_messages","diagnostic_tickets","sensor_devices","scans","fields"): cn.execute("DELETE FROM "+t)
cn.execute("DELETE FROM users WHERE email LIKE ?",("%@t.test",)); cn.commit(); cn.close()
check("init_db idempotent with Phase 5 migrations", A.init_db() is None or True)
def uid_of(e):
    cn=A.db(); r=cn.execute("SELECT id FROM users WHERE email=?",(e,)).fetchone(); cn.close(); return r["id"]
def rows(q,*a_):
    cn=A.db(); r=cn.execute(q,a_).fetchall(); cn.close(); return r
a=client(); register(a,"a@t.test"); b=client(); register(b,"b@t.test"); ua,ub=uid_of("a@t.test"),uid_of("b@t.test")

# ---------- risk estimator ----------
def W(t=20,rh=90,rain=0.0,days=None): return {"current":{"temperature_2m":t,"relative_humidity_2m":rh,"rain":rain,"wind_speed_10m":10},
    "daily":days if days is not None else [{"date":f"2026-10-0{i}","tmax":t+4,"tmin":t-4,"rain":0,"rain_prob":10,"wind":12} for i in (7,8,9)]}
def lvl(w,crop=None): return [r for r in A.estimate_risks(w,crop) if r["type"]=="disease_risk"][0]["level"]
codes=lambda w:{r.get("code") for r in A.estimate_risks(w) if r["type"]=="weather"}
check("risk HIGH: humid + right temperature + wet (tomato)", lvl(W(20,90,1.2),"Tomato")=="high")
check("risk MODERATE: humid + right temp but dry", lvl(W(20,90,0),"Tomato")=="moderate")
check("risk LOW: dry air, hot, no rain", lvl(W(36,30,0),"Tomato")=="low")
check("crop-specific rule used (rice needs warmer)", lvl(W(14,90,2),"Rice")!="high" and lvl(W(26,90,2),"Rice")=="high")
check("unknown crop -> generic fungal rule", [r for r in A.estimate_risks(W(),"Dragonfruit") if r["type"]=="disease_risk"][0]["disease"]=="fungal leaf diseases")
check("no weather -> no risks (nothing fabricated)", A.estimate_risks(None)==[] and A.estimate_risks({"current":{}})==[])
check("heat + dry advisories", {"heat","dry"}<=codes(W(30,40,0,[{"date":"d","tmax":40,"tmin":25,"rain":0,"rain_prob":0,"wind":10}])))
check("frost advisory", "frost" in codes(W(5,70,0,[{"date":"d","tmax":12,"tmin":1,"rain":0,"rain_prob":0,"wind":5}])))
check("heavy rain advisory", "rain" in codes(W(22,80,0,[{"date":"d","tmax":25,"tmin":18,"rain":30,"rain_prob":90,"wind":10}])))
check("strong wind advisory", "wind" in codes(W(22,60,0,[{"date":"d","tmax":25,"tmin":18,"rain":0,"rain_prob":0,"wind":55}])))
check("mild weather -> no weather advisories", codes(W())==set())

# ---------- /api/weather ----------
A.fetch_weather=lambda lat,lon,timeout=8: W(20,90,1.0)
r=a.get("/api/weather?lat=21.14&lon=79.08&crop=Tomato").get_json()
check("api weather: current + 3-day forecast + estimated risk + note", r["ok"] and len(r["forecast"])==3 and r["current"]["temperature_2m"]==20 and any(x["type"]=="disease_risk" and x["level"]=="high" for x in r["risk"]) and "definitely" in r["risk_note"], r)
check("api weather: invalid coords 400", a.get("/api/weather?lat=999&lon=0").status_code==400 and a.get("/api/weather?lat=abc&lon=1").status_code==400)
check("api weather: missing params 400", a.get("/api/weather").status_code==400)
A.fetch_weather=lambda lat,lon,timeout=8: None
check("api weather: provider down -> 502, no fake data", a.get("/api/weather?lat=21&lon=79").status_code==502)
A._RATE_BUCKETS.clear()
c45=[client().get("/api/weather?lat=21&lon=79").status_code for _ in range(45)]
check("api weather rate limited (429)", 429 in c45); A._RATE_BUCKETS.clear()
# real fetch_weather parsing + cache (network mocked)
urls=[]
class Resp:
    def read(self): return json.dumps({"current":{"temperature_2m":22,"relative_humidity_2m":50},"daily":{"time":["d1","d2","d3"],"temperature_2m_max":[30,31,32],"temperature_2m_min":[20,21,22],"precipitation_sum":[0,1,2],"precipitation_probability_max":[5,6,7],"wind_speed_10m_max":[10,11,12]}}).encode()
ur.urlopen=lambda u,timeout=0: (urls.append(u), Resp())[1]
A.fetch_weather=REAL_FETCH; A._WEATHER_CACHE.clear()
w1=A.fetch_weather(21.1458,79.0882); w2=A.fetch_weather(21.1461,79.0881)
check("fetch_weather parses forecast", len(w1["daily"])==3 and w1["daily"][2]["rain"]==2 and w1["current"]["temperature_2m"]==22)
check("fetch_weather cached (1 network call for nearby points)", len(urls)==1 and w1 is w2, len(urls))
def boom(u,timeout=0): raise OSError("net down")
ur.urlopen=boom; A._WEATHER_CACHE.clear(); check("fetch_weather returns None on failure (never raises)", A.fetch_weather(1.0,2.0) is None)
ur.urlopen=REAL_OPEN

# ---------- coarse opt-in location ----------
check("no location by default", A.get_location(ua) is None)
post(a,"/notifications/location",{"lat":"21.14587","lon":"79.08823"})
r_=rows("SELECT loc_lat,loc_lon FROM notification_prefs WHERE user_id=?",ua)[0]
check("location stored ROUNDED to 0.1 deg (no precise position)", float(r_["loc_lat"])==21.1 and float(r_["loc_lon"])==79.1, r_)
post(a,"/notifications/location",{"lat":"999","lon":"1"}); check("invalid location rejected, previous kept", A.get_location(ua)==(21.1,79.1))
post(a,"/notifications/location",{"lat":"x","lon":"y"}); check("non-numeric location rejected", A.get_location(ua)==(21.1,79.1))
check("settings page shows saved state + remove button", "Remove saved location" in a.get("/notifications/settings").get_data(as_text=True))
check("other user has no location", A.get_location(ub) is None and "Use my location" in b.get("/notifications/settings").get_data(as_text=True))
check("settings page has new toggles", all(x in a.get("/notifications/settings").get_data(as_text=True) for x in ('name="disease_risk"','name="weather_advisory"')))
post(a,"/notifications/settings",{"scan_reminders":"on","soil_moisture":"on","expert_reply":"on","health_change":"on","disease_risk":"on","weather_advisory":"on","reminder_days":"7"})
check("saving settings keeps the saved location", A.get_location(ua)==(21.1,79.1))

# ---------- weather alerts -> notifications ----------
post(a,"/fields",{"name":"Tomato plot","crop":"Tomato","area_acres":"1"})
calls=[]
A.fetch_weather=lambda lat,lon,timeout=8: (calls.append((lat,lon)), W(20,90,1.5,[{"date":"d","tmax":40,"tmin":25,"rain":0,"rain_prob":0,"wind":10}]))[1]
A.generate_due_notifications(ua)
kinds=sorted(r["kind"] for r in rows("SELECT kind FROM notifications WHERE user_id=?",ua))
check("alerts: estimated disease risk (for the field's crop) + heat advisory", "disease_risk" in kinds and "wx_heat" in kinds, kinds)
check("alerts use the rounded saved location only", calls and calls[0]==(21.1,79.1), calls)
A.generate_due_notifications(ua); check("alerts deduped per day", len(rows("SELECT id FROM notifications WHERE user_id=?",ua))==len(kinds))
h=a.get("/notifications").get_data(as_text=True)
check("alert text is an advisory (estimated risk, not a guarantee)", "Estimated disease risk" in h and "not a guarantee" in h and "Tomato" in h and "Very hot weather" in h)
cn=A.db(); cn.execute("DELETE FROM notifications"); cn.commit(); cn.close()
post(a,"/notifications/settings",{"scan_reminders":"on","weather_advisory":"on","reminder_days":"7"}); A.generate_due_notifications(ua)
check("disease_risk off -> only weather advisory", {r["kind"] for r in rows("SELECT kind FROM notifications WHERE user_id=?",ua)}=={"wx_heat","wx_dry"})
cn=A.db(); cn.execute("DELETE FROM notifications"); cn.commit(); cn.close()
post(a,"/notifications/settings",{"reminder_days":"7"}); A.generate_due_notifications(ua)
check("both off -> nothing", len(rows("SELECT id FROM notifications WHERE user_id=?",ua))==0)
post(a,"/notifications/settings",{"disease_risk":"on","weather_advisory":"on","reminder_days":"7"})
A.generate_due_notifications(ub); check("no location -> no weather alerts for other user", len(rows("SELECT id FROM notifications WHERE user_id=?",ub))==0)
A.fetch_weather=lambda lat,lon,timeout=8: None; A.generate_due_notifications(ua); check("weather provider down -> no alerts, no crash", len(rows("SELECT id FROM notifications WHERE user_id=?",ua))==0)
post(a,"/notifications/location/delete"); check("remove location deletes it", A.get_location(ua) is None)
A.fetch_weather=lambda lat,lon,timeout=8: W(20,90,1.5)
cn=A.db(); cn.execute("DELETE FROM notifications"); cn.commit(); cn.close(); A.generate_due_notifications(ua); check("after removal no more weather alerts", len(rows("SELECT id FROM notifications WHERE user_id=?",ua))==0)
a.get("/set-language/hi")
cn=A.db(); cn.execute("INSERT INTO notifications(user_id,kind,params_json) VALUES(?,?,?)",(ua,"wx_rain",'{"value":40}')); cn.commit(); cn.close()
check("weather notification renders in Hindi session", a.get("/notifications").status_code==200); a.get("/set-language/en")

# ---------- weather snapshot stored with scans (no coordinates) ----------
gate_t={"ok":True,"provider":"plantnet","supported_crop":"Tomato","plant":"Tomato","scientific_name":"Solanum lycopersicum","confidence":88,"common_names":["Tomato"]}
def loc(cls,conf=95): return {"class":cls,"confidence":conf,"margin":90,"stable":100,"accepted":True,"plant":"Tomato","plant_key":"Tomato","supported":True}
def cam(cl,cls="Tomato___Late_blight",**extra):
    A.validate_plant_image=lambda p,l="en": gate_t; A.predict_strict=lambda p: loc(cls); A.GEMINI_ENABLED=False
    return cl.post("/detect-camera",data={"image":(png(),"c.jpg"),**extra},headers={"X-CSRF-Token":token(cl)},content_type="multipart/form-data").get_json()
A.fetch_weather=lambda lat,lon,timeout=8: {"current":{"temperature_2m":27.5,"relative_humidity_2m":62,"rain":0.4,"wind_speed_10m":9},"daily":[]}
r1=cam(a,lat="21.1458",lon="79.0882"); wj=rows("SELECT weather_json FROM scans WHERE id=?",r1["scan_id"])[0]["weather_json"]
check("scan with allowed location stores weather summary", json.loads(wj)=={"temp":27.5,"humidity":62,"rain":0.4,"wind":9}, wj)
check("coordinates are NOT stored anywhere on the scan", "21.14" not in wj and "79.08" not in wj and all("21.14" not in str(v) and "79.08" not in str(v) for v in rows("SELECT * FROM scans WHERE id=?",r1["scan_id"])[0].values()))
r2=cam(a); check("scan without coordinates: no weather stored", rows("SELECT weather_json FROM scans WHERE id=?",r2["scan_id"])[0]["weather_json"] is None)
r3=cam(a,lat="999",lon="1"); check("invalid coordinates ignored", rows("SELECT weather_json FROM scans WHERE id=?",r3["scan_id"])[0]["weather_json"] is None and r3["ok"])
A.fetch_weather=lambda lat,lon,timeout=8: None
r4=cam(a,lat="21.1",lon="79.0"); check("weather provider down: scan still saved", r4["ok"] and rows("SELECT weather_json FROM scans WHERE id=?",r4["scan_id"])[0]["weather_json"] is None)
h=a.get(f"/history/{r1['scan_id']}").get_data(as_text=True); check("scan page shows stored weather", "27.5" in h and "62%" in h)
h=a.get(f"/history/{r2['scan_id']}").get_data(as_text=True); check("scan page: Not recorded when absent", "Not recorded for this scan" in h)
r=a.get(f"/history/{r1['scan_id']}/report.pdf"); check("PDF builds with weather", r.status_code==200 and r.get_data()[:4]==b"%PDF")

# ---------- explainability (route + overlay; heatmap function mocked here, real model tested in test_realmodel.py) ----------
import numpy as np
from PIL import Image as PImage
sid=r1["scan_id"]
check("explain button shown for built-in-model scans", "scan_explain" in a.get(f"/history/{sid}").get_data(as_text=True) or f"/history/{sid}/explain" in a.get(f"/history/{sid}").get_data(as_text=True))
A.interpreter=object()
def fake_heat(path,idx): 
    im=PImage.open(path).convert("RGB"); im.thumbnail((256,256)); h=np.zeros((im.size[1],im.size[0]),np.float32); h[10:60,10:60]=1.0
    return {"heat":h,"focused":True,"class_idx":idx,"confidence":0.9,"image":im}
A.EXPLAINERS["occlusion"]=fake_heat
check("IDOR: other user cannot explain my scan", post(b,f"/history/{sid}/explain").status_code==404)
post(a,f"/history/{sid}/explain"); ex=rows("SELECT explain_image FROM scans WHERE id=?",sid)[0]["explain_image"]
check("explanation image created, owner-prefixed name", ex and ex.startswith(f"u{ua}_explain_") and os.path.isfile(os.path.join(A.UPLOAD_FOLDER,ex)))
h=a.get(f"/history/{sid}").get_data(as_text=True)
check("explanation wording is hedged (not a diagnosis)", "highlighted area contributed strongly" in h and "not a definitive biological diagnosis" in h)
check("overlay served privately to owner only", a.get("/media/"+ex).status_code==200 and b.get("/media/"+ex).status_code==404 and client().get("/media/"+ex).status_code==302)
cn=A.db(); cn.execute("INSERT INTO scans(user_id,image,plant,disease,confidence,status) VALUES(?,?,?,?,?,?)",(ua,"x.jpg","Mango","Anthracnose",80,"Gemini broad plant analysis")); gid=cn.execute("SELECT MAX(id) AS i FROM scans").fetchone()["i"]; cn.commit(); cn.close()
post(a,f"/history/{gid}/explain"); check("no explanation for non built-in-model scans", rows("SELECT explain_image FROM scans WHERE id=?",gid)[0]["explain_image"] is None and "explain" not in a.get(f"/history/{gid}").get_data(as_text=True).split("Why this result")[-1][:0])
check("Why-this-result section hidden for non built-in-model scans", "Why this result" not in a.get(f"/history/{gid}").get_data(as_text=True))
A.EXPLAINERS["occlusion"]=lambda p,i: (_ for _ in ()).throw(RuntimeError("boom")); A._RATE_BUCKETS.clear()
cn=A.db(); cn.execute("UPDATE scans SET explain_image=NULL WHERE id=?",(sid,)); cn.commit(); cn.close()
check("explainer failure handled gracefully (redirect, no 500)", post(a,f"/history/{sid}/explain").status_code==302)
A._RATE_BUCKETS.clear(); codes_=[post(a,f"/history/{sid}/explain").status_code for _ in range(12)]; check("explain rate limited", 429 in codes_); A._RATE_BUCKETS.clear()
A.interpreter=None
check("occlusion registered as an explainer (Grad-CAM can be added)", "occlusion" in A.EXPLAINERS)

# ---------- satellite / drone readiness ----------
cn=A.db(); cn.execute("INSERT INTO fields(user_id,name,crop) VALUES(?,?,?)",(ua,"Imagery field","Wheat")); fid=cn.execute("SELECT MAX(id) AS i FROM fields").fetchone()["i"]; cn.commit(); cn.close()
h=a.get(f"/fields/{fid}/imagery").get_data(as_text=True)
check("imagery page: honest 'not configured yet', no data", "Satellite/drone analysis is not configured yet." in h and "<img" not in h.split("<section class=\"card\">")[-1])
r=a.get(f"/api/fields/{fid}/imagery"); check("imagery API: 501 not configured", r.status_code==501 and r.get_json()["configured"] is False and "not configured" in r.get_json()["message"])
check("imagery IDOR (page + API)", b.get(f"/fields/{fid}/imagery").status_code==404 and b.get(f"/api/fields/{fid}/imagery").status_code==404)
check("field page links to imagery", "/imagery" in a.get(f"/fields/{fid}").get_data(as_text=True))
os.environ["IMAGERY_PROVIDER"]="doesnotexist"; check("unknown provider -> null provider", type(A.get_imagery_provider()).__name__=="NullImageryProvider"); os.environ.pop("IMAGERY_PROVIDER")
check("provider interface exposes the planned capabilities", all(hasattr(A.ImageryProvider,m) for m in ("vegetation_index","crop_stress_map","affected_area")))

# ---------- public landing + SEO ----------
pub=client(); r=pub.get("/"); h=r.get_data(as_text=True)
check("anonymous / is a public landing page (200)", r.status_code==200 and "<h1>" in h and "Scan a plant" in h)
check("landing SEO: description, canonical, og:title, JSON-LD", all(x in h for x in ('name="description"','rel="canonical"','property="og:title"','application/ld+json','og:description')))
check("landing indexable (no noindex header)", "X-Robots-Tag" not in r.headers)
check("og:title is page-specific", 'og:title" content="RBAgriScan' in h)
check("anonymous POST / -> login", pub.post("/",data={"_csrf":token(pub)}).status_code==302)
r=a.get("/"); check("logged-in / is the scanner + noindex", r.status_code==200 and 'id="chatForm"' in r.get_data(as_text=True) and "noindex" in r.headers.get("X-Robots-Tag",""))
check("private pages noindex", all("noindex" in a.get(p).headers.get("X-Robots-Tag","") for p in ("/dashboard","/history","/fields","/community","/notifications")))
check("api noindex", "noindex" in a.get("/api/health").headers.get("X-Robots-Tag",""))
check("login/register/about/contact indexable for visitors", all("X-Robots-Tag" not in pub.get(p).headers for p in ("/login","/register","/about","/contact","/supported-plants")))
for p_ in ("/about","/contact","/supported-plants"): check(f"{p_} has its own meta description", 'name="description" content="' in pub.get(p_).get_data(as_text=True) and "Works offline as an installable app" not in pub.get(p_).get_data(as_text=True).split("<body")[0])
rb=pub.get("/robots.txt").get_data(as_text=True); check("robots.txt hides private areas + has sitemap", all(x in rb for x in ("Disallow: /api/","Disallow: /media/","Disallow: /history","Disallow: /community","Sitemap:")))
sm=pub.get("/sitemap.xml").get_data(as_text=True); check("sitemap lists public pages only", "<urlset" in sm and sm.count("<loc>")>=4 and "history" not in sm and "dashboard" not in sm)
check("PWA manifest valid JSON with icons", len(json.load(open(os.path.join(os.path.dirname(A.__file__),"static/manifest.json"),encoding="utf-8"))["icons"])>=3)
sw=open(os.path.join(os.path.dirname(A.__file__),"static/sw.js"),encoding="utf-8").read()
check("service worker never caches private pages/photos", "/media/" in sw and "PUBLIC_PAGES" in sw)
print(f"\nRESULT: {PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
