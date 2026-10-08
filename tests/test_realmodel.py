"""Tests that use the REAL model.tflite (no mocks for the model). Needs the same DB env as the other tests."""
import os, sys, io, time, threading
import tempfile
TMP = tempfile.gettempdir()
for _k, _v in dict(MYSQL_HOST="127.0.0.1", MYSQL_PORT="3306", MYSQL_USER="root", MYSQL_PASSWORD="", MYSQL_DATABASE="smart_crop_ai_test").items():
    os.environ.setdefault(_k, _v)   # override with environment variables
if not os.environ["MYSQL_DATABASE"].endswith("_test"):
    sys.exit("Refusing to run: these tests DELETE data. Set MYSQL_DATABASE to a dedicated empty test database whose name ends with _test.")
os.environ.update(MYSQL_HOST=os.environ.get("MYSQL_HOST","127.0.0.1"), MYSQL_USER=os.environ.get("MYSQL_USER","root"),
                  MYSQL_PASSWORD=os.environ.get("MYSQL_PASSWORD","testpw"), MYSQL_DATABASE=os.environ.get("MYSQL_DATABASE","smart_crop_ai"),
                  SECRET_KEY=os.environ.get("SECRET_KEY","test-secret-key-for-tests-only-0123456789"))
sys.path.insert(0, os.getcwd())
import numpy as np
from PIL import Image, ImageDraw
import app as A
PASS=FAIL=0
def check(n,c,x=""):
    global PASS,FAIL
    if c: PASS+=1; print("PASS",n)
    else: FAIL+=1; print("FAIL",n,x)
check("real model loaded", A.interpreter is not None and len(A.labels)==A.output_details[0]["shape"][1], (len(A.labels), A.output_details[0]["shape"]))
# synthetic leaf-like test image: green leaf on white with a brown blotch
im=Image.new("RGB",(400,400),(240,240,240)); d=ImageDraw.Draw(im); d.ellipse((60,40,340,360),fill=(50,140,50)); d.ellipse((190,150,270,230),fill=(110,70,30))
p=os.path.join(TMP, "_leaf.png"); im.save(p)
r=A.predict_strict(p); check("predict_strict returns a full result", {"class","confidence","accepted","plant_key"}<=set(r), r)
cls_idx=A.labels.index(r["class"])   # the route explains the STORED class, exactly like this
t=time.time(); res=A.occlusion_heatmap(p, cls_idx); dt=time.time()-t
H,W=res["heat"].shape
check(f"occlusion heatmap runs on the real model ({dt:.1f}s)", dt<20 and res["heat"].min()>=0 and res["heat"].max()<=1.0001, dt)
check("heatmap matches the (thumbnailed) image size", (W,H)==res["image"].size)
ov=A.heatmap_overlay(res); check("overlay image built", ov.size==res["image"].size and ov.mode=="RGB")
check("heatmap explains the requested (stored) class and reports its probability", res["class_idx"]==cls_idx and abs(res["confidence"]-float(A.raw_predict(A.preprocess(p))[cls_idx]))<1e-5)
check("(info) single-view argmax vs 3-view predict_strict on a synthetic image: %s vs %s" % (A.labels[int(np.argmax(A.raw_predict(A.preprocess(p))))], r["class"]), True)
flat=Image.new("RGB",(300,300),(128,128,128)); flat.save(os.path.join(TMP, "_flat.png")); rf=A.occlusion_heatmap(os.path.join(TMP, "_flat.png"))
check("flat image: heatmap well-formed (focused flag tells if any region dominates)", rf["heat"].shape==(rf["image"].size[1],rf["image"].size[0]) and isinstance(rf["focused"],bool), rf["focused"])
# thread safety: concurrent inference must equal sequential inference
arrA=A.preprocess(p); arrB=A.preprocess(os.path.join(TMP, "_flat.png"))
seqA=A.raw_predict(arrA).copy(); seqB=A.raw_predict(arrB).copy(); bad=[]
def work(k):
    for _ in range(40):
        a=A.raw_predict(arrA if k%2==0 else arrB); ref=seqA if k%2==0 else seqB
        if not np.allclose(a,ref,atol=1e-5): bad.append(k)
ts=[threading.Thread(target=work,args=(k,)) for k in range(8)]; [t.start() for t in ts]; [t.join() for t in ts]
check("8 threads x 40 inferences: identical to sequential (no interpreter race)", not bad, len(bad))
# full pipeline with the real model (plant validator mocked: this machine has no PlantNet/Gemini keys)
A.validate_plant_image=lambda path,lang="en": {"ok":True,"provider":"plantnet","supported_crop":"Tomato","plant":"Tomato","scientific_name":"Solanum lycopersicum","confidence":90,"common_names":["Tomato"]}
A.GEMINI_ENABLED=False
out=A.analyze_plant_image(p,"en")
check("pipeline with real model returns a structured result", "unknown" in out and ("class" in out or out.get("unknown")), out)
A.validate_plant_image=lambda path,lang="en": {"ok":False,"reason":"not_a_plant"}
nm=A.analyze_plant_image(os.path.join(TMP, "_flat.png"),"en"); check("real model + failed plant validation -> Plant Not Identified (model alone would have accepted it)", nm["unknown"] and nm["confidence"]==0 and A.predict_strict(os.path.join(TMP, "_flat.png"))["accepted"], nm)
print(f"\nRESULT: {PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
