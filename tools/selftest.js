// v6.2.5 by Claude：單字特訓 App 自動化自我測試 (無畫面 Edge，訪客模式 + 模擬 Supabase，不會連到真正的資料庫)
// 執行方式：
//   1. 安裝一次：在 tools 資料夾執行  npm install puppeteer-core@23.11.1
//   2. 在專案資料夾啟動本機伺服器：  python -m http.server 8765 --bind 127.0.0.1
//   3. 另開視窗執行：                node tools/selftest.js
// 全部通過時最後一行顯示 N/N passed，結束代碼 0
// Self-test suite for vocab-quiz-30days.html (headless Edge, guest mode + stubbed Supabase).
const puppeteer = require("puppeteer-core");
const path = require("path"), fs = require("fs");
const URL_ = "http://127.0.0.1:8765/vocab-quiz-30days.html";
const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok: !!ok, detail }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  -- " + detail : ""}`); };

(async () => {
  // v6.2.6 by Claude：測試用瀏覽器設定檔改放系統暫存資料夾，原本放在 tools/ 底下會被 git 一起提交
  const profile = path.join(require("os").tmpdir(), "vocab-selftest-profile");
  fs.rmSync(profile, { recursive: true, force: true });
  const browser = await puppeteer.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true, userDataDir: profile,
  });
  const page = await browser.newPage();
  const pageErrors = [], dialogs = [];
  page.on("pageerror", e => pageErrors.push(String(e)));
  page.on("dialog", async d => { dialogs.push(d.message()); await d.accept(); });
  // keep tests offline from the real Supabase backend: block its REST/auth endpoints
  await page.setRequestInterception(true);
  page.on("request", r => r.url().includes(".supabase.co") ? r.abort() : r.continue());

  await page.goto(URL_, { waitUntil: "networkidle0" });

  // T1 load
  const t1 = await page.evaluate(() => ({ n: FULL_VOCAB.length, status: document.getElementById("sourceStatus").innerText }));
  check("T1 題庫載入 2486 字", t1.n === 2486 && t1.status.includes("2486"), t1.status);

  // T2 day plans stable + full coverage
  await page.evaluate(() => loginAsGuest());
  const planA = await page.evaluate(() => JSON.stringify(Object.values(dayPlans).map(d => d.map(x => x.en))));
  await page.reload({ waitUntil: "networkidle0" });
  await page.evaluate(() => loginAsGuest());
  const planB = await page.evaluate(() => JSON.stringify(Object.values(dayPlans).map(d => d.map(x => x.en))));
  check("T2a 每日題目重新載入後相同", planA === planB);
  const cov = await page.evaluate(() => { const all = Object.values(dayPlans).flat().map(x => x.en); return { total: all.length, uniq: new Set(all).size, days: Object.keys(dayPlans).length }; });
  check("T2b 30 天涵蓋全部單字且不重複", cov.total === 2486 && cov.uniq === 2486 && cov.days === 30, JSON.stringify(cov));

  // T3/T4 every quiz type on many words incl. tricky ones
  const t4 = await page.evaluate(() => {
    const tricky = ["ma'am", "Mr.", "both … and", "not only … but also", "o'clock", "New Year's Day", "T-shirt", "e-mail", "a few", "Mid-Autumn"];
    const pool = FULL_VOCAB.filter(w => tricky.includes(w.en)).concat(shuffle(FULL_VOCAB).slice(0, 250));
    const problems = [];
    for (const t of ["wordDef", "hard", "cloze", "exam", "listen"]) {
      setQuizType(t);
      isRandomMode = true; currentTestQueue = pool.slice(); currentIndex = 0; correctCount = 0; wrongCount = 0;
      for (let i = 0; i < pool.length; i++) {
        renderQuestion();
        const btns = [...document.querySelectorAll(".option-btn")];
        const disp = document.getElementById("wordDisplay").innerText;
        const targets = btns.map(b => b.dataset.target);
        const need = t === "listen" ? 3 : 4;
        if (btns.length !== need) problems.push(`${t}:${pool[i].en}: ${btns.length} options`);
        if (new Set(targets).size !== targets.length) problems.push(`${t}:${pool[i].en}: duplicate options ${targets.join("|")}`);
        if (/undefined|null/.test(disp)) problems.push(`${t}:${pool[i].en}: display "${disp.slice(0, 60)}"`);
        const correct = btns.find(b => b.dataset.target === (t === "wordDef" ? pool[i].zh : t === "listen" ? null : pool[i].en));
        if (t !== "listen" && !correct) problems.push(`${t}:${pool[i].en}: correct answer missing`);
        (correct || btns[0]).click();
        const exp = document.getElementById("explanationBox").innerText;
        if (/undefined/.test(exp)) problems.push(`${t}:${pool[i].en}: explanation has undefined`);
        if (t !== "listen" && correct && !correct.classList.contains("correct")) problems.push(`${t}:${pool[i].en}: correct option not marked`);
        currentIndex++;
        if (problems.length > 25) break;
      }
    }
    return { checked: pool.length, problems };
  });
  check(`T3/T4 五種題型 × ${t4.checked} 字 (含特殊字) 出題與作答`, t4.problems.length === 0, t4.problems.slice(0, 12).join(" ; "));

  // T4b option sound button works for apostrophe words (inline onclick escaping)
  const t4b = await page.evaluate(() => {
    setQuizType("hard");
    const w = FULL_VOCAB.find(x => x.en === "ma'am") || FULL_VOCAB.find(x => x.en.includes("'"));
    isRandomMode = true; currentTestQueue = [w]; currentIndex = 0; renderQuestion();
    document.querySelector(".option-btn").click();
    let err = null; window.speechSynthesis.speak = () => {};
    try { document.querySelectorAll(".opt-sound-btn").forEach(b => b.click()); } catch (e) { err = String(e); }
    return { word: w.en, err, buttons: document.querySelectorAll(".opt-sound-btn").length };
  });
  check("T4b 含撇號單字的發音按鈕不報錯", !t4b.err && t4b.buttons > 0, JSON.stringify(t4b));

  // T5 finish a day -> history saved, day marked done
  const t5 = await page.evaluate(() => {
    setQuizType("wordDef"); changeDay(1);
    currentTestQueue = currentTestQueue.slice(0, 3); currentIndex = 0; correctCount = 0; wrongCount = 0;
    for (let i = 0; i < 3; i++) { renderQuestion(); document.querySelector(".option-btn").click(); currentIndex++; }
    renderQuestion();
    const saved = JSON.parse(localStorage.getItem("local_vocab_progress"));
    return { hist: saved.historyByQuiz.wordDef.Day_1, opt1: document.getElementById("daySelect").options[0].text, done: document.getElementById("wordDisplay").innerText };
  });
  check("T5 完成一天後寫入紀錄並打勾", t5.hist && t5.hist.total === 3 && t5.opt1.startsWith("✓"), JSON.stringify(t5));

  // T6 wrong answer -> mistake recorded -> mistake review queue
  const t6 = await page.evaluate(() => {
    setQuizType("cloze"); changeDay(2);
    const before = (userStore.mistakesByQuiz.cloze || []).length;
    const cur = currentTestQueue[currentIndex];
    const wrong = [...document.querySelectorAll(".option-btn")].find(b => b.dataset.target !== cur.en);
    wrong.click();
    const after = userStore.mistakesByQuiz.cloze.length;
    reviewMistakes();
    return { before, after, link: document.getElementById("mistakeLink").innerText, queue: currentTestQueue.length };
  });
  check("T6 答錯記入錯題本並可純錯題特訓", t6.after === t6.before + 1 && t6.queue === t6.after, JSON.stringify(t6));

  // T7 keyboard shortcuts
  const t7 = await page.evaluate(() => { setQuizType("wordDef"); changeDay(3); return currentIndex; });
  await page.keyboard.press("2");
  const answered = await page.evaluate(() => hasAnswered);
  await page.keyboard.press("Space");
  const t7b = await page.evaluate(() => ({ idx: currentIndex, answered: hasAnswered }));
  check("T7 鍵盤 2 作答、空白鍵下一題", answered && t7b.idx === t7 + 1 && !t7b.answered, JSON.stringify(t7b));

  // T8 account / sync / admin logic with stubbed Supabase
  dialogs.length = 0;
  const t8 = await page.evaluate(async () => {
    const out = {};
    const u = { id: "u-1", email: "a@test.local" };
    saveKnownUser(u, { access_token: "x", refresh_token: "r1", user: u }, getDefaultStore());
    saveKnownUser(u, null, getDefaultStore());
    out.keepSession = getKnownUsers().find(k => k.id === "u-1").session?.refresh_token === "r1";

    // switch to an account whose session is rejected -> back to login, email prefilled, session cleared
    const realAuth = supabaseClient.auth;
    // network-type failure (no HTTP status) must keep the stored session and stay on the current user
    supabaseClient.auth = { ...realAuth, setSession: async () => ({ data: { session: null }, error: { message: "Failed to fetch", status: 0 } }), getSession: async () => ({ data: { session: null } }) };
    isGuest = true; currentUser = { id: "guest", email: "訪客" };
    await switchToAccount(getKnownUsers().find(k => k.id === "u-1"));
    out.netKeepsSession = getKnownUsers().find(k => k.id === "u-1").session?.refresh_token === "r1" && currentUser.id === "guest";
    // auth rejection (HTTP 400 invalid refresh token)
    supabaseClient.auth.setSession = async () => ({ data: { session: null }, error: { message: "Invalid Refresh Token", status: 400 } });
    isGuest = true; currentUser = { id: "guest", email: "訪客" };
    await switchToAccount(getKnownUsers().find(k => k.id === "u-1"));
    out.backToLogin = !document.getElementById("authSection").classList.contains("hidden");
    out.emailPrefilled = document.getElementById("authEmail").value === "a@test.local";
    out.sessionCleared = getKnownUsers().find(k => k.id === "u-1").session === null;

    // sync with mismatched auth user -> not uploaded, status warns
    isGuest = false; currentUser = { id: "u-2", email: "b@test.local" }; cloudReadOk = true;
    let upserted = false;
    const realFrom = supabaseClient.from.bind(supabaseClient);
    supabaseClient.from = () => ({ upsert: async () => { upserted = true; return { error: null }; } });
    supabaseClient.auth.getSession = async () => ({ data: { session: { user: { id: "u-1" } } } });
    await syncToCloud();
    out.mismatchBlocked = !upserted && document.getElementById("syncStatus").innerText.includes("失效");

    // sync where upsert returns an error -> status not "已同步"
    supabaseClient.auth.getSession = async () => ({ data: { session: { user: { id: "u-2" } } } });
    supabaseClient.from = () => ({ upsert: async () => ({ error: { message: "rls" } }) });
    await syncToCloud();
    out.upsertErrorShown = document.getElementById("syncStatus").innerText.includes("本地暫存");

    // admin update that affects 0 rows -> failure alert
    document.body.insertAdjacentHTML("beforeend", '<input id="startDate_u-9" value="2026-11-01">');
    supabaseClient.from = () => ({
      select: () => ({ eq: () => ({ single: async () => ({ data: { stats: {} }, error: null }) }) }),
      update: () => ({ eq: () => ({ select: async () => ({ data: [], error: null }) }) }),
    });
    await adminUpdateUserStartDate("u-9");

    // v6.2.5: cloud read failure blocks upload; "no row" (PGRST116) counts as success
    let up2 = false;
    supabaseClient.auth.getSession = async () => ({ data: { session: { user: { id: "u-2" } } } });
    supabaseClient.from = () => ({
      select: () => ({ eq: () => ({ single: async () => ({ data: null, error: { code: "08006", message: "network" } }) }) }),
      upsert: async () => { up2 = true; return { error: null }; },
    });
    cloudReadOk = false;
    await syncToCloud();
    out.readFailBlocksUpload = !up2 && !cloudReadOk && document.getElementById("syncStatus").innerText.includes("無法讀取");
    supabaseClient.from = () => ({
      select: () => ({ eq: () => ({ single: async () => ({ data: null, error: { code: "PGRST116" } }) }) }),
      upsert: async () => { up2 = true; return { error: null }; },
    });
    await syncToCloud();
    out.newUserUploads = up2 && cloudReadOk;

    // v6.2.5: admin session is never stored
    saveKnownUser({ id: "adm", email: ADMIN_EMAIL }, { refresh_token: "secret" }, getDefaultStore());
    out.adminSessionNotStored = getKnownUsers().find(k => k.id === "adm").session === null;

    // v6.2.5: password login does not inherit the previous user's progress
    userStore.historyByQuiz.wordDef = { Day_9: { score: 1, total: 1 } };
    isGuest = false; currentUser = { id: "u-2", email: "b@test.local" };
    supabaseClient.auth.signInWithPassword = async () => ({ data: { user: { id: "u-new", email: "new@test.local" }, session: { refresh_token: "n" } }, error: null });
    supabaseClient.from = () => ({ select: () => ({ eq: () => ({ single: async () => ({ data: null, error: { code: "PGRST116" } }) }) }), upsert: async () => ({ error: null }) });
    switchAuthTab("login");
    document.getElementById("authEmail").value = "new@test.local"; document.getElementById("authPassword").value = "pw123456";
    await handleSupabaseAuth();
    out.loginNoInherit = !userStore.historyByQuiz.wordDef.Day_9 && !getKnownUsers().find(k => k.id === "u-new").store.historyByQuiz.wordDef.Day_9;

    supabaseClient.from = realFrom; supabaseClient.auth = realAuth;
    return out;
  });
  check("T8f 網路異常切換帳號時保留 session", t8.netKeepsSession);
  check("T8g 雲端讀取失敗時不上傳；新帳號 (無資料) 可上傳", t8.readFailBlocksUpload && t8.newUserUploads, JSON.stringify({ a: t8.readFailBlocksUpload, b: t8.newUserUploads }));
  check("T8h 不保存管理者 session", t8.adminSessionNotStored);
  check("T8i 登入其他帳號不沿用前一位進度", t8.loginNoInherit);

  // v6.2.5: XSS in admin table is escaped
  const t12 = await page.evaluate(async () => {
    const realFrom = supabaseClient.from.bind(supabaseClient);
    const evil = '<img src=x onerror="window.__xss=1">';
    supabaseClient.from = (t) => ({ select: () => ({ order: async () => (t === "user_progress"
      ? { data: [{ user_id: "x-1", email: evil, stats: {}, historyByQuiz: {}, mistakesByQuiz: {}, updated_at: new Date().toISOString() }], error: null }
      : { data: [], error: null }) }) });
    await fetchAdminAllProgress();
    await new Promise(r => setTimeout(r, 300));
    supabaseClient.from = realFrom;
    const cell = document.querySelector("#adminProgressTableBody td");
    return { xss: !!window.__xss, img: !!document.querySelector("#adminProgressTableBody img"), text: cell && cell.textContent };
  });
  check("T12 後台表格 email 不會執行 HTML/程式 (XSS)", !t12.xss && !t12.img && t12.text.includes("<img"), JSON.stringify(t12));

  // v6.2.5: cloze/exam distractors never share the answer's zh; cloze masking edge cases
  const t13 = await page.evaluate(() => {
    const bad = [];
    for (const t of ["cloze", "exam"]) {
      setQuizType(t);
      const pool = shuffle(FULL_VOCAB).slice(0, 400);
      isRandomMode = true; currentTestQueue = pool; currentIndex = 0;
      for (let i = 0; i < pool.length; i++) {
        renderQuestion();
        const opts = [...document.querySelectorAll(".option-btn")].map(b => b.dataset.target);
        const ansZh = pool[i].zh;
        if (!CAP_EXAM_BANK[pool[i].en.toLowerCase()]) opts.filter(o => o !== pool[i].en).forEach(o => { const w = VOCAB_MAP_EN[o.toLowerCase()]; if (w && w.zh === ansZh) bad.push(`${t}:${pool[i].en}/${o}`); });
        currentIndex++;
      }
    }
    const am = createClozeSentence("The shop opens at 9:00 A.M. every day.", "A.M.", "上午");
    const miss = createClozeSentence("This sentence has no target.", "banana", "香蕉");
    const hy = createClozeSentence("Add some soy-sauce.", "soy sauce", "醬油");
    return { bad, amMasked: am.hasReplaced && !am.masked.includes("A.M."), missHidesAnswer: !miss.hasReplaced && !/banana/i.test(miss.masked) && miss.masked.includes("香蕉"), hyphen: hy.hasReplaced };
  });
  check("T13a 填空/真題干擾選項不與正解同義", t13.bad.length === 0, t13.bad.slice(0, 5).join(", "));
  check("T13b 挖空：A.M. 可挖空、找不到字時不洩漏答案、空白/連字號互通", t13.amMasked && t13.missHidesAnswer && t13.hyphen, JSON.stringify(t13));
  const adminAlert = dialogs.find(m => m.includes("修改失敗"));
  check("T8a 同步時保留已存的 session", t8.keepSession);
  check("T8b session 失效時退回登入並帶入 email", t8.backToLogin && t8.emailPrefilled && t8.sessionCleared, JSON.stringify(t8));
  check("T8c 登入身分不符時不上傳雲端", t8.mismatchBlocked);
  check("T8d 雲端寫入失敗時不顯示已同步", t8.upsertErrorShown);
  check("T8e 後台更新 0 筆時顯示失敗", !!adminAlert, adminAlert || dialogs.join(" | "));

  // T14 (v6.2.7) real user_progress schema: unknown columns are rejected like PostgREST does
  const t14 = await page.evaluate(async () => {
    const COLS = ["user_id", "email", "history", "mistakes", "stats", "startdate", "updated_at"];
    const realFrom = supabaseClient.from.bind(supabaseClient), realAuth = supabaseClient.auth;
    const user = { id: "s-1", email: "s1@test.local" };
    // cloud row in the OLD format: history = { Day_N }, mistakes = [], start date set by admin
    let cloudRow = { user_id: "s-1", email: user.email, history: { Day_1: { score: 80, total: 83, quizType: "wordDef" } }, mistakes: [{ en: "cable", zh: "電纜" }], stats: { totalQ: 83, totalCorrect: 80, startDate: "2026-10-23", wordDef: { total: 83, correct: 80 } } };
    let lastUpsert = null, badColumn = null;
    supabaseClient.auth = { ...realAuth, getSession: async () => ({ data: { session: { user } } }) };
    supabaseClient.from = () => ({
      select: (sel) => ({ eq: () => ({ single: async () => {
        const bad = sel.split(",").map(s => s.trim()).find(c => !COLS.includes(c));
        if (bad) { badColumn = bad; return { data: null, error: { code: "42703", message: `column user_progress.${bad} does not exist` } }; }
        return { data: cloudRow, error: null };
      } }) }),
      upsert: async (payload) => {
        const bad = Object.keys(payload).find(c => !COLS.includes(c));
        if (bad) { badColumn = bad; return { error: { code: "PGRST204", message: `Could not find the '${bad}' column` } }; }
        lastUpsert = payload; cloudRow = { ...cloudRow, ...payload }; return { error: null };
      },
    });
    // local (never-uploaded) progress on this device: wordDef Day_2 and hard Day_1
    const local = getDefaultStore();
    local.historyByQuiz.wordDef = { Day_2: { score: 90, total: 83, quizType: "wordDef" } };
    local.historyByQuiz.hard = { Day_1: { score: 1, total: 1, quizType: "hard" } };
    local.stats.totalQ = 100; local.stats.wordDef = { total: 97, correct: 90 };
    saveKnownUser(user, null, local);
    isGuest = false;
    enterApp(user);
    await new Promise(r => setTimeout(r, 400));
    setQuizType("wordDef");
    const out = {
      readOk: cloudReadOk,
      startDate: userStore.startDate,
      day1Label: document.getElementById("daySelect").options[0].text,
      wordDefDays: Object.keys(userStore.historyByQuiz.wordDef).sort(),
      hardDays: Object.keys(userStore.historyByQuiz.hard),
      mistakes: userStore.mistakesByQuiz.wordDef.map(m => m.en),
      totalQ: userStore.stats.totalQ,
    };
    await syncToCloud();
    out.uploaded = !!lastUpsert && !!lastUpsert.history && "hard" in lastUpsert.history;
    out.badColumn = badColumn;
    out.status = document.getElementById("syncStatus").innerText;
    // reload-equivalent: fresh store, read back what was uploaded
    saveKnownUser(user, null, getDefaultStore());
    enterApp(user);
    await new Promise(r => setTimeout(r, 400));
    out.afterReload = { startDate: userStore.startDate, wordDefDays: Object.keys(userStore.historyByQuiz.wordDef).sort(), hardDays: Object.keys(userStore.historyByQuiz.hard) };
    // admin view reads the new-format column correctly
    out.adminHist = Object.keys(readHistoryColumn(cloudRow.history).hard);
    supabaseClient.from = realFrom; supabaseClient.auth = realAuth;
    return out;
  });
  check("T14a 雲端讀取只用存在的欄位且成功", t14.readOk && !t14.badColumn, JSON.stringify({ readOk: t14.readOk, bad: t14.badColumn }));
  check("T14b 重新整理後啟動日為後台設定的 10/23", t14.startDate === "2026-10-23" && t14.day1Label.includes("2026-10-23") && t14.afterReload.startDate === "2026-10-23", t14.day1Label);
  check("T14c 雲端舊格式與本機紀錄合併不遺失", JSON.stringify(t14.wordDefDays) === '["Day_1","Day_2"]' && t14.hardDays.includes("Day_1") && t14.mistakes.includes("cable") && t14.totalQ === 100, JSON.stringify(t14));
  check("T14d 上傳成功且重新整理後讀回五大題型紀錄", t14.uploaded && t14.status.includes("已同步") && JSON.stringify(t14.afterReload.wordDefDays) === '["Day_1","Day_2"]' && t14.afterReload.hardDays.includes("Day_1") && t14.adminHist.includes("Day_1"), JSON.stringify({ up: t14.uploaded, st: t14.status, ar: t14.afterReload }));

  // T9 service worker + offline
  await page.reload({ waitUntil: "networkidle0" });
  await page.evaluate(() => navigator.serviceWorker.ready);
  await new Promise(r => setTimeout(r, 1500));
  const cached = await page.evaluate(async () => { const c = await caches.open((await caches.keys())[0]); return (await c.keys()).map(r => r.url.replace(location.origin, "")); });
  check("T9a SW 預存主程式與題庫", cached.includes("/vocab-quiz-30days.html") && cached.includes("/vocab2000.js"), cached.join(", "));
  await page.reload({ waitUntil: "networkidle0" });
  await page.setOfflineMode(true);
  await page.reload({ waitUntil: "domcontentloaded" });
  await new Promise(r => setTimeout(r, 2000));
  const off = await page.evaluate(() => (window.EXTERNAL_VOCAB || []).length);
  check("T9b 離線可開啟並載入題庫", off === 2486, `vocab=${off}`);
  await page.setOfflineMode(false);

  // T11 auto refresh to latest version at round start
  await page.reload({ waitUntil: "networkidle0" });
  await page.waitForFunction(() => typeof versionBaseline === "string" && versionBaseline.length > 1, { timeout: 10000 });
  await page.evaluate(() => { loginAsGuest(); window.__marker = 1; lastVersionCheck = 0; });
  await page.evaluate(() => ensureLatestVersion());
  const kept = await page.evaluate(() => window.__marker === 1);
  check("T11a 版本未變時不重新整理", kept);
  await page.evaluate(() => { versionBaseline = "stale|stale"; lastVersionCheck = 0; window.__marker = 1; });
  const nav = page.waitForNavigation({ waitUntil: "networkidle0", timeout: 10000 }).then(() => true).catch(() => false);
  await page.evaluate(() => { changeDay(2); });
  const reloaded = await nav;
  const afterReload = await page.evaluate(() => ({ marker: window.__marker, baselineOk: typeof versionBaseline === "string" }));
  check("T11b 偵測到新版時開始做題會自動重新整理", reloaded && afterReload.marker === undefined, JSON.stringify(afterReload));
  await page.waitForFunction(() => typeof versionBaseline === "string" && versionBaseline.length > 1, { timeout: 10000 });
  await page.evaluate(() => { loginAsGuest(); window.__marker = 2; lastVersionCheck = 0; });
  await page.evaluate(() => ensureLatestVersion());
  check("T11c 重新整理後不會一直重複刷新", await page.evaluate(() => window.__marker === 2));

  const relevantErrors = pageErrors.filter(e => !/supabase|Failed to fetch|NetworkError/i.test(e));
  check("T10 無未預期的 JavaScript 錯誤", relevantErrors.length === 0, relevantErrors.slice(0, 5).join(" | "));

  await browser.close();
  const failed = results.filter(r => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error("SUITE CRASHED", e); process.exit(2); });
