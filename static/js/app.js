/* ============================================================
   RBAgriScan FRONTEND JAVASCRIPT
   Hinglish: Ye browser-side code hai. Buttons, camera, image
   preview, Plant/Soil UI, chatbot display aur common interactions
   yahan handle hote hain. Actual AI prediction backend app.py karta hai.
   ============================================================ */
/* RBAgriScan - shared front-end behavior for every page. */
(function () {
  "use strict";

  const T = (key, fallback) => (window.APP_TEXTS && window.APP_TEXTS[key]) || fallback || key;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
  }
  window.escapeHtml = escapeHtml;
  // Same treatment/prevention sections as the upload result page: camera and upload share one backend pipeline.
  function renderFirstSteps(g) {
    if (!g) return "";
    const items = []; ["home", "natural", "prevention"].forEach((k) => { if (g[k] && g[k].length) items.push(g[k][0]); });
    if (!items.length) return "";
    return '<div class="firststeps"><h3>✅ ' + escapeHtml(T("do_first", "Do this first")) + "</h3><ol>" + items.map((x) => "<li>" + escapeHtml(String(x)) + "</li>").join("") + "</ol></div>";
  }
  function renderGuidance(g) {
    if (!g) return "";
    var sections = [["home_remedies", "Home remedies", "home"], ["natural", "Natural treatment", "natural"],
                    ["field", "Field treatment", "field"], ["chemical", "Chemical advisory", "chemical"], ["prevention", "Prevention", "prevention"]];
    var html = "";
    sections.forEach(function (sec) {
      var items = g[sec[2]] || [];
      if (!items.length) return;
      html += "<h4>" + escapeHtml(T(sec[0], sec[1])) + "</h4><ul>" + items.map(function (x) { return "<li>" + escapeHtml(String(x)) + "</li>"; }).join("") + "</ul>";
    });
    return html;
  }
  // Safe, lightweight Markdown rendering for Gemini responses.
  // Gemini sometimes returns escaped Markdown such as \*\*bold\*\*, \### headings
  // or \--- separators. Render the useful formatting without allowing HTML injection.
  function renderChatMarkdown(text) {
    const raw = String(text || "").replace(/\\([*_#>\-])/g, "$1");
    return raw.split(/\r?\n/).map((line) => {
      let s = escapeHtml(line.trim());
      if (!s) return "<div class=\"chat-spacer\"></div>";
      if (/^#{1,3}\s+/.test(line.trim())) {
        const content = s.replace(/^#{1,3}\s+/, "");
        return "<h4 class=\"chat-heading\">" + content + "</h4>";
      }
      if (/^---+$/.test(line.trim())) return "<hr>";
      if (/^[-*•]\s+/.test(line.trim())) {
        return "<div class=\"chat-bullet\">• " + s.replace(/^[-*•]\s+/, "") + "</div>";
      }
      s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
      s = s.replace(/__(.+?)__/g, "<strong>$1</strong>");
      s = s.replace(/(^|[\s(])\*(?!\s)(.+?)(?<!\s)\*(?=$|[\s).,!?])/g, "$1<em>$2</em>");
      return "<div>" + s + "</div>";
    }).join("");
  }
  window.renderChatMarkdown = renderChatMarkdown;

  // Security: attach the per-session CSRF token to every normal HTML form.
  // API calls made by this file send the same token in X-CSRF-Token.
  function initCsrf() {
    document.querySelectorAll("form").forEach(form => {
      if (form.method.toLowerCase() === "get") return;
      if (!form.querySelector('input[name="_csrf"]')) {
        const input = document.createElement("input"); input.type="hidden"; input.name="_csrf"; input.value=window.CSRF_TOKEN||""; form.appendChild(input);
      }
    });
  }
  const _fetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    init = init || {};
    const headers = new Headers(init.headers || {});
    if (window.CSRF_TOKEN && String(input).startsWith("/")) headers.set("X-CSRF-Token", window.CSRF_TOKEN);
    init.headers = headers;
    return _fetch(input, init);
  };


  // Instant language UX: common built-in languages are already embedded by Flask,
  // so switch without a full navigation. This keeps Marathi/Hindi changes under
  // a second on normal devices. Unknown/new languages still use the server route.
  window.applyInstantLanguage = function(lang, texts) {
    if (!lang || !texts) return false;
    document.documentElement.lang = lang;
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      if (texts[key]) el.textContent = texts[key];
    });
    return true;
  };

  function initShareResult() {
    const btn=document.getElementById('shareResultBtn');
    if(!btn) return;
    btn.addEventListener('click', async ()=>{
      const title=document.querySelector('#plant-result h2')?.textContent || T('identified_plant','Plant result');
      const text=document.querySelector('#plant-result')?.innerText || title;
      try {
        if(navigator.share) await navigator.share({title, text, url:location.href});
        else await navigator.clipboard.writeText(text + '\n' + location.href);
        btn.textContent='✅ '+T('share_result','Shared');
      } catch(e) {}
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initCsrf();
    initNavToggle();
    initConnectivityBanner();
    initInstallPrompt();
    initServiceWorker();
    initImagePreview();
    initCamera();
    initChat();
    initSoilAnalysis();
    initWeather();
    initPlantHospitalOfflineQueue();
    initShareResult();
  });

  // ---------------- Nav ----------------
  function initNavToggle() {
    const btn = document.querySelector(".nav-toggle");
    const links = document.querySelector(".nav-links");
    if (!btn || !links) return;
    btn.addEventListener("click", () => links.classList.toggle("open"));
  }

  // ---------------- Online / offline banner ----------------
  function initConnectivityBanner() {
    const banner = document.getElementById("connBanner");
    if (!banner) return;
    function render() {
      if (navigator.onLine) {
        banner.textContent = "🌱 " + T("back_online", "Back online.");
        banner.className = "conn-banner online show";
        setTimeout(() => banner.classList.remove("show"), 2500);
      } else {
        banner.textContent = "📡 " + T("offline", "You're offline") + ". " + T("offline_browse", "Browsing cached pages - detection, weather and chat need a connection.");
        banner.className = "conn-banner offline show";
      }
    }
    window.addEventListener("online", render);
    window.addEventListener("offline", render);
    if (!navigator.onLine) render();
  }

  // ---------------- PWA install prompt ----------------
  function initInstallPrompt() {
    const btn = document.getElementById("installBtn");
    let deferredPrompt = null;
    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      deferredPrompt = e;
      if (btn) btn.classList.add("show");
    });
    if (btn) {
      btn.addEventListener("click", async () => {
        if (!deferredPrompt) return;
        btn.classList.remove("show");
        deferredPrompt.prompt();
        await deferredPrompt.userChoice;
        deferredPrompt = null;
      });
    }
    window.addEventListener("appinstalled", () => { if (btn) btn.classList.remove("show"); });
  }

  // ---------------- Service worker + update toast ----------------
  function initServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    // Camera capture and detection need HTTPS/localhost anyway; SW registration
    // silently no-ops on plain http:// origins other than localhost.
    navigator.serviceWorker.register("/sw.js").then((reg) => {
      reg.addEventListener("updatefound", () => {
        const installing = reg.installing;
        if (!installing) return;
        installing.addEventListener("statechange", () => {
          if (installing.state === "installed" && navigator.serviceWorker.controller) {
            showUpdateToast(reg);
          }
        });
      });
    }).catch(() => {});
  }

  function showUpdateToast(reg) {
    const toast = document.getElementById("updateToast");
    if (!toast) return;
    toast.classList.add("show");
    const btn = toast.querySelector("button");
    if (btn) {
      btn.addEventListener("click", () => {
        if (reg.waiting) reg.waiting.postMessage("SKIP_WAITING");
        navigator.serviceWorker.addEventListener("controllerchange", () => location.reload());
      });
    }
  }

  // ---------------- Image preview (upload form) ----------------
  function initImagePreview() {
    const input = document.getElementById("imageInput");
    const prev = document.getElementById("prev");
    if (!input || !prev) return;
    input.addEventListener("change", () => {
      if (input.files[0]) {
        prev.src = URL.createObjectURL(input.files[0]);
        prev.style.display = "block";
      }
    });
  }

  // ---------------- Camera capture ----------------
  // Hinglish: Plant aur Soil dono ke liye separate camera controls hain.
  // Photo capture ke turant baad camera stream STOP + modal CLOSE hota hai,
  // phir AI result respective section ke niche show hota hai.
  function initCamera() {
    initPlantCamera();
    initSoilCamera();
  }

  function cameraIsLocalHost() {
    return ["localhost", "127.0.0.1", "::1"].includes(location.hostname);
  }

  function cameraSecureError(box, message) {
    if (box) box.innerHTML = '<div class="error" role="alert">📷 ' + escapeHtml(message) + '</div>';
  }

  function initPlantCamera() {
    const camBox = document.getElementById("cam");
    const openBtn = document.getElementById("openCameraBtn");
    const video = document.getElementById("video");
    const canvas = document.getElementById("canvas");
    const modalResult = document.getElementById("camResult");
    const inlineResult = document.getElementById("cameraDetectionResult");
    if (!camBox || !openBtn || !video || !canvas) return;

    let stream = null;
    let facingMode = "environment";

    function closeCamera() {
      if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
      video.srcObject = null;
      camBox.style.display = "none";
    }

    async function startCamera() {
      if (!window.isSecureContext && !cameraIsLocalHost()) {
        cameraSecureError(modalResult, T("camera_secure", "Camera needs a secure HTTPS connection on mobile browsers."));
        return false;
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        cameraSecureError(modalResult, T("camera_unavailable", "Camera is not available in this browser."));
        return false;
      }
      if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: facingMode }, width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false
        });
        video.srcObject = stream;
        await video.play().catch(() => {});
        if (modalResult) modalResult.innerHTML = "";
        return true;
      } catch (e) {
        let msg = T("camera_permission", "Camera permission is required.");
        if (e.name === "NotAllowedError" || e.name === "SecurityError") msg = T("camera_denied", "Camera permission was denied. Please allow camera access for this site.");
        else if (e.name === "NotFoundError" || e.name === "OverconstrainedError") msg = T("camera_not_found", "No usable camera was found on this device.");
        else if (e.name === "NotReadableError") msg = T("camera_in_use", "The camera is already in use by another app.");
        cameraSecureError(modalResult, msg);
        return false;
      }
    }

    async function openCamera() {
      camBox.style.display = "flex";
      await startCamera();
    }

    async function flipCamera() {
      facingMode = facingMode === "environment" ? "user" : "environment";
      await startCamera();
    }

    async function capture() {
      if (!stream || !video.videoWidth) {
        cameraSecureError(modalResult, T("camera_not_ready", "Camera is not ready yet. Please wait a moment and try again."));
        return;
      }
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);

      canvas.toBlob(async blob => {
        if (!blob) {
          cameraSecureError(modalResult, T("camera_capture_failed", "Could not capture the photo. Please try again."));
          return;
        }
        // IMPORTANT: close camera immediately after successful capture.
        closeCamera();
        if (!navigator.onLine) {
          if (inlineResult) {
            inlineResult.style.display = "block";
            inlineResult.innerHTML = '<div class="error">📡 ' + escapeHtml(T("offline_detect", "You're offline - plant scanning needs an internet connection.")) + '</div>';
          }
          return;
        }
        if (inlineResult) {
          inlineResult.style.display = "block";
          inlineResult.innerHTML = '<div class="weatherbox">⏳ 🔍 ' + escapeHtml(T("analyzing", "Analyzing the plant photo...")) + '</div>';
        }
        const fd = new FormData();
        fd.append("image", blob, "plant-camera.jpg");
        var _sf = document.getElementById("scanField"); if (_sf && _sf.value) fd.append("field_id", _sf.value);
        if (window.RB_COORDS) { fd.append("lat", window.RB_COORDS.lat); fd.append("lon", window.RB_COORDS.lon); }
        try {
          const r = await fetch("/detect-camera", { method: "POST", body: fd, credentials: "same-origin" });
          const d = await r.json();
          if (!d.ok) throw new Error(d.error || "Plant scan failed");
          if (!inlineResult) return;
          if (d.unknown) {
            inlineResult.innerHTML = '<span class="section-label">🌱 ' + escapeHtml(T("identified_plant", "Plant Result")) + '</span><h3>⚠️ ' + escapeHtml(T("unrecognized", "Plant not reliably identified")) + '</h3><p>' + escapeHtml(d.message || T("plant_scan_help", "Please capture a clearer plant photo.")) + '</p><p class="small">' + escapeHtml(T("confidence", "Confidence")) + ': ' + Number(d.confidence || 0).toFixed(2) + '%</p>'; 
          } else {
            inlineResult.innerHTML = '<span class="section-label">🌱 ' + escapeHtml(T("identified_plant", "Plant Result")) + '</span>' +
              '<h3>🌱 ' + escapeHtml(d.plant || "Plant") + '</h3>' +
              '<p><b>🦠 ' + escapeHtml(T("disease", "Disease")) + ':</b> ' + escapeHtml(d.class || d.disease || T("not_reliably_identified", "Not reliably identified")) + '</p>' +
              '<span class="pill">' + Number(d.confidence || 0).toFixed(2) + '% ' + escapeHtml(T("confidence", "confidence")) + '</span>' +
              '<p class="small">🔎 ' + escapeHtml(T("analysis_source", "Source")) + ': ' + escapeHtml(d.source || "AI") + '</p>' +
              (d.explanation ? '<p>' + escapeHtml(d.explanation) + '</p>' : '') +
              (d.health_score != null ? '<p><b>' + escapeHtml(d.health_label || 'Crop Health Score') + ':</b> ' + escapeHtml(String(d.health_score)) + '/100</p>' : '') +
              (d.low_confidence && d.message ? '<p>' + escapeHtml(d.message) + '</p>' : '') +
              renderFirstSteps(d.guidance) +
              '<p><button type="button" class="btn" data-listen="#cameraDetectionResult">🔊 ' + escapeHtml(T("listen", "Listen")) + '</button> <a class="btn alt" href="/experts/tickets">🩺 ' + escapeHtml(T("ask_expert", "Not sure? Ask an expert")) + '</a></p>' +
              renderGuidance(d.guidance);
          }
          inlineResult.scrollIntoView({behavior:"smooth", block:"start"});
        } catch (err) {
          if (inlineResult) inlineResult.innerHTML = '<div class="error">⚠️ ' + escapeHtml(err.message || "Could not reach the detection server.") + '</div>';
        }
      }, "image/jpeg", 0.92);
    }

    openBtn.addEventListener("click", openCamera);
    document.querySelectorAll("[data-cam-close]").forEach(b => b.addEventListener("click", closeCamera));
    document.querySelectorAll("[data-cam-flip]").forEach(b => b.addEventListener("click", flipCamera));
    document.querySelectorAll("[data-cam-capture]").forEach(b => b.addEventListener("click", capture));
    window.addEventListener("pagehide", closeCamera);
  }

  function initSoilCamera() {
    const camBox = document.getElementById("soilCam");
    const openBtn = document.getElementById("openSoilCameraBtn");
    const uploadBtn = document.getElementById("soilUploadBtn");
    const input = document.getElementById("soilImageInput");
    const video = document.getElementById("soilVideo");
    const canvas = document.getElementById("soilCanvas");
    const modalResult = document.getElementById("soilCamResult");
    const result = document.getElementById("soilResult");
    if (!camBox || !openBtn || !input || !video || !canvas || !result) return;

    let stream = null;
    let facingMode = "environment";

    function closeCamera() {
      if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
      video.srcObject = null;
      camBox.style.display = "none";
    }

    async function startCamera() {
      if (!window.isSecureContext && !cameraIsLocalHost()) {
        cameraSecureError(modalResult, T("camera_secure", "Camera needs a secure HTTPS connection on mobile browsers."));
        return false;
      }
      try {
        if (stream) { stream.getTracks().forEach(t => t.stop()); }
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: facingMode }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false
        });
        video.srcObject = stream;
        await video.play().catch(() => {});
        modalResult.innerHTML = "";
        return true;
      } catch (e) {
        cameraSecureError(modalResult, "Camera could not be opened. Please allow camera access and use HTTPS/localhost.");
        return false;
      }
    }

    async function capture() {
      if (!stream || !video.videoWidth) return;
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(async blob => {
        if (!blob) return;
        closeCamera(); // Camera CLOSE immediately after soil photo capture.
        result.style.display = "block";
        result.className = "soil-result";
        result.innerHTML = "⏳ 🧠 AI is analyzing the captured soil photo...";
        const fd = new FormData();
        fd.append("image", blob, "soil-camera.jpg");
        try {
          const r = await fetch("/api/soil-analysis", {method:"POST", body:fd, credentials:"same-origin"});
          const d = await r.json();
          if (!d.ok) throw new Error(d.error || "Soil analysis failed");
          renderSoilResult(result, d.result || {});
          result.scrollIntoView({behavior:"smooth", block:"start"});
        } catch (err) {
          result.className = "soil-result error";
          result.innerHTML = "⚠️ " + escapeHtml(err.message || "Could not analyze the soil photo.");
        }
      }, "image/jpeg", 0.92);
    }

    openBtn.addEventListener("click", async () => { camBox.style.display = "flex"; await startCamera(); });
    document.querySelectorAll("[data-soil-cam-close]").forEach(b => b.addEventListener("click", closeCamera));
    document.querySelectorAll("[data-soil-cam-flip]").forEach(b => b.addEventListener("click", async () => {
      facingMode = facingMode === "environment" ? "user" : "environment";
      await startCamera();
    }));
    document.querySelectorAll("[data-soil-cam-capture]").forEach(b => b.addEventListener("click", capture));
    uploadBtn.addEventListener("click", () => input.click());
    window.addEventListener("pagehide", closeCamera);
  }

  // Shared soil result renderer. Hinglish: Upload aur Camera dono ka result
  // same Soil Result box ke niche dikhta hai.
  function renderSoilResult(result, x) {
    const problems = Array.isArray(x.visible_problems) ? x.visible_problems : [];
    const confidence = Number(x.confidence || 0).toFixed(0);
    result.style.display = "block";
    result.className = "soil-result";
    result.innerHTML =
      '<div class="soil-result-head"><h3>🪨 Soil & Irrigation Report</h3><span class="pill">' + confidence + '% confidence</span></div>' +
      '<div class="soil-metrics">' +
      '<div><b>💧 Moisture</b><span>' + escapeHtml(x.moisture_estimate || "Cannot tell") + '</span></div>' +
      '<div><b>🪨 Condition</b><span>' + escapeHtml(x.soil_condition || "Unknown") + '</span></div>' +
      '<div><b>🚰 Drainage</b><span>' + escapeHtml(x.drainage_risk || "Not clear") + '</span></div>' +
      '<div><b>🌾 Texture</b><span>' + escapeHtml(x.texture_visual || "Not clear") + '</span></div></div>' +
      '<p><b>🚿 Irrigation advice:</b> ' + escapeHtml(x.irrigation_advice || "Check the soil manually before watering.") + '</p>' +
      '<p><b>🌿 Organic matter (visual):</b> ' + escapeHtml(x.organic_matter_visual || "Cannot reliably determine from one photo.") + '</p>' +
      (problems.length ? '<p><b>⚠️ Visible issues:</b> ' + problems.map(escapeHtml).join(" • ") + '</p>' : '') +
      '<p><b>🌱 Crop suitability:</b> ' + escapeHtml(x.crop_suitability || "Confirm with a soil test and local agronomist.") + '</p>' +
      '<p class="small">' + escapeHtml(x.explanation || "") + '</p>' +
      '<div class="soil-warning">⚠️ Photo screening cannot measure exact pH, N/P/K, EC or laboratory moisture. Test soil before major fertilizer decisions.</div>';
  }

  // ---------------- Voice Assistant: speak -> answer by voice -> open apps ----------------
  const VA_LANGS = [["hi", "hi-IN", "हिन्दी (Hindi)"], ["en", "en-IN", "English"], ["mr", "mr-IN", "मराठी (Marathi)"], ["gu", "gu-IN", "ગુજરાતી (Gujarati)"],
    ["bn", "bn-IN", "বাংলা (Bengali)"], ["ta", "ta-IN", "தமிழ் (Tamil)"], ["te", "te-IN", "తెలుగు (Telugu)"], ["kn", "kn-IN", "ಕನ್ನಡ (Kannada)"],
    ["ml", "ml-IN", "മലയാളം (Malayalam)"], ["pa", "pa-IN", "ਪੰਜਾਬੀ (Punjabi)"], ["ur", "ur-PK", "اردو (Urdu)"], ["ar", "ar-SA", "العربية (Arabic)"],
    ["fr", "fr-FR", "Français"], ["es", "es-ES", "Español"], ["pt", "pt-BR", "Português"]];
  const VA_FALLBACK_VOICE = { mr: "hi-IN" };   // Marathi uses the Devanagari script: a Hindi voice can read it if no Marathi voice is installed
  const APP_LINKS = {
    chatgpt: { label: "ChatGPT", url: "https://chatgpt.com/", icon: "💬" }, gemini: { label: "Gemini", url: "https://gemini.google.com/app", icon: "✨" },
    google: { label: "Google", url: "https://www.google.com/", icon: "🔎" }, gmail: { label: "Gmail", url: "https://mail.google.com/", icon: "✉️" },
    youtube: { label: "YouTube", url: "https://www.youtube.com/", icon: "▶️" }, whatsapp: { label: "WhatsApp", url: "https://wa.me/", icon: "🟢" },
    maps: { label: "Maps", url: "https://www.google.com/maps", icon: "📍" }
  };
  const INTERNAL = { weather: ["#weather-section", "Weather"], scan: ["#plant-detection", "Scan Plant"], fields: ["/fields", "My Fields"], history: ["/history", "Scan History"],
    community: ["/community", "Farmer Community"], expert: ["/experts/tickets", "Expert Help"], crops: ["/crops", "My Crops"], iot: ["/iot", "IoT Sensors"],
    dashboard: ["/dashboard", "Dashboard"], notifications: ["/notifications", "Notifications"] };
  function plainText(md) { return String(md || "").replace(/[*#>`_]/g, "").replace(/\s+/g, " ").trim(); }
  function gtTarget() {   // language picked in the Google Translate widget: cookie "googtrans" = "/en/hi"
    const m = document.cookie.match(/(?:^|;\s*)googtrans=\/[^/]*\/([^;]+)/);
    return m ? decodeURIComponent(m[1]).split("-")[0] : "";
  }
  function vaDefaultLang() {
    try { const s = localStorage.getItem("rb_va_lang"); if (s && VA_LANGS.some((l) => l[0] === s)) return s; } catch (e) {}
    const g = gtTarget(); if (g && VA_LANGS.some((l) => l[0] === g)) return g;
    const n = (navigator.language || "en").split("-")[0];
    return VA_LANGS.some((l) => l[0] === n) ? n : "en";
  }
  window.RB_speechTag = function () { const code = gtTarget() || vaDefaultLang(); const row = VA_LANGS.find((l) => l[0] === code); return row ? row[1] : "en-IN"; };
  const synth = "speechSynthesis" in window ? window.speechSynthesis : null;
  let voicesCache = [];
  function loadVoices() { if (synth) voicesCache = synth.getVoices() || []; }
  if (synth) { loadVoices(); if (synth.addEventListener) synth.addEventListener("voiceschanged", loadVoices); }
  function pickVoice(tag) {
    const t = tag.toLowerCase(), base = t.split("-")[0];
    return voicesCache.find((v) => v.lang.toLowerCase().replace("_", "-") === t) || voicesCache.find((v) => v.lang.toLowerCase().startsWith(base)) || null;
  }
  function speechChunks(text) {   // long utterances are cut by some browsers: speak sentence-sized pieces in a queue
    const out = []; let cur = "";
    String(text).split(/(?<=[.!?।])\s+/).forEach((s) => { if ((cur + " " + s).length > 220 && cur) { out.push(cur); cur = s; } else cur = (cur ? cur + " " : "") + s; });
    if (cur) out.push(cur);
    return out.slice(0, 8);
  }
  window.rbSpeak = function (text, tag, onend) {
    if (!synth) { if (onend) onend(false); return false; }
    try {
      synth.cancel();
      const clean = plainText(text); if (!clean) { if (onend) onend(false); return false; }
      let lang = tag || "en-IN", voice = pickVoice(lang);
      if (!voice) { const fb = VA_FALLBACK_VOICE[lang.split("-")[0]]; if (fb) { lang = fb; voice = pickVoice(fb); } }
      const chunks = speechChunks(clean);
      chunks.forEach((c, i) => {
        const u = new SpeechSynthesisUtterance(c); u.lang = voice ? voice.lang : lang; if (voice) u.voice = voice; u.rate = 0.95;
        if (i === chunks.length - 1) { u.onend = () => { if (onend) onend(true); }; }
        u.onerror = () => { if (onend) onend(false); };
        synth.speak(u);
      });
      return true;
    } catch (e) { if (onend) onend(false); return false; }
  };
  window.rbStopSpeaking = function () { try { if (synth) synth.cancel(); } catch (e) {} };
  function unlockSpeech() { try { if (synth) { const u = new SpeechSynthesisUtterance(" "); u.volume = 0; synth.speak(u); } } catch (e) {} }

  // Live camera for the assistant (works on laptop webcams AND phones; falls back to the phone's own camera app / file picker)
  function openCameraModal(onFile, fallbackInput) {
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) { if (fallbackInput) fallbackInput.click(); return; }
    const ov = document.createElement("div"); ov.className = "va-cam"; ov.setAttribute("role", "dialog"); ov.setAttribute("aria-modal", "true");
    ov.innerHTML = '<div class="va-cam-box"><video playsinline autoplay muted></video><img class="va-cam-still" alt="" hidden><div class="va-cam-msg" role="status"></div>' +
      '<div class="va-cam-btns"><button type="button" class="btn" data-cap>📸 ' + escapeHtml(T("capture", "Capture")) + '</button><button type="button" class="btn alt" data-flip>🔄 ' + escapeHtml(T("flip_camera", "Flip")) + '</button>' +
      '<button type="button" class="btn alt" data-retake hidden>↩ ' + escapeHtml(T("retake", "Retake")) + '</button><button type="button" class="btn" data-use hidden>✅ ' + escapeHtml(T("use_photo", "Use photo")) + '</button>' +
      '<button type="button" class="btn alt" data-pick hidden>🖼️ ' + escapeHtml(T("attach_photo", "Choose photo")) + '</button><button type="button" class="btn red" data-close>✖ ' + escapeHtml(T("close", "Close")) + '</button></div></div>';
    document.body.appendChild(ov);
    const $ = (s) => ov.querySelector(s), video = $("video"), still = $(".va-cam-still"), msg = $(".va-cam-msg");
    let stream = null, facing = "environment", blob = null, url = null;
    function stop() { if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; } }
    function onKey(e) { if (e.key === "Escape") close(); }
    function close() { stop(); if (url) URL.revokeObjectURL(url); document.removeEventListener("keydown", onKey); ov.remove(); }
    document.addEventListener("keydown", onKey);
    function show(captured) { video.hidden = captured; still.hidden = !captured; $("[data-cap]").hidden = captured; $("[data-flip]").hidden = captured; $("[data-retake]").hidden = !captured; $("[data-use]").hidden = !captured; }
    async function start() {
      stop(); msg.textContent = ""; $("[data-pick]").hidden = true;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
        video.srcObject = stream; await video.play();
      } catch (e) {
        const denied = e && (e.name === "NotAllowedError" || e.name === "PermissionDeniedError");
        msg.textContent = denied ? T("camera_denied", "Camera permission is required. You can upload an image instead.") : T("camera_unavailable", "Camera is not available. You can upload an image instead.");
        if (fallbackInput) $("[data-pick]").hidden = false;
        $("[data-cap]").hidden = true; $("[data-flip]").hidden = true;
      }
    }
    ov.addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.hasAttribute("data-close")) close();
      else if (b.hasAttribute("data-flip")) { facing = facing === "environment" ? "user" : "environment"; start(); }
      else if (b.hasAttribute("data-cap")) {
        if (!video.videoWidth) return;
        const c = document.createElement("canvas"); c.width = video.videoWidth; c.height = video.videoHeight; c.getContext("2d").drawImage(video, 0, 0);
        c.toBlob((bl) => { if (!bl) return; blob = bl; if (url) URL.revokeObjectURL(url); url = URL.createObjectURL(bl); still.src = url; show(true); }, "image/jpeg", 0.9);
      } else if (b.hasAttribute("data-retake")) { blob = null; show(false); }
      else if (b.hasAttribute("data-use")) { if (blob) { const f = new File([blob], "assistant-photo.jpg", { type: "image/jpeg" }); close(); onFile(f); } }
      else if (b.hasAttribute("data-pick")) { close(); if (fallbackInput) fallbackInput.click(); }
    });
    start();
  }

  function initChat() {
    const form = document.getElementById("chatForm"), box = document.getElementById("chatBox"), input = document.getElementById("chatInput");
    if (!form || !box || !input) return;
    const mic = document.getElementById("vaMic"), statusEl = document.getElementById("vaStatus"), langSel = document.getElementById("vaLang"),
      appCard = document.getElementById("vaAppCard"), appsBox = document.getElementById("vaApps"), chips = document.getElementById("vaChips"),
      photoInput = document.getElementById("chatPhoto"), camInput = document.getElementById("chatCameraInput"), attach = document.getElementById("chatAttach"),
      camBtn = document.getElementById("chatCameraBtn"), photoInfo = document.getElementById("chatPhotoInfo"), photoClear = document.getElementById("chatPhotoClear"),
      speak = document.getElementById("chatSpeak"), hint = document.getElementById("voiceHint");
    let chatHistory = [], photoFile = null, listening = false, rec = null;
    const IDLE = T("voice_idle", "Tap the mic and speak");
    function setStatus(t) { if (statusEl) statusEl.textContent = t; }
    function scrollBox() { box.scrollTop = box.scrollHeight; }
    // language the farmer speaks (used for listening, speaking and the AI reply)
    if (langSel) {
      VA_LANGS.forEach((l) => { const o = document.createElement("option"); o.value = l[0]; o.textContent = l[2]; langSel.appendChild(o); });
      langSel.value = vaDefaultLang();
      langSel.addEventListener("change", () => { try { localStorage.setItem("rb_va_lang", langSel.value); } catch (e) {} });
    }
    const langCode = () => (langSel && langSel.value) || "en";
    const langTag = () => { const r = VA_LANGS.find((l) => l[0] === langCode()); return r ? r[1] : "en-IN"; };
    // voice replies are ON by default (a farmer should not have to find a switch)
    if (speak) { try { speak.checked = localStorage.getItem("rb_voice_replies") !== "0"; } catch (e) {} speak.addEventListener("change", () => { try { localStorage.setItem("rb_voice_replies", speak.checked ? "1" : "0"); } catch (e) {} if (!speak.checked) window.rbStopSpeaking(); }); }
    if (!synth && hint) hint.textContent = "🔇 " + T("voice_no_tts", "This browser cannot read answers aloud. Answers are shown as text.");
    function speakReply(text) {
      if (!speak || !speak.checked || !synth) return;
      setStatus("🔊 " + T("voice_speaking", "Speaking... tap the mic to stop"));
      window.rbSpeak(text, langTag(), () => setStatus(IDLE));
    }
    // quick-open app buttons: plain links, so they always work with one tap
    if (appsBox) Object.keys(APP_LINKS).forEach((k) => { const a = document.createElement("a"); a.className = "va-app"; a.href = APP_LINKS[k].url; a.target = "_blank"; a.rel = "noopener"; a.innerHTML = APP_LINKS[k].icon + " <span>" + escapeHtml(APP_LINKS[k].label) + "</span>"; appsBox.appendChild(a); });
    function chatGPTUrl(q) { return "https://chatgpt.com/?q=" + encodeURIComponent(q); }
    function addUser(text, hasPhoto) { box.insertAdjacentHTML("beforeend", '<div class="va-msg va-me"><b>👩‍🌾</b> ' + escapeHtml(text) + (hasPhoto ? " 📷" : "") + "</div>"); scrollBox(); }
    function addBot(html, plain, question, extraClass) {
      const d = document.createElement("div"); d.className = "va-msg va-bot " + (extraClass || "");
      d.innerHTML = '<div class="va-ans">' + html + '</div><div class="va-actions"><button type="button" class="btn alt" data-again>🔊 ' + escapeHtml(T("listen_again", "Listen again")) + '</button>' +
        (question ? '<a class="btn alt" data-gpt target="_blank" rel="noopener" href="' + chatGPTUrl(question) + '">💬 ' + escapeHtml(T("ask_chatgpt", "Ask ChatGPT")) + '</a>' : '') + '</div>';
      d.querySelector("[data-again]").addEventListener("click", () => { unlockSpeech(); window.rbSpeak(plain, langTag(), () => setStatus(IDLE)); });
      const g = d.querySelector("[data-gpt]"); if (g) g.addEventListener("click", () => { try { navigator.clipboard && navigator.clipboard.writeText(question); } catch (e) {} });
      box.appendChild(d); scrollBox();
    }
    function showAppCard(key) {
      const a = APP_LINKS[key]; if (!a || !appCard) return;
      appCard.hidden = false;
      appCard.innerHTML = '<div class="va-appcard-in"><span class="ic">' + a.icon + '</span><div><b>' + escapeHtml(a.label) + '</b><div class="small">' + escapeHtml(T("app_tap", "If it did not open, tap the button.")) + '</div></div>' +
        '<a class="btn" target="_blank" rel="noopener" href="' + a.url + '">' + escapeHtml(T("open", "Open")) + " " + escapeHtml(a.label) + '</a></div>';
    }
    function openApp(key, query) {
      const a = APP_LINKS[key]; if (!a) return;
      const url = key === "chatgpt" && query ? chatGPTUrl(query) : a.url;
      const msg = T("opening", "Opening") + " " + a.label + "...";
      addBot(escapeHtml(msg), msg, null); showAppCard(key);
      try { const w = window.open(url, "_blank"); if (w) w.opener = null; } catch (e) {}
      speakReply(msg);
    }
    function runInternal(key) {
      const t = INTERNAL[key]; if (!t) return;
      const msg = T("opening", "Opening") + " " + t[1] + "...";
      addBot(escapeHtml(msg), msg, null); speakReply(msg);
      const target = t[0];
      if (target.charAt(0) === "#") { const el = document.querySelector(target); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); else location.href = "/" + target; }
      else setTimeout(() => { location.href = target; }, 700);
    }
    function setPhoto(f) { photoFile = f || null; if (photoInfo) photoInfo.textContent = photoFile ? "📷 " + T("photo_attached", "Photo attached") : ""; if (photoClear) photoClear.hidden = !photoFile; }
    if (attach && photoInput) { attach.addEventListener("click", () => photoInput.click()); photoInput.addEventListener("change", () => setPhoto(photoInput.files && photoInput.files[0])); }
    if (camInput) camInput.addEventListener("change", () => setPhoto(camInput.files && camInput.files[0]));
    if (camBtn) camBtn.addEventListener("click", () => openCameraModal(setPhoto, camInput));
    if (photoClear) photoClear.addEventListener("click", () => { setPhoto(null); if (photoInput) photoInput.value = ""; if (camInput) camInput.value = ""; });

    async function handleUtterance(text) {
      const msg = String(text || "").trim(), photo = photoFile;
      if (!msg && !photo) return;
      unlockSpeech(); window.rbStopSpeaking(); if (appCard) appCard.hidden = true;
      addUser(msg || T("photo_attached", "Photo attached"), !!photo); input.value = "";
      if (!photo && window.RBVoiceCommands) {
        const cmd = window.RBVoiceCommands.parse(msg);
        if (cmd.type === "external") { setStatus(IDLE); return openApp(cmd.key); }
        if (cmd.type === "chatgpt_ask") { setStatus(IDLE); return openApp("chatgpt", cmd.query); }
        if (cmd.type === "internal") { setStatus(IDLE); return runInternal(cmd.key); }
      }
      const q = msg || T("photo_question", "What is wrong with this plant and what should I do?");
      if (!navigator.onLine) { const m = T("chat_offline_msg", "You are offline right now, so the assistant cannot answer."); addBot(escapeHtml(m), m, q, "va-err"); setStatus(IDLE); return speakReply(m); }
      setStatus("⏳ " + T("voice_thinking", "Thinking..."));
      try {
        let r;
        if (photo) {
          const fd = new FormData(); fd.append("message", msg); fd.append("image", photo, photo.name || "photo.jpg"); fd.append("history", JSON.stringify(chatHistory.slice(-8))); fd.append("language", langCode());
          r = await fetch("/api/chat", { method: "POST", body: fd });
          setPhoto(null); if (photoInput) photoInput.value = ""; if (camInput) camInput.value = "";
        } else {
          r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: msg, history: chatHistory, language: langCode() }) });
        }
        const d = await r.json();
        if (d.ok) {
          addBot(renderChatMarkdown(d.answer), d.answer, q);
          chatHistory.push({ role: "user", content: msg }, { role: "assistant", content: d.answer });
          setStatus(IDLE); speakReply(d.answer);
        } else {
          const m = d.error || T("chat_server_error", "Could not reach the server.");
          addBot(escapeHtml(m), m, q, "va-err"); setStatus(IDLE); speakReply(m);
        }
      } catch (err) {
        const m = T("chat_server_error", "Could not reach the server.");
        addBot(escapeHtml(m), m, q, "va-err"); setStatus(IDLE); speakReply(m);
      }
    }
    form.addEventListener("submit", (e) => { e.preventDefault(); handleUtterance(input.value); });
    if (chips) chips.addEventListener("click", (e) => { const b = e.target.closest("button[data-q]"); if (b) handleUtterance(b.getAttribute("data-q")); });

    // big microphone
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    setStatus(IDLE);
    if (mic && !SR) { mic.classList.add("is-off"); setStatus("⌨️ " + T("voice_unsupported", "Voice input is not supported on this browser. Please type your question.")); }
    if (mic) mic.addEventListener("click", () => {
      if (synth && synth.speaking) { window.rbStopSpeaking(); setStatus(IDLE); return; }
      if (listening && rec) { rec.stop(); return; }
      if (!SR) { input.focus(); return; }
      unlockSpeech(); window.rbStopSpeaking();
      rec = new SR(); rec.lang = langTag(); rec.interimResults = true; rec.maxAlternatives = 1; rec.continuous = false;
      let finalText = "", errored = false;
      rec.onstart = () => { listening = true; mic.classList.add("is-listening"); setStatus("🎙️ " + T("voice_listening", "Listening... speak now")); };
      rec.onresult = (ev) => {
        let interim = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) { const r = ev.results[i]; if (r.isFinal) finalText += r[0].transcript; else interim += r[0].transcript; }
        input.value = (finalText + " " + interim).trim();
      };
      rec.onerror = (ev) => {
        errored = true;
        const denied = ev.error === "not-allowed" || ev.error === "service-not-allowed";
        setStatus(denied ? "🚫 " + T("voice_denied", "Microphone permission is required for voice input. You can type your question instead.")
          : ev.error === "no-speech" ? "🤷 " + T("voice_nospeech", "I did not hear anything. Tap the mic and try again.")
          : ev.error === "network" ? "📡 " + T("voice_network", "Voice needs an internet connection.")
          : "⚠️ " + T("voice_error", "Could not understand the voice. Please try again or type your question."));
      };
      rec.onend = () => {
        listening = false; mic.classList.remove("is-listening");
        const t = (finalText || input.value).trim();
        if (t && !errored) handleUtterance(t); else if (!errored) setStatus("🤷 " + T("voice_nospeech", "I did not hear anything. Tap the mic and try again."));
      };
      try { rec.start(); } catch (e) { setStatus("⚠️ " + T("voice_error", "Could not understand the voice. Please try again or type your question.")); }
    });
  }

  // ---------------- AI Soil & Irrigation Photo ----------------
  function initSoilAnalysis() {
    const form = document.getElementById("soilAnalysisForm");
    const input = document.getElementById("soilImageInput");
    const preview = document.getElementById("soilPreview");
    const result = document.getElementById("soilResult");
    const btn = document.getElementById("soilAnalyzeBtn");
    if (!form || !input || !result) return;

    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (!file) return;
      if (preview) { preview.src = URL.createObjectURL(file); preview.style.display = "block"; }
      result.style.display = "none";
      result.innerHTML = "";
    });

    form.addEventListener("submit", async e => {
      e.preventDefault();
      const file = input.files && input.files[0];
      if (!file) return;
      if (!navigator.onLine) {
        result.style.display = "block";
        result.className = "soil-result error";
        result.textContent = "📡 " + T("offline", "You're offline") + ". Soil AI needs an internet connection.";
        return;
      }
      btn.disabled = true;
      btn.textContent = "🧠 Analyzing...";
      result.style.display = "block";
      result.className = "soil-result";
      result.innerHTML = "⏳ AI is checking the visible soil condition...";
      try {
        const fd = new FormData();
        fd.append("image", file);
        const r = await fetch("/api/soil-analysis", {method:"POST", body:fd, credentials:"same-origin"});
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || "Analysis failed");
        renderSoilResult(result, d.result || {});
        result.scrollIntoView({behavior:"smooth", block:"start"});
      } catch (err) {
        result.className = "soil-result error";
        result.innerHTML = "⚠️ " + escapeHtml(err.message || "Could not analyze the soil photo.");
      } finally {
        btn.disabled = false;
        btn.textContent = "🧠 Analyze Soil & Irrigation";
      }
    });
  }

  // ---------------- Weather ----------------
  function initWeather() {
    const result = document.getElementById("weatherResult");
    const locBtn = document.getElementById("locateBtn");
    const cityBtn = document.getElementById("cityWeatherBtn");
    if (!result) return;

    async function weather(q) {
      if (!navigator.onLine) {
        result.style.display = "block";
        result.innerText = "📡 " + T("weather_offline", "You're offline - weather needs an internet connection.");
        return;
      }
      result.style.display = "block";
      result.innerText = T("weather_loading", "Loading...");
      try {
        const r = await fetch("/api/weather?" + q);
        const d = await r.json();
        if (!d.ok) { result.innerText = "⚠️ " + d.error; return; }
        const x = d.current;
        result.innerHTML = "<b>📍 " + escapeHtml(d.place) + "</b><br>🌡️ " + x.temperature_2m + " °C &nbsp; 💧 " + x.relative_humidity_2m +
          "% " + T("humidity", "humidity") + "<br>🌧️ " + T("weather_rain", "Rain") + ": " + x.rain + " mm &nbsp; 💨 " + T("weather_wind", "Wind") + ": " + x.wind_speed_10m + " km/h";
        let html = result.innerHTML;
        if (d.forecast && d.forecast.length) {
          html += "<br><b>" + escapeHtml(T("weather_forecast", "Forecast")) + ":</b><br>" + d.forecast.map(function (f) {
            return escapeHtml(f.date) + ": " + f.tmin + "–" + f.tmax + " °C, 🌧️ " + (f.rain == null ? 0 : f.rain) + " mm" + (f.rain_prob != null ? " (" + f.rain_prob + "%)" : ""); }).join("<br>");
        }
        if (d.risk && d.risk.length) {
          html += "<br><b>⚠️ " + escapeHtml(T("estimated_risk", "Estimated risk")) + " • " + escapeHtml(T("advisory", "Advisory")) + ":</b><br>" + d.risk.map(function (r) {
            return (r.type === "disease_risk" ? "🦠 " + escapeHtml(T("risk_" + r.level, r.level)) + " — " : "ℹ️ ") + escapeHtml(r.text); }).join("<br>") +
            '<br><span class="small">' + escapeHtml(d.risk_note || T("risk_disclaimer", "")) + "</span>";
        }
        result.innerHTML = html;
        const m = /^lat=([-\d.]+)&lon=([-\d.]+)$/.exec(q); if (m) window.RB_COORDS = { lat: m[1], lon: m[2] };   // memory only; sent with the next scan so its weather can be saved
      } catch (err) {
        result.innerText = "⚠️ " + T("weather_server_error", "Could not reach the server.");
      }
    }
    if (locBtn) locBtn.addEventListener("click", () => {
      if (!navigator.geolocation) { alert(T("geo_unsupported", "Geolocation is not supported.")); return; }
      navigator.geolocation.getCurrentPosition(
        (p) => weather("lat=" + p.coords.latitude + "&lon=" + p.coords.longitude),
        () => alert(T("location_denied", "Location permission was not granted."))
      );
    });
    if (cityBtn) cityBtn.addEventListener("click", () => {
      const c = document.getElementById("city").value.trim();
      if (c) weather("city=" + encodeURIComponent(c));
    });
  }
  // ---------------- Plant Hospital: low-network/offline queue ----------------
  // Ticket forms contain only text + references to already-uploaded scan images,
  // so a ticket can safely wait on this device until the server is reachable.
  function initPlantHospitalOfflineQueue() {
    const form = document.getElementById("ticketForm");
    if (!form) return;

    const KEY = "smart_crop_ai_hospital_queue_v1";
    const notice = document.getElementById("ticketOfflineNotice");
    const pending = document.getElementById("pendingTickets");

    function readQueue() {
      try {
        const q = JSON.parse(localStorage.getItem(KEY) || "[]");
        return Array.isArray(q) ? q : [];
      } catch (_) { return []; }
    }
    function writeQueue(q) {
      try { localStorage.setItem(KEY, JSON.stringify(q)); } catch (_) {}
    }
    function showQueue() {
      const q = readQueue();
      if (!pending) return;
      if (!q.length) {
        pending.style.display = "none";
        pending.textContent = "";
        return;
      }
      pending.style.display = "block";
      pending.textContent = "📡 " + T("offline_queue", "Waiting to sync") + ": " + q.length;
    }
    function showNotice(text) {
      if (!notice) return;
      notice.textContent = text;
      notice.style.display = "block";
    }

    // Give every ticket an idempotency key so a slow request retried later
    // cannot create the same ticket twice.
    let ref = form.querySelector('input[name="client_ref"]');
    if (!ref) {
      ref = document.createElement("input");
      ref.type = "hidden";
      ref.name = "client_ref";
      ref.value = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : (Date.now() + "-" + Math.random());
      form.appendChild(ref);
    }

    async function syncQueue() {
      if (!navigator.onLine) return;
      const q = readQueue();
      if (!q.length) return;
      showNotice("🔄 " + T("ticket_syncing", "Sending saved Plant Hospital tickets..."));
      const remaining = [];
      for (const item of q) {
        try {
          const body = new URLSearchParams(item);
          const response = await fetch("/experts/tickets", {
            method: "POST",
            body,
            credentials: "same-origin",
            headers: {"X-SmartCrop-Offline-Sync": "1"}
          });
          // A redirect to /login means the session expired; keep the ticket
          // queued instead of silently deleting it.
          if (!response.ok || /\/login(?:\?|$)/.test(response.url)) {
            remaining.push(item);
          }
        } catch (_) {
          remaining.push(item);
        }
      }
      writeQueue(remaining);
      showQueue();
      if (!remaining.length) {
        showNotice("✅ " + T("ticket_synced", "Saved Plant Hospital ticket sent."));
        setTimeout(() => { if (notice) notice.style.display = "none"; }, 3500);
        // Refresh so the farmer can immediately see the server-side ticket.
        setTimeout(() => location.reload(), 500);
      }
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(form).entries());

      function queueTicket() {
        const q = readQueue();
        // Do not add the same client_ref twice.
        if (!q.some(x => x.client_ref === data.client_ref)) q.push(data);
        writeQueue(q);
        ref.value = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : (Date.now() + "-" + Math.random());
        showQueue();
        showNotice("📡 " + T("ticket_offline_queued", "No/weak network. Your Plant Hospital ticket was saved on this device and will be sent automatically when the connection returns."));
      }

      if (!navigator.onLine) {
        queueTicket();
        return;
      }

      // Use fetch for low/unstable networks. If it really cannot reach the
      // server, queue the ticket. The client_ref makes retries idempotent.
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 25000);
      try {
        showNotice("⏳ " + T("ticket_syncing", "Sending saved Plant Hospital tickets..."));
        const response = await fetch("/experts/tickets", {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          signal: controller.signal
        });
        clearTimeout(timer);
        if (/\/login(?:\?|$)/.test(response.url)) {
          window.location.href = response.url;
          return;
        }
        if (!response.ok) throw new Error("ticket-submit-failed");
        window.location.href = response.url;
      } catch (_) {
        clearTimeout(timer);
        queueTicket();
      }
    });

    window.addEventListener("online", syncQueue);
    showQueue();
    if (navigator.onLine) setTimeout(syncQueue, 700);
  }

})();

// Notification bell: unread count (private, never cached by the service worker)
(function () {
  var b = document.getElementById("notifBadge");
  if (!b || !window.fetch) return;
  fetch("/api/notifications/unread", { credentials: "same-origin", headers: { Accept: "application/json" } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { if (d && d.count > 0) { b.textContent = d.count > 99 ? "99+" : String(d.count); b.style.display = "inline-block"; } })
    .catch(function () {});
})();

// Upload form: attach coordinates (only if the user already used "Use my location") so the scan can store its weather summary.
document.addEventListener("submit", function (e) {
  var f = e.target;
  if (!window.RB_COORDS || location.pathname !== "/" || !f || !f.querySelector || !f.querySelector('input[type="file"][name="image"]')) return;
  ["lat", "lon"].forEach(function (k) { if (!f.querySelector('input[name="' + k + '"]')) { var i = document.createElement("input"); i.type = "hidden"; i.name = k; i.value = window.RB_COORDS[k]; f.appendChild(i); } });
}, true);

// ---- Navbar (hamburger + Features dropdown), bottom bar, welcome card, "Listen" buttons
(function () {
  const toggle = document.getElementById("rbnavToggle"), menu = document.getElementById("rbnavMenu"), more = document.getElementById("rbnavMore"), moreBtn = document.getElementById("rbnavMoreBtn");
  function setMore(open) { if (more) { more.classList.toggle("open", open); if (moreBtn) moreBtn.setAttribute("aria-expanded", open ? "true" : "false"); } }
  function setMenu(open) { if (menu) { menu.classList.toggle("open", open); if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false"); } }
  function openAllMenus() { window.scrollTo({ top: 0, behavior: "smooth" }); setMenu(true); setMore(true); }
  if (toggle) toggle.addEventListener("click", () => setMenu(!menu.classList.contains("open")));
  if (moreBtn) moreBtn.addEventListener("click", (e) => { e.stopPropagation(); setMore(!more.classList.contains("open")); });
  document.addEventListener("click", (e) => {
    if (more && !more.contains(e.target)) setMore(false);
    if (menu && menu.classList.contains("open") && e.target.closest(".rbnav-menu a")) setMenu(false);
    if (e.target.closest("[data-open-features]")) openAllMenus();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { setMore(false); } });
  const bm = document.getElementById("rbbottomMore");
  if (bm) { document.body.classList.add("has-bottombar"); bm.addEventListener("click", openAllMenus); }

  // first-visit welcome card (3 easy steps + language buttons that drive Google Translate)
  const wc = document.getElementById("welcomeCard");
  function welcomeDone() { try { localStorage.setItem("rb_welcome_done", "1"); } catch (e) {} }
  function setGoogleLang(code) {
    const past = "Thu, 01 Jan 1970 00:00:00 GMT", host = location.hostname, v = "/en/" + code;
    if (code === "en") { document.cookie = "googtrans=; expires=" + past + "; path=/"; document.cookie = "googtrans=; expires=" + past + "; path=/; domain=" + host; }
    else { document.cookie = "googtrans=" + v + "; path=/"; document.cookie = "googtrans=" + v + "; path=/; domain=" + host; }
    try { localStorage.setItem("rb_va_lang", code); } catch (e) {}
  }
  if (wc) {
    let seen = false; try { seen = localStorage.getItem("rb_welcome_done") === "1"; } catch (e) {}
    if (!seen) wc.hidden = false;
    wc.addEventListener("click", (e) => {
      const g = e.target.closest("[data-gt]");
      if (g) { setGoogleLang(g.getAttribute("data-gt")); welcomeDone(); location.reload(); return; }
      if (e.target.closest("[data-welcome-close]")) { welcomeDone(); wc.hidden = true; }
    });
  }
  // "Listen": read a result aloud (reads the text as the page currently shows it, i.e. already translated by Google Translate)
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-listen]"); if (!b || !window.rbSpeak) return;
    if (b.dataset.speaking === "1") { window.rbStopSpeaking(); b.dataset.speaking = "0"; return; }
    const parts = [];
    b.getAttribute("data-listen").split(",").forEach((sel) => {
      const el = document.querySelector(sel.trim()); if (!el) return;
      const c = el.cloneNode(true); c.querySelectorAll("button,a.btn,script,style,.small,.section-label,.preview").forEach((n) => n.remove());
      parts.push(c.textContent || "");
    });
    const text = parts.join(". ").replace(/\s+/g, " ").trim(); if (!text) return;
    b.dataset.speaking = "1";
    window.rbSpeak(text, window.RB_speechTag ? window.RB_speechTag() : "en-IN", () => { b.dataset.speaking = "0"; });
  });
})();
