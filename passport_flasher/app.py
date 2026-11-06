#!/usr/bin/env python3
"""AI Passport 燒錄工具（macOS 圖形介面）。

三個分頁：
  官方社區玩法   直接從 https://ai-passport.folotoy.cn/plays/ 挑玩法上傳（同官方網頁安裝）
  開源專案       GitHub 專案（含我的 hakkaataipassport）→ 下載 → ESP-IDF 編譯 → 上傳
  韌體檔         本機 .bin 或 GitHub Release 的 .bin → 直接上傳

執行：python3 app.py（依賴見 requirements.txt）
"""
from __future__ import annotations

import sys

# 打包成 .app 後，用 app 自己當 esptool 執行（flasher_core.esptool_cmd 會這樣呼叫）
if len(sys.argv) > 1 and sys.argv[1] == "--esptool":
    import esptool
    sys.argv = ["esptool", *sys.argv[2:]]
    esptool._main()
    sys.exit(0)

import codecs  # noqa: E402
import os  # noqa: E402
import queue  # noqa: E402
import re  # noqa: E402
import shlex  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import traceback  # noqa: E402
import webbrowser  # noqa: E402
from pathlib import Path  # noqa: E402

import tkinter as tk  # noqa: E402
from tkinter import filedialog, messagebox, ttk  # noqa: E402
from tkinter.scrolledtext import ScrolledText  # noqa: E402

import flasher_core as core  # noqa: E402
from flasher_core import FlashError  # noqa: E402

try:
    from PIL import Image, ImageTk  # 選用：顯示玩法縮圖（webp）
except ImportError:  # pragma: no cover
    Image = ImageTk = None

BAUDS = ["460800", "921600", "115200"]
NINJA_RE = re.compile(r"^\[(\d+)/(\d+)\]\s")
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
# 平台差異集中在這裡（目前以 macOS 為主，Windows 之後支援）
if IS_MAC:
    UI_FONT, MONO_FONT = ("PingFang TC", 13), ("Menlo", 11)
elif IS_WIN:
    UI_FONT, MONO_FONT = ("Microsoft JhengHei UI", 10), ("Consolas", 10)
else:
    UI_FONT, MONO_FONT = ("Noto Sans CJK TC", 10), ("DejaVu Sans Mono", 9)


def open_in_file_manager(path: Path) -> None:
    if IS_WIN:
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if IS_MAC else "xdg-open", str(path)])


class Cancelled(Exception):
    pass


# ====================================================================== 背景工作
class Job:
    """在背景執行緒跑的工作；透過 queue 把紀錄、進度送回畫面。"""

    def __init__(self, app: "App"):
        self.app = app
        self.cancel = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.found_chip: str | None = None
        self.mac: str | None = None

    # ---- 回報
    def log(self, s: str) -> None:
        self.app.q.put(("log", s))

    def status(self, s: str) -> None:
        self.app.q.put(("status", s))

    def progress(self, pct: float | None) -> None:
        self.app.q.put(("progress", pct))

    def check(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    # ---- 執行外部程式
    def run_cmd(self, argv: list[str], cwd: str | Path | None = None, label: str = "") -> None:
        self.check()
        shown = " ".join(shlex.quote(a) for a in argv)
        if len(shown) > 400:
            shown = shown[:400] + " …"
        self.log(f"\n$ {shown}\n")
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=core.tool_env())
        except FileNotFoundError as e:
            raise FlashError(f"找不到程式：{argv[0]}。{self._missing_hint(argv[0])}") from e
        self.proc = proc
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        buf = ""
        fd = proc.stdout.fileno()
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            buf += dec.decode(chunk)
            while True:
                m = re.search(r"[\r\n]", buf)
                if not m:
                    break
                self._line(buf[:m.start()])
                buf = buf[m.end():]
        buf += dec.decode(b"", final=True)
        if buf:
            self._line(buf)
        rc = proc.wait()
        self.proc = None
        if self.cancel.is_set():
            raise Cancelled()
        if rc != 0:
            raise FlashError(f"{label or Path(argv[0]).name} 失敗（結束代碼 {rc}），詳細原因請看下方紀錄。")

    @staticmethod
    def _missing_hint(prog: str) -> str:
        if prog == "git":
            return "請在「終端機」執行 xcode-select --install 安裝開發者工具。"
        return ""

    def _line(self, line: str) -> None:
        line = core.strip_ansi(line).rstrip()
        if not line.strip():
            return
        pct = core.parse_progress(line)
        if pct is not None:
            self.progress(pct)
            self.status(line.strip()[:110])
            if pct >= 100:
                self.log(line.strip() + "\n")
            return
        m = NINJA_RE.match(line)
        if m:  # ESP-IDF 編譯進度 [123/456]
            done, total = int(m.group(1)), int(m.group(2))
            self.progress(done * 100 / max(total, 1))
            self.status(f"編譯中 {done}/{total}")
            if done == total or done % 50 == 0:
                self.log(line + "\n")
            return
        chip = core.parse_chip(line)
        if chip:
            self.found_chip = chip
        mm = re.search(r"MAC:\s*([0-9a-f:]{17})", line, re.I)
        if mm:
            self.mac = mm.group(1)
        self.log(line + "\n")

    def stop(self) -> None:
        self.cancel.set()
        p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass


# ====================================================================== 主視窗
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{core.APP_NAME} {core.APP_VERSION}")
        self.geometry("1120x840")
        self.minsize(940, 700)
        self.option_add("*Font", UI_FONT)
        self.cfg = core.load_config()
        self.q: queue.Queue = queue.Queue()
        self.job: Job | None = None
        self.action_buttons: list[ttk.Button] = []
        self.ports: list[core.PortInfo] = []
        self.plays: list[core.Play] = []
        self.play_offset = 0
        self.thumb_cache: dict[str, object] = {}

        style = ttk.Style(self)
        if "aqua" not in style.theme_names():
            style.theme_use("clam")
        style.configure("Accent.TButton", font=(UI_FONT[0], UI_FONT[1], "bold"))
        style.configure("Hint.TLabel", foreground="#666")
        style.configure("Big.TLabel", font=(UI_FONT[0], UI_FONT[1] + 4, "bold"))

        self._build_device_bar()
        self._build_tabs()
        self._build_menu()
        self._build_log()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._poll_queue)
        self.after(300, self.refresh_ports)
        self.after(600, self.search_plays)
        self.after(2500, self._auto_refresh_ports)

    # ------------------------------------------------------------------ 版面
    def _build_device_bar(self) -> None:
        f = ttk.LabelFrame(self, text="① 用 USB-C 連接 AI Passport（請用可傳資料的線，裝置要開機）", padding=8)
        f.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(f, text="序列埠").grid(row=0, column=0, sticky="w")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(f, textvariable=self.port_var, state="readonly", width=62)
        self.port_combo.grid(row=0, column=1, sticky="we", padx=6)
        ttk.Button(f, text="重新整理", command=self.refresh_ports).grid(row=0, column=2)
        b = ttk.Button(f, text="檢查裝置", command=self.check_device)
        b.grid(row=0, column=3, padx=6)
        self.action_buttons.append(b)
        ttk.Label(f, text="速度").grid(row=0, column=4)
        self.baud_var = tk.StringVar(value=str(self.cfg.get("baud", BAUDS[0])))
        ttk.Combobox(f, textvariable=self.baud_var, values=BAUDS, width=8, state="readonly").grid(row=0, column=5, padx=4)
        self.device_var = tk.StringVar(value="尋找裝置中…")
        ttk.Label(f, textvariable=self.device_var, style="Hint.TLabel").grid(row=1, column=1, columnspan=4, sticky="w", padx=6)
        ttk.Button(f, text=f"v{core.APP_VERSION} 版本紀錄", command=self.show_about).grid(row=1, column=5, sticky="e", pady=(4, 0))
        f.columnconfigure(1, weight=1)

    def _build_menu(self) -> None:
        m = tk.Menu(self)
        if IS_MAC:
            app_menu = tk.Menu(m, name="apple")  # macOS 的應用程式選單
            app_menu.add_command(label=f"關於 {core.APP_NAME}", command=self.show_about)
            m.add_cascade(menu=app_menu)
            try:
                self.createcommand("tkAboutDialog", self.show_about)
            except tk.TclError:
                pass
        h = tk.Menu(m, tearoff=False)
        h.add_command(label="上傳紀錄", command=lambda: self.nb.select(self.history_tab))
        h.add_command(label="燒錄工具版本紀錄", command=self.show_about)
        h.add_separator()
        h.add_command(label="打開下載資料夾", command=lambda: open_in_file_manager(core.workspace_dir()))
        h.add_command(label="AI Passport 玩法社區", command=lambda: webbrowser.open(core.COMMUNITY_BASE + "/plays/"))
        m.add_cascade(label="說明", menu=h)
        self.configure(menu=m)

    def show_about(self) -> None:
        w = tk.Toplevel(self)
        w.title(f"{core.APP_NAME} — 版本紀錄")
        w.geometry("640x520")
        w.transient(self)
        ttk.Label(w, text=f"{core.APP_NAME}  v{core.APP_VERSION}", style="Big.TLabel").pack(anchor="w", padx=14, pady=(12, 0))
        ttk.Label(w, text="by Claude Code", style="Hint.TLabel").pack(anchor="w", padx=14)
        ttk.Label(w, text=f"esptool {_esptool_version()} · Python {sys.version.split()[0]} · 資料夾 {core.workspace_dir()}",
                  style="Hint.TLabel").pack(anchor="w", padx=14, pady=(2, 8))
        txt = ScrolledText(w, wrap="word", relief="flat", padx=8, pady=8)
        txt.pack(fill="both", expand=True, padx=10)
        _render_markdown(txt, core.read_changelog())
        txt.configure(state="disabled")
        ttk.Button(w, text="關閉", command=w.destroy).pack(pady=8)

    def _build_tabs(self) -> None:
        wrap = ttk.LabelFrame(self, text="② 選擇要上傳的內容", padding=6)
        wrap.pack(fill="both", expand=True, padx=10, pady=4)
        self.nb = ttk.Notebook(wrap)
        self.nb.pack(fill="both", expand=True)
        self._build_community_tab()
        self._build_project_tab()
        self._build_file_tab()
        self._build_history_tab()
        try:
            self.nb.select(int(self.cfg.get("tab", 0)))
        except tk.TclError:
            pass

    # ---- 分頁 1：官方社區
    def _build_community_tab(self) -> None:
        t = ttk.Frame(self.nb, padding=6)
        self.nb.add(t, text="  官方社區玩法  ")
        top = ttk.Frame(t)
        top.pack(fill="x")
        ttk.Label(top, text="搜尋").pack(side="left")
        self.q_var = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.q_var, width=22)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _e: self.search_plays())
        ttk.Label(top, text="分類").pack(side="left", padx=(8, 0))
        self.cat_var = tk.StringVar(value=core.COMMUNITY_CATEGORIES[0][1])
        c = ttk.Combobox(top, textvariable=self.cat_var, state="readonly", width=9,
                         values=[n for _k, n in core.COMMUNITY_CATEGORIES])
        c.pack(side="left", padx=4)
        c.bind("<<ComboboxSelected>>", lambda _e: self.search_plays())
        ttk.Label(top, text="排序").pack(side="left", padx=(8, 0))
        self.sort_var = tk.StringVar(value=core.COMMUNITY_SORTS[0][1])
        s = ttk.Combobox(top, textvariable=self.sort_var, state="readonly", width=9,
                         values=[n for _k, n in core.COMMUNITY_SORTS])
        s.pack(side="left", padx=4)
        s.bind("<<ComboboxSelected>>", lambda _e: self.search_plays())
        ttk.Button(top, text="搜尋", command=self.search_plays).pack(side="left", padx=6)
        ttk.Button(top, text="開啟社區網站", command=lambda: webbrowser.open(core.COMMUNITY_BASE + "/plays/")).pack(side="right")

        pw = ttk.PanedWindow(t, orient="horizontal")
        pw.pack(fill="both", expand=True, pady=(6, 0))
        left = ttk.Frame(pw)
        cols = ("title", "author", "cat", "dl", "size")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse", height=12)
        for col, txt, w, anchor in (("title", "玩法", 250, "w"), ("author", "作者", 110, "w"),
                                    ("cat", "分類", 70, "center"), ("dl", "下載", 70, "e"),
                                    ("size", "大小", 80, "e")):
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor=anchor, stretch=(col == "title"))
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_play())
        self.tree.bind("<Double-1>", lambda _e: self.flash_play())
        bottom = ttk.Frame(left)
        bottom.grid(row=1, column=0, columnspan=2, sticky="we", pady=4)
        self.more_btn = ttk.Button(bottom, text="載入更多", command=lambda: self.search_plays(more=True))
        self.more_btn.pack(side="left")
        self.count_var = tk.StringVar()
        ttk.Label(bottom, textvariable=self.count_var, style="Hint.TLabel").pack(side="left", padx=8)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        pw.add(left, weight=3)

        right = ttk.Frame(pw, padding=(8, 0, 0, 0))
        self.thumb_label = ttk.Label(right)
        self.thumb_label.pack(anchor="w")
        self.play_text = tk.Text(right, wrap="word", height=12, relief="flat", padx=4, pady=4,
                                 background=self._bg(), highlightthickness=0)
        self.play_text.pack(fill="both", expand=True)
        self.play_text.tag_configure("h", font=(UI_FONT[0], UI_FONT[1] + 4, "bold"))
        self.play_text.tag_configure("sub", foreground="#666")
        self.play_text.tag_configure("b", font=(UI_FONT[0], UI_FONT[1], "bold"))
        self.play_text.configure(state="disabled")
        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=4)
        b = ttk.Button(btns, text="⬆ 上傳到 AI Passport", style="Accent.TButton", command=self.flash_play)
        b.pack(side="left")
        self.action_buttons.append(b)
        ttk.Button(btns, text="網頁", command=self.open_play_page).pack(side="left", padx=4)
        self.src_btn = ttk.Button(btns, text="原始碼 → 編譯", command=self.play_to_project, state="disabled")
        self.src_btn.pack(side="left")
        pw.add(right, weight=2)

    def _bg(self) -> str:
        try:
            return ttk.Style(self).lookup("TFrame", "background") or "#ececec"
        except tk.TclError:
            return "#ececec"

    # ---- 分頁 2：開源專案
    def _build_project_tab(self) -> None:
        t = ttk.Frame(self.nb, padding=6)
        self.nb.add(t, text="  開源專案（GitHub → 編譯 → 上傳）  ")
        left = ttk.Frame(t)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="專案清單").pack(anchor="w")
        self.proj_list = tk.Listbox(left, width=30, height=14, exportselection=False, activestyle="none")
        self.proj_list.pack(fill="y", expand=True)
        self.proj_list.bind("<<ListboxSelect>>", lambda _e: self.show_project())
        pb = ttk.Frame(left)
        pb.pack(fill="x", pady=4)
        ttk.Button(pb, text="＋ GitHub", command=self.add_github_project).pack(side="left")
        ttk.Button(pb, text="＋ 本機資料夾", command=self.add_local_project).pack(side="left", padx=4)
        ttk.Button(pb, text="移除", command=self.remove_project).pack(side="left")

        r = ttk.Frame(t, padding=(12, 0, 0, 0))
        r.pack(side="left", fill="both", expand=True)
        self.proj_name_var = tk.StringVar()
        ttk.Label(r, textvariable=self.proj_name_var, style="Big.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        self.proj_info_var = tk.StringVar()
        ttk.Label(r, textvariable=self.proj_info_var, style="Hint.TLabel", wraplength=620, justify="left").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(2, 8))

        ttk.Label(r, text="分支／標籤").grid(row=2, column=0, sticky="w")
        self.ref_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.ref_var, width=20).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(r, text="（空白＝預設分支最新版）", style="Hint.TLabel").grid(row=2, column=2, columnspan=2, sticky="w")

        ttk.Label(r, text="ESP-IDF").grid(row=3, column=0, sticky="w", pady=6)
        self.idf_var = tk.StringVar(value=self.cfg.get("idf_path", ""))
        self.idf_combo = ttk.Combobox(r, textvariable=self.idf_var, width=46)
        self.idf_combo.grid(row=3, column=1, columnspan=2, sticky="we", padx=6)
        self.idf_combo.bind("<<ComboboxSelected>>", lambda _e: self.update_idf_label())
        self.idf_combo.bind("<FocusOut>", lambda _e: self.update_idf_label())
        idfb = ttk.Frame(r)
        idfb.grid(row=3, column=3, sticky="w")
        ttk.Button(idfb, text="選擇…", command=self.browse_idf).pack(side="left")
        b = ttk.Button(idfb, text=f"安裝 {core.DEFAULT_IDF_VERSION}", command=self.install_idf)
        b.pack(side="left", padx=4)
        self.action_buttons.append(b)
        self.idf_info_var = tk.StringVar()
        ttk.Label(r, textvariable=self.idf_info_var, style="Hint.TLabel").grid(row=4, column=1, columnspan=3, sticky="w", padx=6)

        ttk.Label(r, text="寫入方式").grid(row=5, column=0, sticky="nw", pady=8)
        self.proj_mode = tk.StringVar(value=self.cfg.get("project_mode", "segments"))
        mf = ttk.Frame(r)
        mf.grid(row=5, column=1, columnspan=3, sticky="w", pady=8)
        ttk.Radiobutton(mf, text="分段寫入（等同 idf.py flash，保留 Wi-Fi／配網設定，建議）",
                        variable=self.proj_mode, value="segments").pack(anchor="w")
        ttk.Radiobutton(mf, text="完整寫入 0x0（同官方社區安裝，會清除設定）",
                        variable=self.proj_mode, value="full").pack(anchor="w")

        bf = ttk.Frame(r)
        bf.grid(row=6, column=0, columnspan=4, sticky="w", pady=10)
        for text, cmd, accent in (("一鍵：下載 → 編譯 → 上傳", self.project_all, True),
                                  ("① 下載／更新原始碼", self.project_sync, False),
                                  ("② 編譯", self.project_build, False),
                                  ("③ 上傳", self.project_flash, False)):
            b = ttk.Button(bf, text=text, command=cmd, style="Accent.TButton" if accent else "TButton")
            b.pack(side="left", padx=(0, 6))
            self.action_buttons.append(b)
        of = ttk.Frame(r)
        of.grid(row=7, column=0, columnspan=4, sticky="w")
        ttk.Button(of, text="在 Finder 打開資料夾" if IS_MAC else "打開資料夾", command=self.open_project_folder).pack(side="left")
        ttk.Button(of, text="GitHub 頁面", command=self.open_project_github).pack(side="left", padx=6)
        ttk.Button(of, text="版本歷史…", command=self.show_project_history).pack(side="left")
        ttk.Label(r, text="第一次編譯需要先安裝 ESP-IDF（約 1–2 GB，10–30 分鐘），並會自動下載專案用到的元件。",
                  style="Hint.TLabel", wraplength=640, justify="left").grid(row=8, column=0, columnspan=4, sticky="w", pady=(14, 0))
        r.columnconfigure(2, weight=1)

        self.reload_projects()
        self.refresh_idf_candidates()

    # ---- 分頁 3：韌體檔
    def _build_file_tab(self) -> None:
        t = ttk.Frame(self.nb, padding=10)
        self.nb.add(t, text="  韌體檔（免編譯直接上傳）  ")
        ttk.Label(t, text="韌體檔 (.bin)").grid(row=0, column=0, sticky="w")
        self.bin_var = tk.StringVar(value=self.cfg.get("last_bin", ""))
        ttk.Entry(t, textvariable=self.bin_var, width=70).grid(row=0, column=1, sticky="we", padx=6)
        ttk.Button(t, text="選擇檔案…", command=self.browse_bin).grid(row=0, column=2)
        self.bin_info_var = tk.StringVar()
        ttk.Label(t, textvariable=self.bin_info_var, style="Hint.TLabel", wraplength=760, justify="left").grid(
            row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(2, 10))
        self.bin_var.trace_add("write", lambda *_a: self.after_idle(self.inspect_bin))

        ttk.Label(t, text="寫入位址").grid(row=2, column=0, sticky="nw")
        mf = ttk.Frame(t)
        mf.grid(row=2, column=1, columnspan=2, sticky="w", padx=6)
        self.bin_mode = tk.StringVar(value="auto")
        for val, txt in (("auto", "自動判斷（建議）"),
                         ("full", "完整映像 → 0x0（社區玩法、*-full.bin）"),
                         ("app", "只有應用程式 → 0x10000（官方預設分區表）")):
            ttk.Radiobutton(mf, text=txt, variable=self.bin_mode, value=val,
                            command=self.inspect_bin).pack(anchor="w")
        cf = ttk.Frame(mf)
        cf.pack(anchor="w")
        ttk.Radiobutton(cf, text="自訂位址", variable=self.bin_mode, value="custom",
                        command=self.inspect_bin).pack(side="left")
        self.offset_var = tk.StringVar(value="0x0")
        ttk.Entry(cf, textvariable=self.offset_var, width=12).pack(side="left", padx=6)

        rel = ttk.LabelFrame(t, text="或從 GitHub Releases 下載 .bin", padding=8)
        rel.grid(row=3, column=0, columnspan=3, sticky="we", pady=14)
        ttk.Label(rel, text="Repo").grid(row=0, column=0, sticky="w")
        self.rel_repo_var = tk.StringVar(value=self.cfg.get("release_repo", ""))
        ttk.Entry(rel, textvariable=self.rel_repo_var, width=46).grid(row=0, column=1, sticky="we", padx=6)
        ttk.Button(rel, text="讀取 Releases", command=self.load_releases).grid(row=0, column=2)
        self.rel_var = tk.StringVar()
        self.rel_combo = ttk.Combobox(rel, textvariable=self.rel_var, state="readonly", width=60)
        self.rel_combo.grid(row=1, column=1, sticky="we", padx=6, pady=6)
        b = ttk.Button(rel, text="下載並選用", command=self.download_release)
        b.grid(row=1, column=2)
        self.action_buttons.append(b)
        rel.columnconfigure(1, weight=1)
        self.release_assets: list[core.ReleaseAsset] = []

        b = ttk.Button(t, text="⬆ 上傳到 AI Passport", style="Accent.TButton", command=self.flash_bin)
        b.grid(row=4, column=1, sticky="w", padx=6)
        self.action_buttons.append(b)
        t.columnconfigure(1, weight=1)
        self.after_idle(self.inspect_bin)

    # ---- 分頁 4：上傳紀錄
    def _build_history_tab(self) -> None:
        t = ttk.Frame(self.nb, padding=6)
        self.history_tab = t
        self.nb.add(t, text="  上傳紀錄  ")
        cols = ("time", "result", "name", "source", "version", "mac")
        self.hist_tree = ttk.Treeview(t, columns=cols, show="headings", selectmode="browse")
        for col, txt, w in (("time", "時間", 150), ("result", "結果", 60), ("name", "內容", 230),
                            ("source", "來源", 80), ("version", "版本", 230), ("mac", "裝置 MAC", 140)):
            self.hist_tree.heading(col, text=txt)
            self.hist_tree.column(col, width=w, stretch=(col in ("name", "version")),
                                  anchor="center" if col in ("result", "source") else "w")
        sb = ttk.Scrollbar(t, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb.set)
        self.hist_tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.hist_tree.bind("<<TreeviewSelect>>", lambda _e: self.show_history_item())
        self.hist_detail = tk.StringVar()
        ttk.Label(t, textvariable=self.hist_detail, style="Hint.TLabel", wraplength=900, justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=4)
        bf = ttk.Frame(t)
        bf.grid(row=2, column=0, columnspan=2, sticky="w")
        b = ttk.Button(bf, text="⬆ 重新上傳這一筆", command=self.reflash_history)
        b.pack(side="left")
        self.action_buttons.append(b)
        ttk.Button(bf, text="清除全部紀錄", command=self.clear_history).pack(side="left", padx=6)
        t.rowconfigure(0, weight=1)
        t.columnconfigure(0, weight=1)
        self.reload_history()

    def reload_history(self) -> None:
        self.history = core.load_history()
        self.hist_tree.delete(*self.hist_tree.get_children())
        icons = {"成功": "✅ 成功", "失敗": "✗ 失敗", "已停止": "■ 停止"}
        for i, h in enumerate(self.history):
            self.hist_tree.insert("", "end", iid=str(i), values=(
                h.get("time", ""), icons.get(h.get("result"), h.get("result", "")), h.get("name", ""),
                h.get("source", ""), h.get("version", ""), h.get("mac", "")))
        self.hist_detail.set(f"共 {len(self.history)} 筆（存在 {core.history_path()}）" if self.history
                             else "還沒有上傳紀錄。")

    def current_history(self) -> dict | None:
        sel = self.hist_tree.selection()
        return self.history[int(sel[0])] if sel else None

    def show_history_item(self) -> None:
        h = self.current_history()
        if not h:
            return
        plan = h.get("plan") or {}
        lines = [f"{plan.get('title', '')} · 序列埠 {h.get('port', '')}" + (f" · {h['chip']}" if h.get("chip") else "")]
        lines += [f"0x{i['offset']:06X}  {i['path']}" for i in plan.get("items", [])]
        if h.get("error"):
            lines.append("錯誤：" + h["error"])
        self.hist_detail.set("\n".join(lines))

    def reflash_history(self) -> None:
        h = self.current_history()
        if not h:
            messagebox.showinfo(core.APP_NAME, "請先選一筆紀錄。")
            return
        try:
            plan = core.plan_from_dict(h.get("plan") or {})
        except FlashError as e:
            self.show_error(str(e))
            return
        port = self.require_port()
        if port:
            self.confirm_and_flash(plan, port, h.get("name", "重新上傳"), h.get("source", ""), h.get("version", ""))

    def clear_history(self) -> None:
        if self.history and messagebox.askyesno(core.APP_NAME, "清除全部上傳紀錄？（不會刪除下載的韌體檔）"):
            core.clear_history()
            self.reload_history()

    # ---- 開源專案的版本歷史
    def show_project_history(self) -> None:
        p = self.current_project()
        if not p:
            return
        path = self.project_path(p)
        if not (path / ".git").exists():
            messagebox.showinfo(core.APP_NAME, "這個專案還沒下載（或不是 git 專案），請先按「① 下載／更新原始碼」。")
            return
        rows = core.git_log(path)
        w = tk.Toplevel(self)
        w.title(f"{p['name']} — 版本歷史")
        w.geometry("820x600")
        w.transient(self)
        ttk.Label(w, text=f"{p['name']}　目前：{core.project_version(path) or '?'}", style="Big.TLabel").pack(
            anchor="w", padx=12, pady=(10, 4))
        pw = ttk.PanedWindow(w, orient="vertical")
        pw.pack(fill="both", expand=True, padx=10)
        top = ttk.Frame(pw)
        tree = ttk.Treeview(top, columns=("hash", "date", "tags", "subject"), show="headings", height=10)
        for col, txt, wd in (("hash", "提交", 80), ("date", "日期", 95), ("tags", "標籤", 90), ("subject", "說明", 520)):
            tree.heading(col, text=txt)
            tree.column(col, width=wd, stretch=(col == "subject"))
        for r in rows:
            tree.insert("", "end", values=(r["hash"], r["date"], ", ".join(r["tags"]), r["subject"]))
        sb = ttk.Scrollbar(top, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        pw.add(top, weight=1)
        cl = next((path / n for n in ("CHANGELOG.md", "CHANGELOG", "HISTORY.md") if (path / n).exists()), None)
        if cl:
            bottom = ttk.Frame(pw)
            ttk.Label(bottom, text=cl.name).pack(anchor="w")
            txt = ScrolledText(bottom, wrap="word", height=10, relief="flat", padx=6, pady=6)
            txt.pack(fill="both", expand=True)
            _render_markdown(txt, cl.read_text("utf-8", "replace"))
            txt.configure(state="disabled")
            pw.add(bottom, weight=1)

        def use_selected():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo(core.APP_NAME, "請先選一個版本。", parent=w)
                return
            vals = tree.item(sel[0], "values")
            ref = vals[2].split(",")[0].strip() if vals[2] else vals[0]
            self.ref_var.set(ref)
            self._save_project_ref(p)
            w.destroy()
            messagebox.showinfo(core.APP_NAME, f"已選擇版本 {ref}。\n按「一鍵：下載 → 編譯 → 上傳」就會用這個版本。\n"
                                               "（要回到最新版，把「分支／標籤」清空）")

        bf = ttk.Frame(w)
        bf.pack(fill="x", padx=10, pady=8)
        ttk.Button(bf, text="使用選取的版本", command=use_selected).pack(side="left")
        ttk.Button(bf, text="回到最新版", command=lambda: (self.ref_var.set(""), self._save_project_ref(p), w.destroy())).pack(
            side="left", padx=6)
        ttk.Button(bf, text="關閉", command=w.destroy).pack(side="right")

    def _build_log(self) -> None:
        f = ttk.LabelFrame(self, text="③ 進度與紀錄", padding=6)
        f.pack(fill="both", padx=10, pady=(4, 10))
        row = ttk.Frame(f)
        row.pack(fill="x")
        self.pbar = ttk.Progressbar(row, mode="determinate", maximum=100)
        self.pbar.pack(side="left", fill="x", expand=True)
        self.stop_btn = ttk.Button(row, text="停止", command=self.stop_job, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(row, text="複製紀錄", command=self.copy_log).pack(side="left")
        ttk.Button(row, text="清除", command=lambda: self.log_text.delete("1.0", "end")).pack(side="left", padx=4)
        self.status_var = tk.StringVar(value="就緒")
        ttk.Label(f, textvariable=self.status_var).pack(anchor="w", pady=2)
        self.log_text = ScrolledText(f, height=10, font=MONO_FONT, wrap="char")
        self.log_text.pack(fill="both", expand=True)

    # ------------------------------------------------------------------ 共用
    def log(self, s: str) -> None:
        self.log_text.insert("end", s)
        self.log_text.see("end")

    def copy_log(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.log_text.get("1.0", "end"))
        self.status_var.set("已複製紀錄")

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log(val)
                elif kind == "status":
                    self.status_var.set(val)
                elif kind == "progress":
                    if val is None:
                        self.pbar.configure(mode="indeterminate")
                        self.pbar.start(12)
                    else:
                        self.pbar.stop()
                        self.pbar.configure(mode="determinate", value=max(0.0, min(100.0, val)))
                elif kind == "call":
                    val()
        except queue.Empty:
            pass
        self.after(60, self._poll_queue)

    def set_busy(self, busy: bool) -> None:
        for b in self.action_buttons:
            b.configure(state="disabled" if busy else "normal")
        self.stop_btn.configure(state="normal" if busy else "disabled")
        if not busy:
            self.pbar.stop()
            self.pbar.configure(mode="determinate")

    def run_job(self, title: str, work, on_done=None, on_error=None) -> None:
        """work(job) 在背景跑；成功後 on_done(結果) 在主執行緒跑。"""
        if self.job:
            messagebox.showinfo(core.APP_NAME, "目前還有工作在進行，請等它完成或按「停止」。")
            return
        job = Job(self)
        self.job = job
        self.set_busy(True)
        self.status_var.set(title)
        self.pbar.configure(value=0)
        self.log(f"\n━━━━ {title} ━━━━\n")

        def runner():
            try:
                res = work(job)
            except Cancelled:
                self.q.put(("call", lambda: self._job_end(job, "已停止。", None)))
            except FlashError as e:
                msg = str(e)
                self.q.put(("call", lambda: self._job_end(job, msg, on_error or self.show_error)))
            except Exception as e:  # noqa: BLE001
                tb = traceback.format_exc()
                job.log(tb)
                msg = f"發生未預期的錯誤：{e}"
                self.q.put(("call", lambda: self._job_end(job, msg, on_error or self.show_error)))
            else:
                self.q.put(("call", lambda: self._job_end(job, None, None, on_done, res)))

        threading.Thread(target=runner, daemon=True).start()

    def _job_end(self, job: Job, err: str | None, err_cb, on_done=None, res=None) -> None:
        self.job = None
        self.set_busy(False)
        if err:
            self.status_var.set(err.splitlines()[0])
            self.log(f"✗ {err}\n")
            if err_cb:
                err_cb(err)
            return
        self.status_var.set("完成")
        if on_done:
            on_done(res)

    def stop_job(self) -> None:
        if self.job:
            self.job.stop()
            self.status_var.set("停止中…")

    def show_error(self, msg: str) -> None:
        tips = self.troubleshoot_tips()
        messagebox.showerror(core.APP_NAME, msg + (("\n\n可以試試：\n" + tips) if tips else ""))

    def troubleshoot_tips(self) -> str:
        text = self.log_text.get("end-80l", "end")
        tips = []
        if re.search(r"Failed to connect|No serial data received|Could not open port|Resource busy|"
                     r"could not open port|Errno 16|Errno 2\b", text):
            tips += ["關閉其他正在連接裝置的程式（官方安裝網頁、序列埠監看工具、Arduino 等）。",
                     "拔掉 USB-C 線重新插好，確認裝置有開機；換一條可傳資料的線或電腦上另一個 USB 孔，避免用集線器。",
                     "按「重新整理」重新選序列埠。"]
        if re.search(r"Timed out|timeout|Invalid head of packet|corrupt|MD5 of file does not match|Hash of data", text, re.I):
            tips += ["把「速度」改成 115200 再試一次。", "換一條線或 USB 孔，避免用集線器。"]
        if "Wrong --chip" in text or "This chip is" in text:
            tips += ["接的不是 AI Passport（ESP32-C3），請確認裝置。"]
        if "idf.py" in text and ("not found" in text or "No such file" in text):
            tips += ["請確認 ESP-IDF 路徑，或按「安裝 ESP-IDF」。"]
        return "\n".join(f"• {t}" for t in dict.fromkeys(tips))

    def on_close(self) -> None:
        if self.job:
            if not messagebox.askyesno(core.APP_NAME, "還有工作在進行（可能正在寫入裝置），確定要關閉嗎？"):
                return
            self.job.stop()
        self.cfg["baud"] = self.baud_var.get()
        self.cfg["tab"] = self.nb.index(self.nb.select())
        self.cfg["idf_path"] = self.idf_var.get().strip()
        self.cfg["project_mode"] = self.proj_mode.get()
        self.cfg["last_bin"] = self.bin_var.get().strip()
        self.cfg["release_repo"] = self.rel_repo_var.get().strip()
        try:
            core.save_config(self.cfg)
        except OSError:
            pass
        self.destroy()

    # ------------------------------------------------------------------ 裝置
    def refresh_ports(self, quiet: bool = False) -> None:
        ports = core.list_ports()
        cur = self.selected_port()
        if [p.device for p in ports] == [p.device for p in self.ports] and quiet:
            return
        self.ports = ports
        self.port_combo.configure(values=[p.label for p in ports])
        pick = None
        devices = [p.device for p in ports]
        for want in (cur, self.cfg.get("last_port")):
            if want in devices:
                pick = devices.index(want)
                break
        if pick is None:
            pick = next((i for i, p in enumerate(ports) if p.is_passport), 0 if ports else None)
        if pick is not None:
            self.port_combo.current(pick)
        else:
            self.port_var.set("")
        if not ports:
            self.device_var.set("找不到裝置：請接上 USB-C、開機；仍沒有就換一條可傳資料的線。")
        elif any(p.is_passport for p in ports):
            self.device_var.set("✅ 偵測到 AI Passport。")
        else:
            self.device_var.set("有序列埠，但不像 AI Passport（不是 Espressif USB JTAG/serial）。")

    def _auto_refresh_ports(self) -> None:
        if not self.job:
            self.refresh_ports(quiet=True)
        self.after(2500, self._auto_refresh_ports)

    def selected_port(self) -> str | None:
        i = self.port_combo.current()
        if 0 <= i < len(self.ports):
            return self.ports[i].device
        return None

    def require_port(self) -> str | None:
        self.refresh_ports(quiet=True)
        port = self.selected_port()
        if not port:
            messagebox.showwarning(core.APP_NAME, "還沒有選序列埠。\n\n請用可傳資料的 USB-C 線接上 AI Passport 並開機，"
                                                  "然後按「重新整理」。")
            return None
        info = next((p for p in self.ports if p.device == port), None)
        if info and not info.is_passport:
            if not messagebox.askyesno(core.APP_NAME, f"{port} 看起來不是 AI Passport（{info.description}）。\n"
                                                      "仍要繼續嗎？（寫入前 esptool 會再確認晶片是 ESP32-C3）"):
                return None
        self.cfg["last_port"] = port
        return port

    def baud(self) -> int:
        try:
            return int(self.baud_var.get())
        except ValueError:
            return 460800

    def check_device(self) -> None:
        port = self.require_port()
        if not port:
            return

        def work(job: Job):
            job.progress(None)
            job.run_cmd(core.esptool_cmd(core.chip_id_args(port)), label="讀取裝置")
            return job.found_chip, job.mac

        def done(res):
            chip, mac = res
            if chip and chip.startswith("ESP32-C3"):
                self.device_var.set(f"✅ 已連線：{chip}（MAC {mac or '?'}）— 可以上傳。")
            else:
                self.device_var.set(f"⚠️ 偵測到 {chip or '未知晶片'}，不是 AI Passport 的 ESP32-C3。")

        self.run_job("檢查裝置", work, done)

    # ------------------------------------------------------------------ 燒錄（共用）
    def confirm_and_flash(self, plan: core.FlashPlan, port: str, what: str,
                          source: str = "", version: str = "") -> None:
        msg = (f"要把「{what}」寫入 {port} 嗎？\n\n{plan.summary()}\n\n"
               "寫入時請不要拔線或讓電腦睡眠；中途停止會讓裝置暫時無法開機（重新寫入即可恢復）。")
        if not messagebox.askokcancel(core.APP_NAME, msg, icon="warning" if plan.resets_settings else "question"):
            self.log("已取消寫入。\n")
            return
        baud = self.baud()

        def record(job: Job, result: str, error: str = "") -> None:
            try:
                core.add_history({"name": what, "source": source, "version": version, "port": port,
                                  "mac": job.mac or "", "chip": job.found_chip or "", "result": result,
                                  "error": error, "plan": core.plan_to_dict(plan)})
            except OSError:
                pass
            self.q.put(("call", self.reload_history))

        def work(job: Job):
            job.status("連接裝置中…")
            job.progress(None)
            try:
                job.run_cmd(core.esptool_cmd(core.write_flash_args(port, baud, plan)), label="寫入")
            except Cancelled:
                record(job, "已停止")
                raise
            except Exception as e:  # noqa: BLE001
                record(job, "失敗", str(e))
                raise
            record(job, "成功")
            job.progress(100)

        def done(_):
            self.status_var.set(f"✅ 完成：{what}")
            messagebox.showinfo(core.APP_NAME, f"✅ 已寫入「{what}」並校驗成功，裝置會自動重新開機。\n\n"
                                               "如果畫面沒有變化，拔下 USB 線後重新開機即可。")

        self.run_job(f"寫入：{what}", work, done)

    # ------------------------------------------------------------------ 官方社區
    def search_plays(self, more: bool = False) -> None:
        cat = dict((n, k) for k, n in core.COMMUNITY_CATEGORIES).get(self.cat_var.get(), "")
        sort = dict((n, k) for k, n in core.COMMUNITY_SORTS).get(self.sort_var.get(), "downloads")
        q = self.q_var.get().strip()
        offset = self.play_offset if more else 0
        if q and not more:
            sort = "relevance"
        self.count_var.set("讀取中…")
        self.more_btn.configure(state="disabled")

        def fetch():
            try:
                res = core.fetch_plays(q=q, category=cat, sort=sort, offset=offset, limit=40)
                self.q.put(("call", lambda: self._show_plays(res, more)))
            except FlashError as e:
                msg = str(e)
                self.q.put(("call", lambda: (self.count_var.set(msg), self.more_btn.configure(state="normal"))))

        threading.Thread(target=fetch, daemon=True).start()

    def _show_plays(self, res, more: bool) -> None:
        plays, total, has_more = res
        if not more:
            self.plays = []
            self.tree.delete(*self.tree.get_children())
        for p in plays:
            idx = len(self.plays)
            self.plays.append(p)
            name = ("★ " if p.official else "") + p.title
            if not p.fw_available:
                name += "（無韌體）"
            self.tree.insert("", "end", iid=str(idx), values=(
                name, p.author, core.CATEGORY_NAMES.get(p.category, p.category),
                f"{p.downloads:,}", core.human_size(p.fw_size) if p.fw_size else "-"))
        self.play_offset = len(self.plays)
        self.count_var.set(f"顯示 {len(self.plays)} / {total} 款（★ 官方）")
        self.more_btn.configure(state="normal" if has_more else "disabled")
        if not more and self.plays:
            self.tree.selection_set("0")
            self.tree.see("0")

    def current_play(self) -> core.Play | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return self.plays[int(sel[0])]

    def show_play(self) -> None:
        p = self.current_play()
        if not p:
            return
        t = self.play_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("end", p.title + "\n", "h")
        meta = f"{'官方' if p.official else '社區'} · {p.author} · {core.CATEGORY_NAMES.get(p.category, p.category)} · " \
               f"下載 {p.downloads:,} · ♥ {p.hearts} · 更新 {p.updated}"
        if p.fw_size:
            meta += f" · {core.human_size(p.fw_size)}"
        t.insert("end", meta + "\n\n", "sub")
        if p.description:
            t.insert("end", p.description.strip() + "\n\n")
        if p.instructions:
            t.insert("end", "使用方式\n", "b")
            t.insert("end", p.instructions.strip() + "\n\n")
        if p.changelog:
            t.insert("end", "本次更新\n", "b")
            t.insert("end", p.changelog.strip() + "\n\n")
        if p.github_url:
            t.insert("end", "原始碼：" + p.github_url + "\n", "sub")
        t.insert("end", "\n注意：社區玩法安裝會覆蓋原本的玩法及配網設定（官方說明）。\n", "sub")
        t.configure(state="disabled")
        t.yview_moveto(0)
        self.src_btn.configure(state="normal" if p.github_url else "disabled")
        self.load_thumbnail(p)

    def load_thumbnail(self, p: core.Play) -> None:
        if not (Image and p.thumbnail_url):
            self.thumb_label.configure(image="")
            return
        if p.slug in self.thumb_cache:
            self.thumb_label.configure(image=self.thumb_cache[p.slug])
            return
        self.thumb_label.configure(image="")

        def fetch():
            try:
                import io
                with core.http_get(p.thumbnail_url, timeout=15) as r:
                    data = r.read()
                img = Image.open(io.BytesIO(data))
                img.thumbnail((360, 160))

                def show():
                    photo = ImageTk.PhotoImage(img)
                    self.thumb_cache[p.slug] = photo
                    cur = self.current_play()
                    if cur and cur.slug == p.slug:
                        self.thumb_label.configure(image=photo)
                self.q.put(("call", show))
            except Exception:  # noqa: BLE001 — 縮圖失敗不影響功能
                pass

        threading.Thread(target=fetch, daemon=True).start()

    def open_play_page(self) -> None:
        p = self.current_play()
        webbrowser.open(p.page_url if p else core.COMMUNITY_BASE + "/plays/")

    def play_to_project(self) -> None:
        p = self.current_play()
        if not p or not p.github_url:
            return
        try:
            owner, repo = core.parse_github_repo(p.github_url)
        except FlashError as e:
            self.show_error(str(e))
            return
        self._add_project({"name": p.title, "url": f"https://github.com/{owner}/{repo}", "ref": "",
                           "note": f"來自官方社區：{p.title}（{p.author}）"})
        self.nb.select(1)

    def flash_play(self) -> None:
        p = self.current_play()
        if not p:
            messagebox.showinfo(core.APP_NAME, "請先在清單中選一個玩法。")
            return
        if not p.fw_available:
            messagebox.showinfo(core.APP_NAME, "這個玩法目前沒有可下載的韌體。")
            return
        port = self.require_port()
        if not port:
            return

        def work(job: Job):
            job.status(f"下載「{p.title}」韌體…")

            def prog(got, total):
                if total:
                    job.progress(got * 100 / total)
                    job.status(f"下載中 {core.human_size(got)} / {core.human_size(total)}")
            path = core.download_play(p, prog, job.cancel.is_set)
            job.log(f"韌體：{path}\nSHA-256 校驗通過：{p.fw_sha256}\n")
            return core.plan_single_bin(path, "full")

        ver = f"{p.updated} 版 · SHA {p.fw_sha256[:10]}"
        self.run_job(f"準備「{p.title}」", work,
                     lambda plan: self.confirm_and_flash(plan, port, p.title, "官方社區", ver))

    # ------------------------------------------------------------------ 開源專案
    def all_projects(self) -> list[dict]:
        hidden = set(self.cfg.get("hidden_builtin", []))
        return [dict(p) for p in core.BUILTIN_PROJECTS if p["url"] not in hidden] + list(self.cfg.get("projects", []))

    def reload_projects(self, select_key: str | None = None) -> None:
        self.projects = self.all_projects()
        self.proj_list.delete(0, "end")
        for p in self.projects:
            self.proj_list.insert("end", ("★ " if p.get("builtin") else "   ") + p["name"])
        key = select_key or self.cfg.get("selected_project")
        idx = next((i for i, p in enumerate(self.projects) if self._proj_key(p) == key), 0)
        if self.projects:
            self.proj_list.selection_clear(0, "end")
            self.proj_list.selection_set(idx)
            self.proj_list.see(idx)
            self.show_project()

    @staticmethod
    def _proj_key(p: dict) -> str:
        return p.get("url") or p.get("path", "")

    def current_project(self) -> dict | None:
        sel = self.proj_list.curselection()
        return self.projects[sel[0]] if sel else None

    def project_path(self, p: dict) -> Path:
        if p.get("url"):
            owner, repo = core.parse_github_repo(p["url"])
            return core.project_dir_for(owner, repo)
        return Path(p["path"])

    def show_project(self) -> None:
        p = self.current_project()
        if not p:
            return
        self.cfg["selected_project"] = self._proj_key(p)
        self.proj_name_var.set(p["name"])
        path = self.project_path(p)
        lines = []
        if p.get("note"):
            lines.append(p["note"])
        if p.get("url"):
            lines.append(f"來源：{p['url']}")
        lines.append(f"位置：{path}")
        if (path / "CMakeLists.txt").exists():
            ver = (path / "version.txt").read_text().strip() if (path / "version.txt").exists() else ""
            head = ""
            try:
                head = subprocess.run(["git", "-C", str(path), "log", "-1", "--format=%h %cs %s"], capture_output=True,
                                      text=True, timeout=5, env=core.tool_env()).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
            lines.append("狀態：已下載" + (f"，版本 {ver}" if ver else "") + (f"\n最新提交：{head}" if head else ""))
            if (path / "build" / "flasher_args.json").exists():
                lines.append("已編譯，可以直接「③ 上傳」。")
        else:
            lines.append("狀態：尚未下載" if p.get("url") else "狀態：⚠️ 找不到這個資料夾")
        self.proj_info_var.set("\n".join(lines))
        self.ref_var.set(p.get("ref", ""))

    def _add_project(self, proj: dict) -> None:
        projs = self.cfg.setdefault("projects", [])
        key = self._proj_key(proj)
        if key in (self._proj_key(p) for p in self.all_projects()):
            self.cfg.setdefault("hidden_builtin", [])
            if key in self.cfg["hidden_builtin"]:
                self.cfg["hidden_builtin"].remove(key)
        else:
            projs.append(proj)
        core.save_config(self.cfg)
        self.reload_projects(key)

    def add_github_project(self) -> None:
        url = _ask_string(self, "新增 GitHub 專案", "GitHub 網址（例如 https://github.com/owner/repo）：")
        if not url:
            return
        try:
            owner, repo = core.parse_github_repo(url)
        except FlashError as e:
            self.show_error(str(e))
            return
        name = _ask_string(self, "新增 GitHub 專案", "顯示名稱：", initial=repo) or repo
        self._add_project({"name": name, "url": f"https://github.com/{owner}/{repo}", "ref": "", "note": ""})

    def add_local_project(self) -> None:
        d = filedialog.askdirectory(title="選擇 ESP-IDF 專案資料夾（有 CMakeLists.txt 的那層）")
        if not d:
            return
        path = Path(d)
        if not core.is_idf_project(path):
            if not messagebox.askyesno(core.APP_NAME, "這個資料夾看起來不是 ESP-IDF 專案（沒有 CMakeLists.txt），仍要加入嗎？"):
                return
        self._add_project({"name": path.name, "url": "", "path": str(path), "ref": "", "note": "本機資料夾"})

    def remove_project(self) -> None:
        p = self.current_project()
        if not p:
            return
        if not messagebox.askyesno(core.APP_NAME, f"從清單移除「{p['name']}」？\n（不會刪除電腦上已下載的檔案）"):
            return
        key = self._proj_key(p)
        if p.get("builtin"):
            self.cfg.setdefault("hidden_builtin", []).append(key)
        else:
            self.cfg["projects"] = [x for x in self.cfg.get("projects", []) if self._proj_key(x) != key]
        core.save_config(self.cfg)
        self.reload_projects()

    def _save_project_ref(self, p: dict) -> None:
        ref = self.ref_var.get().strip()
        p["ref"] = ref
        for x in self.cfg.get("projects", []):
            if self._proj_key(x) == self._proj_key(p):
                x["ref"] = ref
        refs = self.cfg.setdefault("builtin_refs", {})
        if p.get("builtin"):
            refs[p["url"]] = ref

    def open_project_folder(self) -> None:
        p = self.current_project()
        if p:
            path = self.project_path(p)
            path = path if path.exists() else path.parent
            path.mkdir(parents=True, exist_ok=True)
            open_in_file_manager(path)

    def open_project_github(self) -> None:
        p = self.current_project()
        if p and p.get("url"):
            webbrowser.open(p["url"])

    # ---- ESP-IDF
    def refresh_idf_candidates(self) -> None:
        cands = [str(c) for c in core.find_idf_candidates()]
        self.idf_combo.configure(values=cands)
        if not self.idf_var.get() and cands:
            self.idf_var.set(cands[0])
        self.update_idf_label()

    def update_idf_label(self) -> None:
        idf = self.idf_path()
        if idf:
            self.idf_info_var.set(f"✓ ESP-IDF {core.idf_version(idf)}")
        elif self.idf_var.get().strip():
            self.idf_info_var.set("⚠️ 這個資料夾裡沒有 export.sh，不是 ESP-IDF。")
        else:
            self.idf_info_var.set(f"尚未安裝 ESP-IDF：按「安裝 {core.DEFAULT_IDF_VERSION}」，或選擇已安裝的資料夾。")

    def idf_path(self) -> Path | None:
        v = self.idf_var.get().strip()
        if v and (Path(v).expanduser() / "export.sh").exists():
            return Path(v).expanduser()
        return None

    def browse_idf(self) -> None:
        d = filedialog.askdirectory(title="選擇 ESP-IDF 資料夾（裡面有 export.sh）",
                                    initialdir=str(Path.home() / "esp"))
        if d:
            self.idf_var.set(d)
            self.update_idf_label()

    def install_idf(self) -> None:
        dest, cmd = core.idf_install_command()
        if not messagebox.askokcancel(core.APP_NAME,
                                      f"將安裝 ESP-IDF {core.DEFAULT_IDF_VERSION} 到\n{dest}\n\n"
                                      "需要 git（xcode-select --install）與 Homebrew 的 cmake、ninja（沒有會自動用 brew 安裝）。\n"
                                      "下載約 1–2 GB，可能需要 10–30 分鐘。要開始嗎？"):
            return

        def work(job: Job):
            job.progress(None)
            job.run_cmd(cmd, label="安裝 ESP-IDF")
            return dest

        def done(d):
            self.idf_var.set(str(d))
            self.refresh_idf_candidates()
            messagebox.showinfo(core.APP_NAME, "ESP-IDF 安裝完成，可以編譯了。")

        self.run_job("安裝 ESP-IDF", work, done)

    # ---- 下載／編譯／上傳步驟（在背景執行）
    def _step_sync(self, job: Job, p: dict) -> Path:
        path = self.project_path(p)
        if not p.get("url"):
            if not path.exists():
                raise FlashError(f"找不到資料夾 {path}")
            job.log("本機資料夾專案，略過下載。\n")
            return path
        owner, repo = core.parse_github_repo(p["url"])
        job.status(f"下載 {owner}/{repo} …")
        job.progress(None)
        for cmd in core.git_sync_commands(core.github_clone_url(owner, repo), path, p.get("ref", "")):
            job.run_cmd(cmd, label="下載原始碼")
        return path

    def _step_build(self, job: Job, path: Path, idf: Path) -> Path:
        if not core.is_idf_project(path):
            raise FlashError(f"{path} 不是 ESP-IDF 專案（找不到含 project.cmake 的 CMakeLists.txt），無法編譯。")
        job.status("編譯中（第一次會比較久）…")
        job.progress(None)
        job.run_cmd(core.idf_build_command(idf, path), cwd=path, label="編譯")
        return path / "build"

    def _step_plan(self, job: Job, build_dir: Path, mode: str, idf: Path | None) -> core.FlashPlan:
        if mode == "segments":
            return core.plan_from_idf_build(build_dir)
        merged = core.find_merged_image(build_dir)
        if not merged:
            merged = build_dir / "passport-merged-full.bin"
            job.status("合成完整映像…")
            job.run_cmd(core.esptool_cmd(core.merge_bin_args(build_dir, merged)), label="合成完整映像")
        return core.plan_single_bin(merged, "full")

    def _project_run(self, sync: bool, build: bool, flash: bool) -> None:
        p = self.current_project()
        if not p:
            messagebox.showinfo(core.APP_NAME, "請先選一個專案。")
            return
        self._save_project_ref(p)
        idf = self.idf_path()
        if build and IS_WIN:
            messagebox.showinfo(core.APP_NAME, "Windows 版的編譯功能尚未支援（之後會加入）。\n"
                                               "目前可以先用「官方社區玩法」或「韌體檔」分頁直接上傳。")
            return
        if build and not idf:
            messagebox.showwarning(core.APP_NAME, "編譯需要 ESP-IDF。\n\n請按「安裝 ESP-IDF」，或選擇已安裝的 ESP-IDF 資料夾"
                                                  "（例如 ~/esp/esp-idf-v5.5.3）。")
            return
        port = None
        if flash:
            port = self.require_port()
            if not port:
                return
        mode = self.proj_mode.get()
        self.cfg["idf_path"] = str(idf) if idf else self.cfg.get("idf_path", "")

        def work(job: Job):
            path = self._step_sync(job, p) if sync else self.project_path(p)
            build_dir = path / "build"
            if build:
                build_dir = self._step_build(job, path, idf)
            if flash:
                if not (build_dir / "flasher_args.json").exists():
                    raise FlashError("找不到編譯結果，請先按「② 編譯」。")
                return self._step_plan(job, build_dir, mode, idf), core.project_version(path)
            return None

        def done(res):
            self.show_project()
            if res:
                plan, ver = res
                self.confirm_and_flash(plan, port, p["name"], "開源專案", ver)
            elif build:
                messagebox.showinfo(core.APP_NAME, "編譯完成，可以按「③ 上傳」。")

        names = [n for n, on in (("下載", sync), ("編譯", build), ("上傳", flash)) if on]
        self.run_job(f"{p['name']}：{' → '.join(names)}", work, done)

    def project_all(self) -> None:
        self._project_run(True, True, True)

    def project_sync(self) -> None:
        self._project_run(True, False, False)

    def project_build(self) -> None:
        self._project_run(False, True, False)

    def project_flash(self) -> None:
        self._project_run(False, False, True)

    # ------------------------------------------------------------------ 韌體檔
    def browse_bin(self) -> None:
        f = filedialog.askopenfilename(title="選擇韌體 .bin", filetypes=[("韌體", "*.bin"), ("所有檔案", "*")])
        if f:
            self.bin_var.set(f)

    def _bin_offset(self) -> int | None:
        try:
            return int(self.offset_var.get().strip(), 0)
        except ValueError:
            return None

    def inspect_bin(self) -> None:
        f = self.bin_var.get().strip()
        if not f:
            self.bin_info_var.set("可選擇社區下載的 FoloToy-AI-Passport-full.bin、自己編譯的韌體等。")
            return
        path = Path(f).expanduser()
        if not path.is_file():
            self.bin_info_var.set("⚠️ 找不到這個檔案。")
            return
        try:
            info = core.inspect_image(path)
            txt = f"{info.kind_label} · {core.human_size(info.size)}"
            if info.chip_id is not None:
                txt += " · ESP32-C3 ✓" if info.chip_id == core.CHIP_IMAGE_ID else f" · ⚠️ 非 ESP32-C3（chip id {info.chip_id}）"
            if info.partitions:
                txt += "\n分區：" + "、".join(f"{p.name}@0x{p.offset:X}" for p in info.partitions)
            try:
                plan = core.plan_single_bin(path, self.bin_mode.get(), self._bin_offset())
                txt += f"\n→ {plan.title}" + ("（會清除裝置設定）" if plan.resets_settings else "")
            except FlashError as e:
                txt += f"\n⚠️ {e}"
            self.bin_info_var.set(txt)
        except OSError as e:
            self.bin_info_var.set(f"⚠️ 讀不到檔案：{e}")

    def flash_bin(self) -> None:
        f = self.bin_var.get().strip()
        if not f:
            messagebox.showinfo(core.APP_NAME, "請先選擇韌體檔。")
            return
        try:
            plan = core.plan_single_bin(Path(f).expanduser(), self.bin_mode.get(), self._bin_offset())
        except (FlashError, OSError) as e:
            self.show_error(str(e))
            return
        port = self.require_port()
        if port:
            ver = f"SHA {core.sha256_file(plan.items[0].path)[:10]}"
            self.confirm_and_flash(plan, port, Path(f).name, "韌體檔", ver)

    def load_releases(self) -> None:
        text = self.rel_repo_var.get().strip()
        try:
            owner, repo = core.parse_github_repo(text)
        except FlashError as e:
            self.show_error(str(e))
            return

        def fetch():
            try:
                assets = core.fetch_release_bins(owner, repo)
                self.q.put(("call", lambda: self._show_releases(owner, repo, assets)))
            except FlashError as e:
                msg = str(e)
                self.q.put(("call", lambda: self.show_error(msg)))

        self.status_var.set(f"讀取 {owner}/{repo} 的 Releases…")
        threading.Thread(target=fetch, daemon=True).start()

    def _show_releases(self, owner, repo, assets) -> None:
        self.release_repo = (owner, repo)
        self.release_assets = assets
        self.rel_combo.configure(values=[a.label for a in assets])
        if assets:
            self.rel_combo.current(0)
            self.status_var.set(f"找到 {len(assets)} 個 .bin")
        else:
            self.rel_var.set("")
            self.status_var.set("這個 repo 的 Releases 沒有 .bin 檔")
            messagebox.showinfo(core.APP_NAME, f"{owner}/{repo} 的 Releases 裡沒有 .bin 檔。\n"
                                               "如果它只有原始碼，請到「開源專案」分頁新增並編譯。")

    def download_release(self) -> None:
        i = self.rel_combo.current()
        if i < 0 or i >= len(self.release_assets):
            messagebox.showinfo(core.APP_NAME, "請先按「讀取 Releases」並選一個檔案。")
            return
        a = self.release_assets[i]
        owner, repo = self.release_repo
        dest = core.release_cache_path(owner, repo, a)

        def work(job: Job):
            def prog(got, total):
                if total:
                    job.progress(got * 100 / total)
                    job.status(f"下載中 {core.human_size(got)} / {core.human_size(total)}")
            path = core.download_file(a.url, dest, None, a.size or None, prog, job.cancel.is_set)
            job.log(f"已下載：{path}\nSHA-256：{core.sha256_file(path)}\n")
            return path

        def done(path):
            self.bin_var.set(str(path))
            self.bin_mode.set("auto")
            self.inspect_bin()

        self.run_job(f"下載 {a.name}", work, done)


def _esptool_version() -> str:
    try:
        from importlib.metadata import version
        return version("esptool")
    except Exception:  # noqa: BLE001
        return "?"


def _render_markdown(txt: tk.Text, md: str) -> None:
    """很簡單的 Markdown 顯示：標題、粗體、清單。"""
    base = UI_FONT
    txt.tag_configure("h1", font=(base[0], base[1] + 6, "bold"), spacing3=6)
    txt.tag_configure("h2", font=(base[0], base[1] + 3, "bold"), spacing1=10, spacing3=4)
    txt.tag_configure("h3", font=(base[0], base[1] + 1, "bold"), spacing1=6)
    txt.tag_configure("b", font=(base[0], base[1], "bold"))
    txt.tag_configure("li", lmargin1=12, lmargin2=26)
    lines: list[str] = []
    for line in md.splitlines():  # 縮排的接續行併回上一行（清單項目換行）
        if lines and line.startswith("  ") and line.strip() and lines[-1].strip():
            lines[-1] += line.strip()
        else:
            lines.append(line)
    for line in lines:
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            txt.insert("end", m.group(2) + "\n", f"h{len(m.group(1))}")
            continue
        tag = ()
        if re.match(r"^\s*[-*]\s+", line):
            line = re.sub(r"^\s*[-*]\s+", "• ", line)
            tag = ("li",)
        for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", line)):
            txt.insert("end", part.replace("`", ""), tag + (("b",) if i % 2 else ()))
        txt.insert("end", "\n")


def _ask_string(parent, title: str, prompt: str, initial: str = "") -> str | None:
    from tkinter import simpledialog
    v = simpledialog.askstring(title, prompt, initialvalue=initial, parent=parent)
    return v.strip() if v else None


def main() -> None:
    app = App()
    # 內建專案的分支／標籤設定
    for p in app.projects:
        if p.get("builtin"):
            p["ref"] = app.cfg.get("builtin_refs", {}).get(p["url"], "")
    app.show_project()
    app.mainloop()


if __name__ == "__main__":
    main()
