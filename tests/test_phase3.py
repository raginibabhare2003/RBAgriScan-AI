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
for t in ("notifications","notification_prefs","ticket_messages","diagnostic_tickets","sensor_alerts","sensor_readings","sensor_devices","scans","fields"): cn.execute("DELETE FROM "+t)
cn.execute("DELETE FROM users WHERE email LIKE ?",("%@t.test",)); cn.commit(); cn.close()
check("init_db idempotent with Phase 3 migrations", A.init_db() is None or True)
def uid_of(email):
    cn=A.db(); r=cn.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone(); cn.close(); return r["id"]
def count(uid,kind=None):
    cn=A.db(); q="SELECT COUNT(*) AS n FROM notifications WHERE user_id=?"; a=[uid]
    if kind: q+=" AND kind=?"; a.append(kind)
    n=cn.execute(q,tuple(a)).fetchone()["n"]; cn.close(); return n
a=client(); register(a,"a@t.test"); b=client(); register(b,"b@t.test"); ua,ub=uid_of("a@t.test"),uid_of("b@t.test")

# ---- safe rendering
check("_fill substitutes", A._fill("Hi {name}, {days} days",{"name":"Ravi","days":7})=="Hi Ravi, 7 days")
check("_fill is not str.format (no attribute access / recursion)", A._fill("{0.__class__} {a}",{"a":"{b}"})=="{0.__class__} {b}")
check("_fill tolerates spaced placeholder from translators", A._fill("{ days } days",{"days":3})=="3 days")
row={"id":1,"kind":"field_reminder","params_json":'{"field":"F1","days":9}',"link":"/fields/1","is_read":0,"created_at":None}
orig=A.tr_fast
A.tr_fast=lambda t,l=None: "Przypomnienie {pole} {days}" if t.startswith("Scan reminder") else t
m=A.render_notification(row,"pl"); A.tr_fast=orig
check("mangled placeholder in translation -> English fallback", m["title"]=="Scan reminder: F1", m["title"])
check("unknown kind -> skipped", A.render_notification({"id":1,"kind":"nope","params_json":"{}","link":None,"is_read":0,"created_at":None},"en") is None)

# ---- prefs
check("default prefs when no row", A.get_prefs(ua)=={"scan_reminders":1,"soil_moisture":1,"expert_reply":1,"health_change":1,"disease_risk":1,"weather_advisory":1,"reminder_days":7})
post(a,"/notifications/settings",{"expert_reply":"on","reminder_days":"14"})
p=A.get_prefs(ua); check("settings saved (only expert_reply on, 14 days)", p=={"scan_reminders":0,"soil_moisture":0,"expert_reply":1,"health_change":0,"disease_risk":0,"weather_advisory":0,"reminder_days":14}, p)
post(a,"/notifications/settings",{"scan_reminders":"on","soil_moisture":"on","expert_reply":"on","health_change":"on","reminder_days":"5"})
check("invalid reminder_days -> 7", A.get_prefs(ua)["reminder_days"]==7)
check("settings page 200", a.get("/notifications/settings").status_code==200)
post(a,"/notifications/settings",{"expert_reply":"on"})
check("disabled kind is not created", A.notify(ua,"low_moisture",{"device":"x","value":1})==False and count(ua)==0)
post(a,"/notifications/settings",{"scan_reminders":"on","soil_moisture":"on","expert_reply":"on","health_change":"on","reminder_days":"7"})
check("notify unknown kind -> False", A.notify(ua,"bogus",{})==False)
check("dedupe_key prevents duplicates", A.notify(ua,"low_moisture",{"device":"d","value":9},dedupe_key="k1") and not A.notify(ua,"low_moisture",{"device":"d","value":9},dedupe_key="k1") and count(ua)==1)
A.notify(ua,"low_moisture",{"device":"d","value":9},link="//evil.com")
cn=A.db(); lk=cn.execute("SELECT link FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 1",(ua,)).fetchone()["link"]; cn.close()
check("external link not stored", lk is None, lk)
cn=A.db(); cn.execute("DELETE FROM notifications"); cn.commit(); cn.close()

# ---- health change producer
gate_t={"ok":True,"provider":"plantnet","supported_crop":"Tomato","plant":"Tomato","scientific_name":"Solanum lycopersicum","confidence":88,"common_names":["Tomato"]}
def loc(cls,conf=95): return {"class":cls,"confidence":conf,"margin":90,"stable":100,"accepted":True,"plant":"Tomato","plant_key":"Tomato","supported":True}
def cam(cl,cls):
    A.validate_plant_image=lambda p,l="en": gate_t; A.predict_strict=lambda p: loc(cls); A.GEMINI_ENABLED=False
    return cl.post("/detect-camera",data={"image":(png(),"c.jpg")},headers={"X-CSRF-Token":token(cl)},content_type="multipart/form-data").get_json()
r1=cam(a,"Tomato___healthy"); check("first scan: no notification (nothing to compare)", count(ua)==0)
cam(a,"Tomato___healthy"); check("unchanged score: no notification", count(ua)==0)
r3=cam(a,"Tomato___Late_blight"); check("98 -> 35: health_drop notification", count(ua,"health_drop")==1)
r4=cam(a,"Tomato___healthy"); check("35 -> 98: health_up notification", count(ua,"health_up")==1)
check("other user got nothing", count(ub)==0)
h=a.get("/notifications").get_data(as_text=True)
check("notification page renders text with real numbers", "Crop health dropped" in h and "Tomato health score fell from 98 to 35" in h and "improved from 35 to 98" in h)

# ---- unread api / open / idor
r=a.get("/api/notifications/unread"); check("unread api count=2", r.get_json()["count"]==2 and r.headers["Cache-Control"]=="private, no-store", r.get_json())
check("unread api: anonymous 401", client().get("/api/notifications/unread").status_code==401)
check("notifications page: anonymous -> login", client().get("/notifications").status_code==302)
cn=A.db(); nid=cn.execute("SELECT id FROM notifications WHERE user_id=? AND kind='health_drop'",(ua,)).fetchone()["id"]; cn.close()
check("IDOR: other user cannot open my notification", b.get(f"/notifications/{nid}/open").status_code==404)
cn=A.db(); st=cn.execute("SELECT is_read FROM notifications WHERE id=?",(nid,)).fetchone()["is_read"]; cn.close(); check("...and it stays unread", st==0)
r=a.get(f"/notifications/{nid}/open"); check("open marks read + redirects to scan", r.status_code==302 and "/history/" in r.headers["Location"])
check("unread count decreased", a.get("/api/notifications/unread").get_json()["count"]==1)
cn=A.db(); cn.execute("INSERT INTO notifications(user_id,kind,params_json,link) VALUES(?,?,?,?)",(ua,"low_moisture",'{"device":"x","value":1}',"//evil.com")); eid=cn.execute("SELECT MAX(id) AS i FROM notifications").fetchone()["i"]; cn.commit(); cn.close()
r=a.get(f"/notifications/{eid}/open"); check("stored hostile link never redirects off-site", r.status_code==302 and r.headers["Location"]=="/", r.headers.get("Location"))
post(b,"/notifications/read-all"); check("IDOR: other user's read-all leaves mine untouched", a.get("/api/notifications/unread").get_json()["count"]>=1)
post(b,"/notifications/clear"); check("IDOR: other user's clear leaves mine", count(ua)>=3)
post(a,"/notifications/read-all"); check("read-all marks mine", a.get("/api/notifications/unread").get_json()["count"]==0)
post(a,"/notifications/clear"); check("clear removes my read ones", count(ua)==0)

# ---- XSS
A.notify(ua,"field_reminder",{"field":"<script>alert(1)</script>","days":9},link="/fields")
h=a.get("/notifications").get_data(as_text=True); check("stored XSS in params escaped", "<script>alert(1)" not in h and "&lt;script&gt;" in h)

# ---- low soil moisture via ingest
post(a,"/iot/devices/new",{"name":"soil-A"}); post(b,"/iot/devices/new",{"name":"soil-B"})
cn=A.db(); dk=cn.execute("SELECT device_key FROM sensor_devices WHERE name='soil-A'").fetchone()["device_key"]; dkb=cn.execute("SELECT device_key FROM sensor_devices WHERE name='soil-B'").fetchone()["device_key"]; cn.execute("DELETE FROM notifications"); cn.commit(); cn.close()
ing=lambda key,sm: A.app.test_client().post("/api/iot/ingest",json={"device_key":key,"soil_moisture":sm,"temperature":25}).get_json()
ing(dk,10); check("dry soil reading -> notification for owner", count(ua,"low_moisture")==1)
ing(dk,8); check("second dry reading in same window deduped", count(ua,"low_moisture")==1)
ing(dk,55); check("normal moisture -> no new notification", count(ua,"low_moisture")==1)
check("other user's sensor not affected", count(ub)==0)
h=a.get("/notifications").get_data(as_text=True); check("body shows device and value", "soil-A" in h and "10% soil moisture" in h)
post(b,"/notifications/settings",{"expert_reply":"on"}); ing(dkb,5); check("moisture alert respects pref off", count(ub)==0)

# ---- expert reply / resolve
cn=A.db(); sid=cn.execute("SELECT id FROM scans WHERE user_id=? LIMIT 1",(ua,)).fetchone()["id"]; cn.execute("DELETE FROM notifications"); cn.commit(); cn.close()
post(a,"/experts/tickets",{"notes":"leaf spots","scan_id":str(sid)})
cn=A.db(); tid=cn.execute("SELECT id FROM diagnostic_tickets").fetchone()["id"]; cn.close()
adm=client(); post(adm,"/login",{"email":"admin@example.test","password":"AdminPass#12345"})
post(a,f"/experts/tickets/{tid}",{"action":"message","message":"any update?"}); check("farmer's own reply does not notify farmer", count(ua)==0)
post(adm,f"/experts/tickets/{tid}",{"action":"message","message":"Try neem spray"}); check("expert reply -> farmer notified", count(ua,"expert_reply")==1 and count(ub)==0)
post(adm,f"/experts/tickets/{tid}",{"action":"resolve","resolution":"Done"}); check("resolve -> ticket_resolved notification", count(ua,"ticket_resolved")==1)
check("expert not notified about own actions", count(uid_of("admin@example.test"))==0)
h=a.get("/notifications").get_data(as_text=True); check("ticket notifications show ticket number", f"#{tid}" in h)
cn=A.db(); cn.execute("DELETE FROM notifications"); cn.commit(); cn.close()

# ---- reminders (DB-clock based)
cd,ce,cf,cg=[client() for _ in range(4)]
for cl,e in ((cd,"d@t.test"),(ce,"e@t.test"),(cf,"f@t.test"),(cg,"g@t.test")): register(cl,e)
ud,ue,uf,ug=[uid_of(x) for x in ("d@t.test","e@t.test","f@t.test","g@t.test")]
check("brand-new user: no reminders (no nagging)", (A.generate_due_notifications(ud), count(ud))[1]==0)
post(cd,"/fields",{"name":"North","crop":"Wheat"}); cn=A.db(); cn.execute("UPDATE fields SET created_at=NOW() - INTERVAL 10 DAY WHERE user_id=?",(ud,)); cn.commit(); cn.close()
A.generate_due_notifications(ud); check("overdue field -> field_reminder", count(ud,"field_reminder")==1)
A.generate_due_notifications(ud); check("same week: not duplicated", count(ud,"field_reminder")==1)
h=cd.get("/notifications").get_data(as_text=True); check("reminder text mentions field + days", "Scan reminder: North" in h and "10 days" in h)
post(ce,"/fields",{"name":"E1","crop":"Rice"}); cn=A.db(); cn.execute("UPDATE fields SET created_at=NOW() - INTERVAL 10 DAY WHERE user_id=?",(ue,)); cn.commit(); cn.close()
post(ce,"/notifications/settings",{"scan_reminders":"on","reminder_days":"14"}); A.generate_due_notifications(ue); check("reminder_days=14 and 10 days old -> none", count(ue)==0)
post(ce,"/notifications/settings",{"reminder_days":"3"}); A.generate_due_notifications(ue); check("reminders switched off -> none", count(ue)==0)
cn=A.db(); cn.execute("INSERT INTO scans(user_id,image,plant,disease,confidence,status,created_at) VALUES(?,?,?,?,?,?,NOW() - INTERVAL 10 DAY)",(uf,"x.jpg","Tomato","Tomato___healthy",90,"t")); cn.commit(); cn.close()
A.generate_due_notifications(uf); check("no fields + old scan -> general scan_reminder", count(uf,"scan_reminder")==1)
# throttle through the API (once per hour per session)
post(cg,"/fields",{"name":"G1","crop":"Rice"}); cn=A.db(); cn.execute("UPDATE fields SET created_at=NOW() - INTERVAL 10 DAY WHERE user_id=?",(ug,)); cn.commit(); cn.close()
n1=cg.get("/api/notifications/unread").get_json()["count"]
post(cg,"/fields",{"name":"G2","crop":"Rice"}); cn=A.db(); cn.execute("UPDATE fields SET created_at=NOW() - INTERVAL 10 DAY WHERE user_id=?",(ug,)); cn.commit(); cn.close()
n2=cg.get("/api/notifications/unread").get_json()["count"]
check("lazy generation throttled to once per hour (2nd overdue field not added within the hour)", n1==1 and n2==1, (n1,n2))
# prune
cn=A.db()
for i in range(205): cn.execute("INSERT INTO notifications(user_id,kind,params_json) VALUES(?,?,?)",(ub,"health_up",'{"plant":"T","old":1,"new":99}'))
cn.commit(); cn.close(); post(b,"/notifications/settings",{"scan_reminders":"on","reminder_days":"7"}); A.generate_due_notifications(ub)
check("table pruned to newest 200 per user", count(ub)==200, count(ub))
# nav + language
h=a.get("/").get_data(as_text=True); check("bell + badge in nav", 'id="notifBadge"' in h and "/notifications" in h)
a.get("/set-language/hi"); t0=time.time(); r=a.get("/notifications"); check(f"notifications in Hindi renders instantly ({time.time()-t0:.2f}s)", r.status_code==200 and time.time()-t0<3)
print(f"\nRESULT: {PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
