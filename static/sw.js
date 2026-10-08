/* ============================================================
   RBAgriScan PWA SERVICE WORKER
   Hinglish: Offline/installable app behavior yahan hai.
   Static files cache hote hain; live AI/API requests ko stale cache
   se replace nahi kiya jata.
   ============================================================ */
/* Smart Crop AI - Service Worker
 * Strategy:
 *  - App shell (CSS/JS/icons/manifest): cache-first, refreshed in the background.
 *  - Pages (navigations): network-first with a short timeout, falling back to the
 *    cached copy of that page, and finally to /offline if nothing is cached yet.
 *  - Scan photos (/media/*) and every signed-in page are private: never cached.
 *  - Everything else (API calls, form POSTs, external APIs): network only. We never
 *    want to serve a stale AI/weather answer as if it were live.
 */
const VERSION = "rbagriscan-v8-voice-nav";
const SHELL_CACHE = VERSION + "-shell";
const PAGE_CACHE = VERSION + "-pages";
// Private user data is NEVER cached: only these public, login-free pages may be stored for offline use.
const PUBLIC_PAGES = new Set(["/offline", "/about", "/supported-plants", "/contact"]);
// 4s was too tight for rural/mobile networks (and for Render's free-tier cold
// start after the server has been idle) - a perfectly fine connection would
// simply not finish in time and the app would show the offline page even
// though it wasn't actually offline. Give it more room.
const NETWORK_TIMEOUT_MS = 10000;

const SHELL_ASSETS = [
  "/offline",
  "/static/css/style.css",
  "/static/js/app.js",
  "/static/js/voice_commands.js",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-512-maskable.png",
  "/static/icons/apple-touch-icon.png",
  "/static/icons/favicon-32.png",
  "/static/icons/favicon-16.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((n) => (n.startsWith("smart-crop-ai-") || n.startsWith("rbagriscan-")) && ![SHELL_CACHE, PAGE_CACHE].includes(n))
          .map((n) => caches.delete(n))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data === "SKIP_WAITING") self.skipWaiting();
});

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("network-timeout")), ms);
    promise.then((v) => { clearTimeout(t); resolve(v); }, (e) => { clearTimeout(t); reject(e); });
  });
}

async function networkFirstPage(request) {
  const cache = await caches.open(PAGE_CACHE);
  try {
    const fresh = await withTimeout(fetch(request, { cache: "no-store" }), NETWORK_TIMEOUT_MS);
    const path = new URL(request.url).pathname;
    // Only cache public pages, never redirects (login walls) and never signed-in pages.
    if (fresh && fresh.ok && !fresh.redirected && PUBLIC_PAGES.has(path)) cache.put(request, fresh.clone());
    return fresh;
  } catch (err) {
    // CRITICAL FIX: the installed PWA's start_url is "/?source=pwa", but a
    // page normally gets cached under plain "/" the first time it's opened
    // in a regular browser tab. { ignoreSearch: true } lets those two match
    // each other instead of missing the cache and showing /offline even
    // though a perfectly good cached copy of the page exists.
    const cached = PUBLIC_PAGES.has(new URL(request.url).pathname) ? await cache.match(request, { ignoreSearch: true }) : null;
    if (cached) return cached;
    const shell = await caches.open(SHELL_CACHE);
    const offline = await shell.match("/offline");
    return offline || Response.error();
  }
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) {
    // Refresh in the background so the next visit stays current.
    fetch(request).then((res) => { if (res && res.ok) cache.put(request, res.clone()); }).catch(() => {});
    return cached;
  }
  try {
    const fresh = await fetch(request);
    if (fresh && fresh.ok) cache.put(request, fresh.clone());
    return fresh;
  } catch (err) {
    return cached || Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  // Language switch / logout: drop cached pages so no stale-language or previous-user page is ever served.
  if (url.pathname.startsWith("/set-language") || url.pathname.startsWith("/logout")) {
    event.waitUntil(caches.delete(PAGE_CACHE));
    return;
  }
  // Private scan photos are served by /media after an ownership check: never cached by the service worker.
  if (url.pathname.startsWith("/media/") || url.pathname.startsWith("/static/uploads/")) return;

  // Never intercept API/data endpoints or the language switcher - always live.
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/detect-camera") ||
    url.pathname.startsWith("/set-language") ||
    url.pathname.startsWith("/admin")
  ) {
    return;
  }

  if (req.mode === "navigate") {
    event.respondWith(networkFirstPage(req));
    return;
  }

  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.json") {
    event.respondWith(cacheFirst(req, SHELL_CACHE));
    return;
  }
});
