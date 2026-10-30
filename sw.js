/* 單字王 2000 離線快取（Service Worker）
   v6.2.3 by Claude：本機檔案改為「網路優先」，上線後學員重新整理即可拿到新版網頁與題庫，
   一般更新不必再手動改版本號；只有要強制清除舊快取時才把版本號 +1 */
const CACHE = "wb2000-v4"; // v6.2.3 by Claude：v3→v4，清除 v3 以前依錯誤清單建立的快取

// v6.2.3 by Claude：此 sw.js 與網站 exams/eng 資料夾內的 2000words.html、vocab.html 共用，
// 清單須保留這些頁面；原清單漏了主程式 vocab-quiz-30days.html 與 manifest.json，且含不存在的 index.html，已修正
const APP_PAGE = "./vocab-quiz-30days.html";
const CORE = [
  APP_PAGE,
  "./2000words.html",
  "./vocab.html",
  "./vocab2000.js",
  "./cap_exam_bank.js", // v6.2.8 by Claude：官方真題題庫
  "./cap_listening_bank.js", // v6.2.9 by Claude：官方聽力題庫 (音檔本身不預存，見下方 fetch)
  "./manifest.json",
  "./manifest.webmanifest",
  "./icon.svg",
  "./icon-192.png",
  "./icon-512.png",
  "./icon-512-maskable.png",
  "./apple-touch-icon.png",
  "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"
];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => Promise.allSettled(CORE.map(u => c.add(u))))   // 某個檔案不存在也不影響安裝
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      // v6.2.5 by Claude：只刪除本 App 舊版快取 (wb2000- 開頭)。原本會刪除整個 lyu0608.github.io 網域下
      // 其他網頁的快取
      .then(keys => Promise.all(keys.filter(k => k.startsWith("wb2000-") && k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// v6.2.3 by Claude：只快取成功的回應，避免把 404 / 錯誤頁存進快取
function putInCache(req, res) {
  if (res && res.ok) {
    const copy = res.clone();
    caches.open(CACHE).then(c => c.put(req, copy));
  }
  return res;
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;
  const isSupabaseLib = url.href.startsWith("https://cdn.jsdelivr.net/npm/@supabase/");
  if (!sameOrigin && !isSupabaseLib) return;   // Supabase 資料庫、Google 圖示等一律走網路
  // v6.2.9 by Claude：聽力官方錄音 (listen/*.mp3) 直接走網路不快取：瀏覽器以分段 (Range) 方式讀取音檔，
  // 分段回應無法存入快取，且 69 個音檔共約 22MB，不適合佔用手機空間
  if (url.pathname.includes("/listen/")) return;

  // 網頁本身：先抓網路上的新版，沒網路才用快取
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req)
        .then(res => putInCache(req, res))
        .catch(() => caches.match(req, { ignoreSearch: true })
          .then(hit => hit || caches.match(APP_PAGE)))   // v6.2.3 by Claude：找不到該頁快取時，以單字特訓主程式作為離線備援頁
    );
    return;
  }

  // v6.2.3 by Claude：本機檔案 (題庫 vocab2000.js、manifest、圖示) 改為網路優先、離線才用快取。
  // 原本一律「有快取就用快取」，題庫更新後學員手機永遠讀到舊題庫
  if (sameOrigin) {
    e.respondWith(
      fetch(req)
        .then(res => putInCache(req, res))
        .catch(() => caches.match(req, { ignoreSearch: true }))
    );
    return;
  }

  // Supabase 程式庫 (CDN)：有快取就用快取
  e.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(res => putInCache(req, res)))
  );
});
