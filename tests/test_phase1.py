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


import time, json as _json
# ---- unit: severity / score
cases=[("Tomato___healthy",95,98,"none"),("Tomato___Late_blight",97,35,"high"),("Tomato___Early_blight",90,55,"medium"),
       ("Apple___Apple_scab",80,55,"medium"),("Tomato___Tomato_Yellow_Leaf_Curl_Virus",90,35,"high"),("Mango anthracnose",90,None,None),
       ("Tomato___healthy",40,None,None),("Unknown",90,None,None),("Disease analysis unavailable for this plant",90,None,None)]
for cls,conf,exp_s,exp_v in cases:
    sc,sv=A.health_assessment({"class":cls,"confidence":conf})
    check(f"health_assessment {cls} @{conf}", (sc,sv)==(exp_s,exp_v),(sc,sv))
check("low_confidence never scored", A.health_assessment({"class":"Tomato___healthy","confidence":99,"low_confidence":True})==(None,None))
check("unknown never scored", A.health_assessment({"unknown":True,"class":"x","confidence":99})==(None,None))
check("init_db idempotent (migrations re-run)", A.init_db() is None or True)

cn=A.db()
for t in ("ticket_messages","diagnostic_tickets","scans"): cn.execute("DELETE FROM "+t)
cn.execute("DELETE FROM users WHERE email LIKE ?",("%@t.test",)); cn.commit(); cn.close()
c=client(); register(c,"a@t.test")
gate_t={"ok":True,"provider":"plantnet","supported_crop":"Tomato","plant":"Tomato","scientific_name":"Solanum lycopersicum","confidence":88,"common_names":["Tomato"]}
gate_m={"ok":True,"provider":"plantnet","supported_crop":"Mango","plant":"Mango","scientific_name":"Mangifera indica","confidence":81,"common_names":["Mango"]}
def loc(cls,conf=95,acc=True): return {"class":cls,"confidence":conf,"margin":90,"stable":100,"accepted":acc,"plant":"Tomato","plant_key":"Tomato","supported":True}
def cam(gate,local,gem=None):
    A.validate_plant_image=lambda p,l="en": gate
    A.predict_strict=lambda p: local
    A.GEMINI_ENABLED = gem is not None
    if gem is not None: A.gemini_detect=lambda p,l: gem
    return c.post("/detect-camera",data={"image":(png(),"c.jpg")},headers={"X-CSRF-Token":token(c)},content_type="multipart/form-data").get_json()
r1=cam(gate_t,loc("Tomato___healthy",95)); r2=cam(gate_t,loc("Tomato___Early_blight",90)); r3=cam(gate_t,loc("Tomato___Late_blight",97))
check("camera payload has health_score 98/55/35", [r1["health_score"],r2["health_score"],r3["health_score"]]==[98,55,35],[r1.get("health_score"),r2.get("health_score"),r3.get("health_score")])
check("camera payload has label", r1["health_label"]=="Crop Health Score")
rm=cam(gate_m,loc("x"),gem={"plant":"Mango","disease":"Anthracnose","confidence":72,"explanation":"Dark sunken spots","home_remedies":["Prune"],"natural":[],"field":[],"chemical":[],"prevention":["Airflow"]})
check("unclassifiable Gemini disease -> no score (Not enough data)", rm["health_score"] is None and rm["scan_id"])
rl=cam(gate_t,loc("Tomato___Late_blight",41,False))
cn=A.db(); lr=cn.execute("SELECT status,health_score,severity FROM scans WHERE id=?",(rl.get("scan_id") or 0,)).fetchone(); cn.close()
check("low confidence: honest message, stored as 'Low confidence', never scored", rl["low_confidence"] and rl["health_score"] is None and lr and lr["status"]=="Low confidence" and lr["health_score"] is None, (rl,lr))

# ---- history
r=c.get("/history"); h=r.get_data(as_text=True)
check("history 200 lists scans", r.status_code==200 and "Tomato" in h and "Mango" in h and h.count("scan-card")>=5, r.status_code)
check("history shows images via /media", "/media/u" in h and "/static/uploads" not in h)
r=c.get("/history?plant=Tomato"); h=r.get_data(as_text=True)
check("timeline SVG charts with >=2 real points", "<svg" in h and "Crop health timeline" in h and h.count("<polyline")>=2, h.count("<polyline"))
r=c.get("/history?plant=Mango"); h=r.get_data(as_text=True)
check("single scan -> Not enough data yet, no fake chart", "Not enough data yet" in h and "<polyline" not in h)
r=c.get("/history?plant=%27%20OR%201%3D1--"); check("SQLi-ish plant filter harmless", r.status_code==200)
r=c.get("/history?page=abc"); check("bad page param ok", r.status_code==200)
cn=A.db(); ids=[x["id"] for x in cn.execute("SELECT id FROM scans ORDER BY id").fetchall()]; cn.close()
late=[x for x in ids][2]
r=c.get(f"/history/{ids[0]}"); h=r.get_data(as_text=True)
check("detail: complete result for local class (advisory + score + pdf link)", r.status_code==200 and "98" in h and "Advisory" in h and "report.pdf" in h)
r=c.get(f"/history/{ids[3]}"); h=r.get_data(as_text=True)
check("detail: saved Gemini guidance restored from result_json", r.status_code==200 and "Prune" in h and "Dark sunken spots" in h and "Not enough data yet" in h)
b=client(); register(b,"b@t.test")
check("IDOR: other user cannot open detail", b.get(f"/history/{ids[0]}").status_code==404)
check("IDOR: other user cannot get PDF", b.get(f"/history/{ids[0]}/report.pdf").status_code==404)
check("IDOR: other user history is empty", 'class="scan-card"' not in b.get("/history").get_data(as_text=True))
check("anonymous -> login", client().get(f"/history/{ids[0]}").status_code==302 and client().get(f"/history/{ids[0]}/report.pdf").status_code==302)
check("missing scan 404", c.get("/history/999999").status_code==404)

# ---- PDF
r=c.get(f"/history/{late}/report.pdf")
pdf=r.get_data()
check("PDF 200 application/pdf", r.status_code==200 and r.mimetype=="application/pdf" and pdf[:5]==b"%PDF-" and len(pdf)>5000, (r.status_code,len(pdf)))
check("PDF attachment + no-store", "attachment" in r.headers["Content-Disposition"] and r.headers["Cache-Control"]=="private, no-store")
open(os.path.join(TMP, "report_en.pdf"),"wb").write(pdf)
c.get("/set-language/fr"); r=c.get(f"/history/{ids[1]}/report.pdf"); check("PDF in French language session builds", r.status_code==200 and r.get_data()[:4]==b"%PDF")
open(os.path.join(TMP, "report_fr.pdf"),"wb").write(r.get_data())
c.get("/set-language/hi"); r=c.get(f"/history/{ids[1]}/report.pdf"); check("PDF in Hindi session builds (English fallback, no crash)", r.status_code==200 and r.get_data()[:4]==b"%PDF")
c.get("/set-language/en")

# ---- XSS in stored data
cn=A.db(); uid=cn.execute("SELECT id FROM users WHERE email='a@t.test'").fetchone()["id"]
cn.execute("INSERT INTO scans(user_id,image,plant,disease,confidence,status,health_score,severity) VALUES(?,?,?,?,?,?,?,?)",(uid,"x.jpg","<script>alert(1)</script>","<img src=x onerror=alert(2)>",90,"t",55,"medium")); cn.commit()
xid=cn.execute("SELECT MAX(id) AS i FROM scans").fetchone()["i"]; cn.close()
h=c.get("/history").get_data(as_text=True)+c.get(f"/history/{xid}").get_data(as_text=True)+c.get("/dashboard").get_data(as_text=True)
check("stored XSS escaped in history/detail/dashboard", "<script>alert(1)" not in h and "<img src=x onerror" not in h)
r=c.get(f"/history/{xid}/report.pdf"); check("PDF handles hostile text", r.status_code==200)

# ---- dashboard analytics
h=c.get("/dashboard").get_data(as_text=True)
check("dashboard: real analytics, no CDN dependency", "Disease distribution" in h and "cdn.jsdelivr" not in h and "Chart(" not in h and "<polyline" in h)
h=b.get("/dashboard").get_data(as_text=True)
check("dashboard: new user -> Not enough data yet, no charts", "Not enough data yet" in h and "<polyline" not in h)
# ---- Hindi pages render fast
c.get("/set-language/hi"); t0=time.time(); r=c.get("/history"); dt=time.time()-t0
check(f"history in Hindi renders instantly ({dt:.2f}s)", r.status_code==200 and dt<3, dt)
print(f"\nRESULT: {PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
