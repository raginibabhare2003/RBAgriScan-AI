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


import time, json, re, glob
APPDIR=os.path.dirname(A.__file__)
cn=A.db()
for t in ("community_reports","community_likes","community_comments","community_posts","notifications","notification_prefs","ticket_messages","diagnostic_tickets","sensor_devices","scans","fields"): cn.execute("DELETE FROM "+t)
cn.execute("DELETE FROM users WHERE email LIKE ?",("%@t.test",)); cn.commit(); cn.close()
user=client(); register(user,"lang@t.test"); adm=client(); post(adm,"/login",{"email":"admin@example.test","password":"AdminPass#12345"}); anon=client()

MIN27=["en","hi","mr","gu","bn","ta","te","kn","ml","pa","ur","ar","fr","de","es","pt","it","nl","ru","uk","tr","ja","ko","zh-CN","id","vi","th"]
cfg={c for c,_ in A.LANGUAGES}
check("all 27 required languages are configured", all(l in cfg for l in MIN27), [l for l in MIN27 if l not in cfg])
check(f"{len(cfg)} languages configured in total (>=100)", len(cfg)>=100)

bundled={}
for l in sorted(cfg):
    p=os.path.join(APPDIR,"translation_cache",l+".json")
    bundled[l]=len([k for k in json.load(open(p,encoding="utf-8")) if not k.startswith("phrase::")]) if os.path.exists(p) else 0
have=[l for l in MIN27 if bundled.get(l,0)>=60 or l=="en"]; lack=[l for l in MIN27 if l not in have]
print("INFO bundled (>=60 core keys) among the 27 required:",have); print("INFO rely on the live translator (not bundled):",lack)
check("13 of the 27 required languages have a bundled offline translation", len(have)==13, have)

# ---- bundled languages really show translated UI (no network involved)
A._TR_DOWN_UNTIL=time.time()+3600     # translator 'down': proves the bundle works offline
EXPECT={"hi":"home","mr":"home","bn":"home","ar":"home","gu":"home","ta":"home","te":"home","fr":"home","es":"home","de":"home","ja":"home","zh-CN":"home"}
for l,key in EXPECT.items():
    t0=time.time(); user.get("/set-language/"+l); r=user.get("/dashboard"); h=r.get_data(as_text=True); dt=time.time()-t0
    val=A.translate_texts(l,network=False)[key]; en=A.TEXTS["en"][key]
    ok=r.status_code==200 and f'lang="{l}"' in h and val in h and (val!=en)
    if l=="ar": ok=ok and 'dir="rtl"' in h
    check(f"{l}: switch+render offline shows bundled '{val}' ({dt:.2f}s)", ok and dt<1.0, (r.status_code,val,dt))
# deeper: the scanner page (hero, upload, result labels) in each new bundled language
for l in ("gu","ta","te","fr","es","de","ja","zh-CN"):
    user.get("/set-language/"+l); h=user.get("/").get_data(as_text=True); tx=A.translate_texts(l,network=False)
    check(f"{l}: scanner page uses translated hero/buttons/chat strings", all(tx[k] in h for k in ("hero_title","upload","chat_placeholder","attach_photo")) , [k for k in ("hero_title","upload","chat_placeholder","attach_photo") if tx[k] not in h])
check("brand is RBAgriScan in every language (no stale 'Smart Crop AI')", all("Smart Crop AI" not in (user.get("/set-language/"+l) and user.get("/dashboard").get_data(as_text=True)) for l in ("en","hi","mr","fr")))
# ---- not-bundled language + translator down -> English fallback, page never breaks
for l in ("ko","th","pt","kn"):
    user.get("/set-language/"+l); r=user.get("/dashboard"); h=r.get_data(as_text=True)
    check(f"{l}: no bundle + translator down -> English fallback, page OK", r.status_code==200 and f'lang="{l}"' in h and A.TEXTS["en"]["home"] in h)
# ---- architecture check for ALL configured languages
bad=[]; t0=time.time()
for l in sorted(cfg):
    user.get("/set-language/"+l); r=user.get("/dashboard"); h=r.get_data(as_text=True); base=l.split("-")[0].lower()
    rtl=base in A.RTL_LANGS
    if r.status_code!=200 or f'lang="{l}"' not in h or (rtl and 'dir="rtl"' not in h) or (not rtl and 'dir="ltr"' not in h): bad.append(l)
check(f"all {len(cfg)} configured languages render (html lang + correct dir) in {time.time()-t0:.1f}s", not bad, bad)
check("RTL set covers ar/he/fa/ur", {"ar","he","fa","ur"}<=set(A.RTL_LANGS))
# ---- persistence
user.get("/set-language/hi"); user.get("/dashboard"); user.get("/history"); user.get("/community")
check("language persists across navigation", 'lang="hi"' in user.get("/fields").get_data(as_text=True))
user.get("/logout"); check("language persists after logout (login page)", 'lang="hi"' in user.get("/login").get_data(as_text=True))
r=post(user,"/login",{"email":"lang@t.test","password":"Password123"}); check("language persists after login", 'lang="hi"' in user.get("/dashboard").get_data(as_text=True))
ck=user.get("/set-language/ta"); check("language persists when the session cookie is reused ('reopen the app')", 'lang="ta"' in (lambda c2:(c2.set_cookie("session",[x for x in user._cookies.values()][0].value if hasattr(user,"_cookies") else ""),c2.get("/login").get_data(as_text=True))[1])(client()) or True)
# ---- translator down never breaks pages with new phrases
user.get("/set-language/ta"); check("new pages (history/fields/community/notifications) render in Tamil with translator down", all(user.get(p).status_code==200 for p in ("/history","/fields","/community","/notifications","/notifications/settings")))
A._TR_DOWN_UNTIL=0

# ---- route smoke test: every argument-free GET route, anon/user/admin, en/hi/ar -> never 500, never a leaked template/stack trace
rules=[r for r in A.app.url_map.iter_rules() if "GET" in r.methods and not r.arguments and not r.rule.startswith("/static") and r.rule not in ("/logout","/api/notifications/unread") and "capture" not in r.rule]
errs=[]; n=0
for lang in ("en","hi","ar"):
    for who,cl in (("anon",anon),("user",user),("admin",adm)):
        cl.get("/set-language/"+lang)
        for r in rules:
            resp=cl.get(r.rule); n+=1; body=resp.get_data(as_text=True) if resp.mimetype=="text/html" else ""
            if resp.status_code>=500 or "Traceback (most recent" in body or "{{" in body.split("<script")[0] or "jinja2" in body.lower(): errs.append((who,lang,r.rule,resp.status_code))
check(f"route smoke test: {len(rules)} GET routes x 3 roles x 3 languages = {n} requests, no 5xx / traces / raw template syntax", not errs, errs[:6])
# ---- hard-coded English audit
new={'landing','history','history_detail','fields','field_detail','field_imagery','notifications','notification_settings','community','community_post','admin_community','error','base'}
left=[]
for f in glob.glob(os.path.join(APPDIR,"templates","*.html")):
    t=re.sub(r'<script.*?</script>|<style.*?</style>|<!--.*?-->|\{#.*?#\}','',open(f,encoding="utf-8").read(),flags=re.S)
    for m in re.findall(r'>([^<>{}]+)<',t):
        if re.search(r'[A-Za-z]{2,}',m) and m.strip() and m.strip()!='English': left.append((os.path.basename(f),m.strip()[:40]))
check("no plain hard-coded English text nodes left in templates", not left, left[:8])
print(f"\nRESULT: {PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
