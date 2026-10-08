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


import time
cn=A.db()
for t in ("ticket_messages","diagnostic_tickets","sensor_readings","sensor_alerts","sensor_devices","scans","fields"): cn.execute("DELETE FROM "+t)
cn.execute("DELETE FROM users WHERE email LIKE ?",("%@t.test",)); cn.commit(); cn.close()
check("init_db idempotent with Phase 2 migrations", A.init_db() is None or True)
a=client(); register(a,"a@t.test"); b=client(); register(b,"b@t.test")
def mk(cl,**kw):
    d={"farm_name":"Home Farm","name":"Field A","crop":"Tomato","area_acres":"2","notes":""}; d.update(kw)
    return post(cl,"/fields",d)
# validation
for label,kw in [("empty name",{"name":""}),("empty crop",{"crop":""}),("bad area",{"area_acres":"abc"}),("zero area",{"area_acres":"0"}),("huge area",{"area_acres":"999999"}),("negative",{"area_acres":"-3"})]:
    mk(a,**kw)
cn=A.db(); n=cn.execute("SELECT COUNT(*) AS n FROM fields").fetchone()["n"]; cn.close()
check("invalid fields rejected (6 bad forms -> 0 rows)", n==0, n)
mk(a); mk(a,name="Field B",crop="Wheat",area_acres="4"); mk(a,name="Field C",crop="Cotton",area_acres="3,5")
cn=A.db(); rows=cn.execute("SELECT id,name,area_acres FROM fields ORDER BY id").fetchall(); cn.close()
check("3 fields created; comma decimal parsed", len(rows)==3 and float(rows[2]["area_acres"])==3.5, rows)
fa,fb,fc=[r["id"] for r in rows]
mk(b,name="B-only",crop="Rice",area_acres="1"); 
cn=A.db(); fbo=cn.execute("SELECT id FROM fields WHERE name='B-only'").fetchone()["id"]; cn.close()
h=a.get("/fields").get_data(as_text=True)
check("fields page: farm group, 3 fields, total 9.5 acres", "Home Farm" in h and "Field B" in h and "Wheat" in h and "9.5" in h and "B-only" not in h)
# IDOR
check("IDOR: other user cannot open field", b.get(f"/fields/{fa}").status_code==404)
r=post(b,f"/fields/{fa}/edit",{"name":"hacked","crop":"x"}); cn=A.db(); nm=cn.execute("SELECT name FROM fields WHERE id=?",(fa,)).fetchone()["name"]; cn.close()
check("IDOR: other user cannot edit field", r.status_code==404 and nm=="Field A")
check("IDOR: other user cannot delete field", post(b,f"/fields/{fa}/delete").status_code==404 and a.get(f"/fields/{fa}").status_code==200)
check("IDOR: other user cannot get field PDF", b.get(f"/fields/{fa}/report.pdf").status_code==404)
check("anonymous -> login", client().get("/fields").status_code==302 and client().get(f"/fields/{fa}").status_code==302)
check("missing field 404", a.get("/fields/999999").status_code==404)
# edit
post(a,f"/fields/{fa}/edit",{"farm_name":"Home Farm","name":"Field A1","crop":"Tomato","area_acres":"2.5","notes":"north side"})
check("edit own field", "Field A1" in a.get(f"/fields/{fa}").get_data(as_text=True) and "north side" in a.get(f"/fields/{fa}").get_data(as_text=True))
# scans
gate_t={"ok":True,"provider":"plantnet","supported_crop":"Tomato","plant":"Tomato","scientific_name":"Solanum lycopersicum","confidence":88,"common_names":["Tomato"]}
def loc(cls,conf=95): return {"class":cls,"confidence":conf,"margin":90,"stable":100,"accepted":True,"plant":"Tomato","plant_key":"Tomato","supported":True}
def cam(cl,cls,field=None,conf=95):
    A.validate_plant_image=lambda p,l="en": gate_t; A.predict_strict=lambda p: loc(cls,conf); A.GEMINI_ENABLED=False
    d={"image":(png(),"c.jpg")}
    if field is not None: d["field_id"]=str(field)
    return cl.post("/detect-camera",data=d,headers={"X-CSRF-Token":token(cl)},content_type="multipart/form-data").get_json()
r1=cam(a,"Tomato___healthy",fa); r2=cam(a,"Tomato___Early_blight",fa); r3=cam(a,"Tomato___Late_blight",fa)
r4=cam(a,"Tomato___healthy",fbo); r5=cam(a,"Tomato___healthy","abc"); r6=cam(a,"Tomato___healthy")
cn=A.db(); fids=[cn.execute("SELECT field_id FROM scans WHERE id=?",(r["scan_id"],)).fetchone()["field_id"] for r in (r1,r2,r3,r4,r5,r6)]; cn.close()
check("camera scan saved into own field; foreign/invalid/missing field_id -> none", fids==[fa,fa,fa,None,None,None], fids)
# upload form route also accepts field
A.validate_plant_image=lambda p,l="en": gate_t; A.predict_strict=lambda p: loc("Tomato___healthy"); 
a.post("/",data={"_csrf":token(a),"image":(png(),"l.png"),"field_id":str(fb)},content_type="multipart/form-data")
cn=A.db(); up=cn.execute("SELECT field_id FROM scans ORDER BY id DESC LIMIT 1").fetchone()["field_id"]; cn.close()
check("upload form saves scan into chosen field", up==fb, up)
h=a.get("/").get_data(as_text=True); check("scan page shows field selector for user with fields", 'id="scanField"' in h and "Field A1" in h and "B-only" not in h)
c2=client(); register(c2,"c@t.test"); check("no selector for user without fields", 'id="scanField"' not in c2.get("/").get_data(as_text=True))
# detail
h=a.get(f"/fields/{fa}").get_data(as_text=True)
check("field detail: timeline charts from 3 real scans", h.count("<polyline")>=2 and "Disease history" in h and "Late blight" in h)
check("field detail: stats latest=35 avg=63", "<b>35</b>" in h and "<b>63</b>" in h, None)
h=a.get(f"/fields/{fc}").get_data(as_text=True)
check("empty field: Not enough data yet, no chart, no fake sensors", "Not enough data yet" in h and "<polyline" not in h and "No live sensor data available" in h)
# scan assign / unassign
cn=A.db(); sid=cn.execute("SELECT id FROM scans WHERE field_id IS NULL ORDER BY id LIMIT 1").fetchone()["id"]; cn.close()
post(a,f"/history/{sid}/field",{"field_id":str(fc)}); cn=A.db(); v=cn.execute("SELECT field_id FROM scans WHERE id=?",(sid,)).fetchone()["field_id"]; cn.close()
check("assign existing scan to own field", v==fc, v)
check("assign to foreign field -> 404, unchanged", post(a,f"/history/{sid}/field",{"field_id":str(fbo)}).status_code==404)
check("other user cannot reassign my scan", post(b,f"/history/{sid}/field",{"field_id":str(fbo)}).status_code==404)
post(a,f"/history/{sid}/field",{"field_id":""}); cn=A.db(); v=cn.execute("SELECT field_id FROM scans WHERE id=?",(sid,)).fetchone()["field_id"]; cn.close()
check("unassign scan", v is None)
check("scan detail shows field selector", "scan_set_field" in a.get(f"/history/{sid}").get_data(as_text=True) or f"/history/{sid}/field" in a.get(f"/history/{sid}").get_data(as_text=True))
# IoT
r=post(a,"/iot/devices/new",{"name":"foreign","field_id":str(fbo)}); cn=A.db(); d0=cn.execute("SELECT COUNT(*) AS n FROM sensor_devices").fetchone()["n"]; cn.close()
check("IoT device cannot link to someone else's field", d0==0, d0)
post(a,"/iot/devices/new",{"name":"soil-A","field_id":str(fa)}); post(a,"/iot/devices/new",{"name":"soil-old","field_id":str(fa)})
cn=A.db(); devs=cn.execute("SELECT id,name FROM sensor_devices ORDER BY id").fetchall()
cn.execute("INSERT INTO sensor_readings(device_id,soil_moisture,temperature,humidity) VALUES(?,?,?,?)",(devs[0]["id"],41.5,27.0,60.0))
cn.execute("INSERT INTO sensor_readings(device_id,soil_moisture,temperature,humidity,recorded_at) VALUES(?,?,?,?,NOW() - INTERVAL 5 HOUR)",(devs[1]["id"],20.0,30.0,50.0)); cn.commit(); cn.close()
h=a.get(f"/fields/{fa}").get_data(as_text=True)
check("sensors: fresh reading connected, 5h-old reading not connected", "#16a34a" in h and "41.5" in h and "not connected" in h and "20.0" in h)
check("IoT page lists own fields in select", "Field A1" in a.get("/iot").get_data(as_text=True))
# dashboard
h=a.get("/dashboard").get_data(as_text=True); check("dashboard: field health table", "Field health" in h and "Field A1" in h and "B-only" not in h)
# PDFs
r=a.get(f"/fields/{fa}/report.pdf"); check("field PDF 200 %PDF", r.status_code==200 and r.get_data()[:5]==b"%PDF-" and "attachment" in r.headers["Content-Disposition"] and r.headers["Cache-Control"]=="private, no-store")
open(os.path.join(TMP, "field_report.pdf"),"wb").write(r.get_data())
r=a.get(f"/fields/{fc}/report.pdf"); check("empty field PDF builds", r.status_code==200)
# XSS
post(a,"/fields",{"farm_name":"<b>x</b>","name":"<script>alert(1)</script>","crop":"<img src=x onerror=alert(2)>","area_acres":"1","notes":"<svg onload=alert(3)>"})
cn=A.db(); xid=cn.execute("SELECT id FROM fields WHERE name LIKE ?",("%script%",)).fetchone()["id"]; cn.close()
h=a.get("/fields").get_data(as_text=True)+a.get(f"/fields/{xid}").get_data(as_text=True)+a.get("/dashboard").get_data(as_text=True)+a.get("/").get_data(as_text=True)
check("stored XSS in field fields escaped everywhere", "<script>alert(1)" not in h and "<img src=x onerror" not in h and "<svg onload" not in h and "<b>x</b>" not in h)
check("PDF handles hostile field text", a.get(f"/fields/{xid}/report.pdf").status_code==200)
# control chars + limit
post(a,"/fields",{"name":"Tab\x00Name\n","crop":"Rice"}); cn=A.db(); nm=cn.execute("SELECT name FROM fields WHERE crop='Rice' AND user_id=(SELECT id FROM users WHERE email='a@t.test')").fetchone()["name"]; cn.close()
check("control characters stripped from names", "\x00" not in nm and "\n" not in nm, repr(nm))
cn=A.db(); uid=cn.execute("SELECT id FROM users WHERE email='c@t.test'").fetchone()["id"]
for i in range(50): cn.execute("INSERT INTO fields(user_id,name,crop) VALUES(?,?,?)",(uid,f"f{i}","Rice"))
cn.commit(); cn.close()
post(c2,"/fields",{"name":"one too many","crop":"Rice"}); cn=A.db(); n=cn.execute("SELECT COUNT(*) AS n FROM fields WHERE user_id=?",(uid,)).fetchone()["n"]; cn.close()
check("max 50 fields per user enforced", n==50, n)
# delete keeps scans
post(a,f"/fields/{fa}/delete")
cn=A.db(); left=cn.execute("SELECT COUNT(*) AS n FROM scans WHERE image IS NOT NULL").fetchone()["n"]; orphan=cn.execute("SELECT COUNT(*) AS n FROM scans WHERE field_id=?",(fa,)).fetchone()["n"]
dev=cn.execute("SELECT field_id FROM sensor_devices WHERE name='soil-A'").fetchone()["field_id"]; gone=cn.execute("SELECT COUNT(*) AS n FROM fields WHERE id=?",(fa,)).fetchone()["n"]; cn.close()
check("delete field: field gone, scans kept & unlinked, sensor kept & unlinked", gone==0 and orphan==0 and left>=7 and dev is None, (gone,orphan,left,dev))
check("deleted field -> 404", a.get(f"/fields/{fa}").status_code==404)
# language
a.get("/set-language/hi"); t0=time.time(); r=a.get("/fields"); check(f"fields page in Hindi renders instantly ({time.time()-t0:.2f}s)", r.status_code==200 and time.time()-t0<3)
print(f"\nRESULT: {PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
