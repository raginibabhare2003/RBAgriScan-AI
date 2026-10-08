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


import time, glob
cn=A.db()
for t in ("community_reports","community_likes","community_comments","community_posts","notifications","notification_prefs","ticket_messages","diagnostic_tickets","sensor_devices","scans","fields"): cn.execute("DELETE FROM "+t)
cn.execute("DELETE FROM users WHERE email LIKE ?",("%@t.test",)); cn.commit(); cn.close()
def uid_of(e):
    cn=A.db(); r=cn.execute("SELECT id FROM users WHERE email=?",(e,)).fetchone(); cn.close(); return r["id"]
def newuser(e):
    c=client(); register(c,e); return c
a,b,r1,r2,r3=[newuser(f"{x}@t.test") for x in ("a","b","r1","r2","r3")]
adm=client(); post(adm,"/login",{"email":"admin@example.test","password":"AdminPass#12345"})

# ================= CHAT: photo + language =================
gate_ok={"ok":True,"provider":"plantnet","supported_crop":"Tomato","plant":"Tomato","scientific_name":"Solanum lycopersicum","confidence":88,"common_names":["Tomato"]}
gate_no={"ok":False,"reason":"not_a_plant","plant":"Unknown","confidence":0}
calls=[]
def fake_gem(prompt,image_path=None): calls.append((prompt,image_path)); return "Use neem oil. Keep leaves dry."
A._gemini_text=fake_gem
def chat(cl,msg="what is wrong?",img=True,name="leaf.png",data=None):
    d={"message":msg}
    if img: d["image"]=(data or png(),name)
    return cl.post("/api/chat",data=d,headers={"X-CSRF-Token":token(cl)},content_type="multipart/form-data")
def uploads(): return set(glob.glob(os.path.join(A.UPLOAD_FOLDER,"*chat_*")))
A.GEMINI_ENABLED=True
A.validate_plant_image=lambda p,l="en": gate_no; before=uploads(); calls.clear()
r=chat(a); j=r.get_json()
check("chat photo: non-plant -> Plant Not Identified, Gemini NEVER called", r.status_code==200 and j["plant_identified"] is False and "Plant Not Identified" in j["answer"] and not calls, (j,calls))
check("chat photo: file not kept", uploads()==before)
A.validate_plant_image=lambda p,l="en": {"ok":False,"reason":"validator_unavailable"}; calls.clear()
j=chat(a).get_json(); check("chat photo: validator down -> no Gemini, honest message", not calls and "not available" in j["answer"])
A.validate_plant_image=lambda p,l="en": gate_ok; calls.clear()
j=chat(a).get_json(); check("chat photo: plant -> Gemini answers with image", j["ok"] and j["detected_plant"]=="Tomato" and "neem" in j["answer"] and calls and calls[0][1] and "Tomato" in calls[0][0], j)
check("chat photo: temp file removed after answer", uploads()==before)
a.get("/set-language/hi"); calls.clear(); chat(a,msg="my leaf has spots")
check("chat answers in the SELECTED language (Hindi) even if typed in English", calls and "Hindi" in calls[0][0], calls[0][0][:120] if calls else None)
a.get("/set-language/en")
A.GEMINI_ENABLED=False; A.predict_strict=lambda p: {"class":"Tomato___Late_blight","confidence":97.0,"margin":90,"stable":100,"accepted":True,"plant":"Tomato","plant_key":"Tomato","supported":True}
gate_t=dict(gate_ok); A.validate_plant_image=lambda p,l="en": gate_t
j=chat(a).get_json(); check("chat photo without Gemini: grounded local guidance", "Late blight" in j["answer"] and "Prevention" in j["answer"] and j["source"]=="local_guidance", j)
check("chat photo: bad extension 400", chat(a,name="x.exe").status_code==400)
check("chat photo: corrupt image 400", chat(a,data=io.BytesIO(b"not an image"),name="x.png").status_code==400)
check("chat: anonymous 401", client().post("/api/chat",json={"message":"hi"}).status_code in (401,403))
r=a.post("/api/chat",json={"message":"how to water tomato?"},headers={"X-CSRF-Token":token(a)}); check("chat: plain text still works (offline assistant)", r.get_json()["ok"])
h=a.get("/").get_data(as_text=True); js=open(os.path.join(os.path.dirname(A.__file__),"static/js/app.js"),encoding="utf-8").read()
check("voice+photo UI present (mic, attach, speak toggle)", all(x in h for x in ('id="vaMic"','id="chatAttach"','id="chatSpeak"','id="voiceHint"')))
check("voice JS: SpeechRecognition + speechSynthesis + typed fallback + denied message", all(x in js for x in ("webkitSpeechRecognition","SpeechSynthesisUtterance","voice_unsupported","voice_denied","not-allowed")))
check("Permissions-Policy allows microphone for this origin", "microphone=(self)" in a.get("/").headers["Permissions-Policy"])

# ================= COMMUNITY =================
A._RATE_BUCKETS.clear()
def newpost(cl,**kw):
    d={"kind":"question","title":"Yellow leaves on tomato","body":"My tomato leaves turn yellow after rain. What should I do?","plant":"Tomato"}; d.update(kw)
    return post(cl,"/community/new",d,content_type="multipart/form-data") if False else cl.post("/community/new",data={**d,"_csrf":token(cl)},content_type="multipart/form-data")
def rows(q,*a_):
    cn=A.db(); r=cn.execute(q,a_).fetchall(); cn.close(); return r
newpost(a,title="x",body="y"); check("too short rejected", len(rows("SELECT id FROM community_posts"))==0)
newpost(a,body="Buy now http://a.com http://b.com http://c.com"); check("link spam rejected", len(rows("SELECT id FROM community_posts"))==0)
r=newpost(a); pid=rows("SELECT id FROM community_posts")[0]["id"]; check("post created -> redirect to post", r.status_code==302 and f"/community/{pid}" in r.headers["Location"])
newpost(a); check("duplicate post within 1h rejected", len(rows("SELECT id FROM community_posts"))==1)
A._RATE_BUCKETS.clear()
for i in range(6): newpost(a,title=f"Tip number {i}",body=f"A useful farming tip number {i} about irrigation timing")
n=len(rows("SELECT id FROM community_posts")); check("rate limit: max 5 posts / 10 min per user (6th attempt rejected)", n==6, n)  # 1 earlier + 5 allowed
A._RATE_BUCKETS.clear()
h=b.get("/community").get_data(as_text=True); check("feed shows posts to other users", "Yellow leaves on tomato" in h and "Question" in h)
check("feed filter kind=post excludes question", "Yellow leaves on tomato" not in b.get("/community?kind=post").get_data(as_text=True))
check("post page loads", b.get(f"/community/{pid}").status_code==200)
check("anonymous -> login", client().get("/community").status_code==302 and client().get(f"/community/{pid}").status_code==302)
# XSS
b.post("/community/new",data={"kind":"post","title":"<script>alert(1)</script>","body":"<img src=x onerror=alert(2)> hello there","plant":"<svg onload=alert(3)>","_csrf":token(b)},content_type="multipart/form-data")
xid=rows("SELECT id FROM community_posts WHERE user_id=?",uid_of("b@t.test"))[0]["id"]
h=a.get("/community").get_data(as_text=True)+a.get(f"/community/{xid}").get_data(as_text=True)
check("stored XSS escaped in feed + post page", "<script>alert(1)" not in h and "<img src=x onerror" not in h and "<svg onload" not in h and "&lt;script&gt;" in h)
# image post
rp=b.post("/community/new",data={"kind":"post","title":"Healthy field photo","body":"Look at my healthy wheat field today","image":(png(),"w.png"),"_csrf":token(b)},content_type="multipart/form-data")
ip=rows("SELECT id,image FROM community_posts WHERE title='Healthy field photo'")[0]
check("image post stored with server-generated name", ip["image"] and ip["image"].startswith("u") and "w.png" not in ip["image"])
check("community image visible to other logged-in users via /media", a.get("/media/"+ip["image"]).status_code==200)
check("community image not public", client().get("/media/"+ip["image"]).status_code==302)
bad=b.post("/community/new",data={"kind":"post","title":"Bad image","body":"corrupt image test post","image":(io.BytesIO(b"nope"),"x.png"),"_csrf":token(b)},content_type="multipart/form-data")
check("corrupt post image rejected, no post", not rows("SELECT id FROM community_posts WHERE title='Bad image'"))
# likes
post(b,f"/community/{pid}/like"); h=a.get("/community").get_data(as_text=True)
check("like adds 1", rows("SELECT COUNT(*) AS n FROM community_likes WHERE post_id=?",pid)[0]["n"]==1)
post(b,f"/community/{pid}/like"); check("like toggles off", rows("SELECT COUNT(*) AS n FROM community_likes WHERE post_id=?",pid)[0]["n"]==0)
post(b,f"/community/{pid}/like"); post(a,f"/community/{pid}/like"); check("two users like -> 2", rows("SELECT COUNT(*) AS n FROM community_likes WHERE post_id=?",pid)[0]["n"]==2)
check("like on missing post 404", post(b,"/community/999999/like").status_code==404)
# comments / replies
post(b,f"/community/{pid}/comment",{"body":"Check for overwatering and fungus."}); cid=rows("SELECT id FROM community_comments")[0]["id"]
post(a,f"/community/{pid}/comment",{"body":"Thanks! Will try.","parent_id":str(cid)})
check("comment + reply stored", len(rows("SELECT id FROM community_comments"))==2)
rid=rows("SELECT id FROM community_comments WHERE parent_id IS NOT NULL")[0]["id"]
check("reply to a reply rejected (1 level only)", post(a,f"/community/{pid}/comment",{"body":"deep","parent_id":str(rid)}).status_code==404)
check("reply with parent from another post rejected", post(a,f"/community/{xid}/comment",{"body":"x","parent_id":str(cid)}).status_code==404)
post(b,f"/community/{pid}/comment",{"body":"Check for overwatering and fungus."}); check("duplicate comment rejected", len(rows("SELECT id FROM community_comments"))==2)
post(b,f"/community/{pid}/comment",{"body":"spam http://a.com http://b.com http://c.com"}); check("comment link spam rejected", len(rows("SELECT id FROM community_comments"))==2)
post(a,f"/community/{pid}/comment",{"body":"<script>alert(9)</script> ok"}); h=a.get(f"/community/{pid}").get_data(as_text=True)
check("comment XSS escaped; replies shown nested", "<script>alert(9)" not in h and "Thanks! Will try." in h and "Check for overwatering" in h)
A._RATE_BUCKETS.clear()
for i in range(21): post(r1,f"/community/{pid}/comment",{"body":f"unique comment number {i} here"})
n=rows("SELECT COUNT(*) AS n FROM community_comments WHERE user_id=?",uid_of("r1@t.test"))[0]["n"]; check("rate limit: max 20 comments / 10 min", n==20, n)
A._RATE_BUCKETS.clear()
# expert badge
cn=A.db(); cn.execute("UPDATE users SET role='agronomist' WHERE email='r2@t.test'"); cn.commit(); cn.close()
r2.post("/community/new",data={"kind":"post","title":"Expert advice on blight","body":"Rotate crops and avoid overhead watering","_csrf":token(r2)},content_type="multipart/form-data")
adm.post("/community/new",data={"kind":"post","title":"Admin announcement","body":"Welcome to the community everyone","_csrf":token(adm)},content_type="multipart/form-data")
h=a.get("/community").get_data(as_text=True)
check("expert badge for agronomist and admin authors", h.count("✔ Expert")>=2 and "Expert advice on blight" in h)
# reports / moderation
check("cannot report own post", (post(a,f"/community/{pid}/report",{"reason":"spam"}), rows("SELECT report_count FROM community_posts WHERE id=?",pid)[0]["report_count"])[1]==0)
post(r1,f"/community/{pid}/report",{"reason":"spam"}); post(r1,f"/community/{pid}/report",{"reason":"spam"})
check("same user cannot report twice", rows("SELECT report_count FROM community_posts WHERE id=?",pid)[0]["report_count"]==1)
check("not hidden yet after 1 report", rows("SELECT status FROM community_posts WHERE id=?",pid)[0]["status"]=="visible")
post(r2,f"/community/{pid}/report",{"reason":"abuse"}); post(r3,f"/community/{pid}/report",{"reason":"nonsense-reason"})
check("auto-hidden after 3 distinct reports", rows("SELECT status,report_count FROM community_posts WHERE id=?",pid)[0]["status"]=="hidden")
check("hidden post not in others' feed", "Yellow leaves on tomato" not in b.get("/community").get_data(as_text=True))
check("hidden post 404 for others", b.get(f"/community/{pid}").status_code==404)
check("owner still sees own hidden post with notice", "under review" in a.get("/community").get_data(as_text=True) and a.get(f"/community/{pid}").status_code==200)
check("comment/like on hidden post blocked", post(b,f"/community/{pid}/comment",{"body":"late comment"}).status_code==404 and post(b,f"/community/{pid}/like").status_code==404)
check("moderation page: admin only", b.get("/admin/community").status_code in (302,403) and adm.get("/admin/community").status_code==200)
check("moderation queue lists hidden post", "Yellow leaves on tomato" in adm.get("/admin/community").get_data(as_text=True))
check("non-admin cannot restore/delete via admin route", post(b,f"/admin/community/post/{pid}/restore").status_code in (302,403) and rows("SELECT status FROM community_posts WHERE id=?",pid)[0]["status"]=="hidden")
post(adm,f"/admin/community/post/{pid}/restore"); check("admin restore -> visible, reports cleared", rows("SELECT status,report_count FROM community_posts WHERE id=?",pid)[0]=={"status":"visible","report_count":0} and not rows("SELECT id FROM community_reports WHERE target_type='post' AND target_id=?",pid))
check("restored post visible again", b.get(f"/community/{pid}").status_code==200)
# comment reports
for cl in (r1,r2,r3): post(cl,f"/community/comment/{cid}/report",{"reason":"spam"})
check("comment auto-hidden after 3 reports", rows("SELECT status FROM community_comments WHERE id=?",cid)[0]["status"]=="hidden")
check("hidden comment not shown to others", "Check for overwatering" not in a.get(f"/community/{pid}").get_data(as_text=True))
# delete
check("other user cannot delete my post (404)", post(b,f"/community/{pid}/delete").status_code==404 and rows("SELECT id FROM community_posts WHERE id=?",pid))
check("other user cannot delete my comment", post(b,f"/community/comment/{rid}/delete").status_code==404 and rows("SELECT id FROM community_comments WHERE id=?",rid))
post(a,f"/community/comment/{rid}/delete"); check("author deletes own comment", not rows("SELECT id FROM community_comments WHERE id=?",rid))
imgfile=os.path.join(A.UPLOAD_FOLDER,ip["image"]); check("image file exists before delete", os.path.isfile(imgfile))
post(b,f"/community/{ip['id']}/delete"); check("owner deletes post: rows + image file removed", not rows("SELECT id FROM community_posts WHERE id=?",ip["id"]) and not os.path.isfile(imgfile))
post(a,f"/community/{pid}/delete"); check("delete cascades comments/likes/reports", not rows("SELECT id FROM community_comments WHERE post_id=?",pid) and not rows("SELECT 1 FROM community_likes WHERE post_id=?",pid) and not rows("SELECT id FROM community_reports WHERE target_type='post' AND target_id=?",pid))
newpost(a,title="Another one",body="Something to be removed by admin soon"); p2=rows("SELECT id FROM community_posts WHERE title='Another one'")[0]["id"]
post(adm,f"/community/{p2}/delete"); check("admin can delete any post", not rows("SELECT id FROM community_posts WHERE id=?",p2))
a.get("/set-language/hi"); t0=time.time(); r=a.get("/community"); check(f"community in Hindi renders instantly ({time.time()-t0:.2f}s)", r.status_code==200 and time.time()-t0<3)
print(f"\nRESULT: {PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
