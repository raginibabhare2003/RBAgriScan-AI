# RBAgriScan – File Type Guide (Hinglish)

- `model.tflite` → binary ML model; source comments possible nahi. Model ko manually edit mat karo.
- `labels.txt` → model output labels; **har line label hai**, isliye explanatory comments add nahi kiye gaye because parser labels ko class names samajh sakta hai.
- `static/icons/*.png` → binary image/logo files; comments possible nahi. Mapping `static/icons/FOLDER_GUIDE.md` me hai.
- `static/manifest.json` → JSON metadata; standard JSON comments allow nahi karta. Explanation `CODE_MAP.md` me hai.
- `runtime.txt` → Python runtime version for deployment.
- `.gitignore` → Git ko secrets/runtime files ignore karne ke rules.
