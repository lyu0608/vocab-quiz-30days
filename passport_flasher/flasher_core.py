"""AI Passport 燒錄工具的核心邏輯（不含畫面，方便測試）。

FoloToy AI Passport：ESP32-C3、8 MB Flash，USB-C 走晶片內建的 USB Serial/JTAG
（USB VID 0x303A / PID 0x1001，macOS 上叫 /dev/cu.usbmodemXXXX）。

三種來源：
  1. 官方社區玩法  https://ai-passport.folotoy.cn/plays/  → 下載 merged 韌體 → 寫到 0x0
  2. 開源專案      GitHub → git clone → ESP-IDF 編譯 → 依 build/flasher_args.json 分段寫入
  3. 韌體檔        本機 .bin 或 GitHub Release 的 .bin → 自動判斷寫入位址

安全原則：永遠不做 erase-flash；寫入前檢查韌體是 ESP32-C3 用的、大小放得下。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import ssl
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

APP_NAME = "AI Passport 燒錄工具"
APP_VERSION = "1.0.0"

# ---------------------------------------------------------------- 硬體常數
CHIP = "esp32c3"
CHIP_IMAGE_ID = 5                 # ESP 映像檔標頭裡 ESP32-C3 的 chip id
FLASH_BYTES = 8 * 1024 * 1024
BOOTLOADER_OFFSET = 0x0
PARTITION_TABLE_OFFSET = 0x8000
DEFAULT_APP_OFFSET = 0x10000      # 官方預設分區表 factory 分區
ESPRESSIF_VID = 0x303A
USB_JTAG_PID = 0x1001
IMAGE_MAGIC = 0xE9
PT_MAGIC = b"\xaa\x50"

DEFAULT_IDF_VERSION = "v5.5.3"    # FoloToy/ai-passport 與 hakkaataipassport 都用這版

# ---------------------------------------------------------------- 官方社區
COMMUNITY_BASE = "https://ai-passport.folotoy.cn"
COMMUNITY_SORTS = [
    ("downloads", "下載最多"),
    ("newest", "最新"),
    ("updated", "最近更新"),
    ("hearts", "點讚最多"),
    ("comments", "討論最多"),
]
COMMUNITY_CATEGORIES = [
    ("", "全部"),
    ("games", "遊戲"),
    ("learning", "學習"),
    ("productivity", "效率工具"),
    ("information", "資訊"),
    ("media", "影音"),
    ("social", "社交"),
    ("developer", "開發者"),
]
CATEGORY_NAMES = dict(COMMUNITY_CATEGORIES)

# ---------------------------------------------------------------- 內建開源專案
BUILTIN_PROJECTS = [
    {
        "name": "客語朗讀練習（我的專案）",
        "url": "https://github.com/lyu0608/hakkaataipassport",
        "ref": "",
        "note": "〈得人惱个木棉樹〉四縣腔朗讀、卡拉 OK 字幕；燒錄會一併寫入錄音（audio 分區）",
        "builtin": True,
    },
    {
        "name": "FoloToy 官方示範韌體",
        "url": "https://github.com/FoloToy/ai-passport",
        "ref": "",
        "note": "FoloToy/ai-passport 原廠開源專案",
        "builtin": True,
    },
]

USER_AGENT = f"PassportFlasher/{APP_VERSION}"


class FlashError(Exception):
    """給使用者看的錯誤（訊息是中文、可直接顯示）。"""


# ================================================================ 路徑與設定
def app_support_dir() -> Path:
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "PassportFlasher"
    else:
        d = Path.home() / ".passport_flasher"
    d.mkdir(parents=True, exist_ok=True)
    return d


def workspace_dir() -> Path:
    """下載的專案與韌體放這裡（使用者看得到，方便自己打開）。"""
    d = Path.home() / "PassportFlasher"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_config() -> dict:
    p = app_support_dir() / "config.json"
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    p = app_support_dir() / "config.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(p)


def tool_env() -> dict:
    """子程序用的環境變數。

    從 Finder 開啟的 app 的 PATH 只有 /usr/bin:/bin…，找不到 Homebrew 裝的
    cmake、ninja、git，所以補上常見路徑。
    """
    env = dict(os.environ)
    extra = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]
    parts = env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join([p for p in extra if p not in parts] + parts)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("LANG", "en_US.UTF-8")
    env["NO_COLOR"] = "1"
    return env


# ================================================================ 上傳紀錄
_history_lock = threading.Lock()
HISTORY_LIMIT = 500


def history_path() -> Path:
    return app_support_dir() / "history.json"


def load_history() -> list[dict]:
    try:
        data = json.loads(history_path().read_text("utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def add_history(entry: dict) -> dict:
    """新增一筆上傳紀錄（最新的在最前面）。可在背景執行緒呼叫。"""
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
    with _history_lock:
        hist = [entry] + load_history()
        p = history_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(hist[:HISTORY_LIMIT], ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(p)
    return entry


def clear_history() -> None:
    with _history_lock:
        history_path().unlink(missing_ok=True)


def plan_to_dict(plan: "FlashPlan") -> dict:
    return {"title": plan.title, "flash_args": plan.flash_args, "resets_settings": plan.resets_settings,
            "items": [{"offset": it.offset, "path": str(it.path)} for it in plan.items]}


def plan_from_dict(d: dict) -> "FlashPlan":
    items = [FlashItem(int(i["offset"]), Path(i["path"])) for i in d.get("items", [])]
    missing = [str(i.path) for i in items if not i.path.exists()]
    if not items:
        raise FlashError("這筆紀錄沒有可重新上傳的檔案。")
    if missing:
        raise FlashError("找不到當時的檔案，無法重新上傳：\n" + "\n".join(missing))
    for it in items:
        check_chip(inspect_image(it.path))
    _check_fits(items)
    return FlashPlan(d.get("title", "重新上傳"), items, list(d.get("flash_args", KEEP_FLASH_ARGS)),
                     bool(d.get("resets_settings", True)))


def bundled_file(name: str) -> Path:
    """跟程式放在一起的檔案（打包成 .app 後在 sys._MEIPASS）。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def read_changelog() -> str:
    try:
        return bundled_file("CHANGELOG.md").read_text("utf-8")
    except OSError:
        return f"{APP_NAME} {APP_VERSION}"


# ================================================================ 韌體映像檢查
@dataclass
class Partition:
    name: str
    type: int
    subtype: int
    offset: int
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size


def parse_partition_table(data: bytes) -> list[Partition]:
    """解析 ESP-IDF 二進位分區表（每筆 32 bytes，以 0xAA50 開頭）。"""
    parts = []
    for i in range(0, min(len(data), 0xC00), 32):
        e = data[i:i + 32]
        if len(e) < 32 or e[:2] != PT_MAGIC:
            break
        ptype, sub = e[2], e[3]
        off, size = struct.unpack("<II", e[4:12])
        name = e[12:28].split(b"\0", 1)[0].decode("ascii", "replace")
        parts.append(Partition(name, ptype, sub, off, size))
    return parts


def is_nvs(p: Partition) -> bool:
    return p.type == 1 and p.subtype == 2


@dataclass
class ImageInfo:
    path: Path
    size: int
    kind: str                     # merged / app / partition-table / unknown
    chip_id: int | None = None
    partitions: list[Partition] = field(default_factory=list)

    @property
    def kind_label(self) -> str:
        return {
            "merged": "完整映像（含 bootloader＋分區表，寫到 0x0）",
            "app": "應用程式（app only）",
            "partition-table": "分區表",
            "unknown": "無法辨識（非 ESP 映像檔）",
        }[self.kind]


def _chip_id_at(data: bytes, off: int) -> int | None:
    if len(data) >= off + 24 and data[off] == IMAGE_MAGIC:
        return struct.unpack("<H", data[off + 12:off + 14])[0]
    return None


def inspect_image(path: str | Path) -> ImageInfo:
    path = Path(path)
    data = path.read_bytes()
    size = len(data)
    if size >= PARTITION_TABLE_OFFSET + 32 and data[PARTITION_TABLE_OFFSET:PARTITION_TABLE_OFFSET + 2] == PT_MAGIC \
            and data[0] == IMAGE_MAGIC:
        parts = parse_partition_table(data[PARTITION_TABLE_OFFSET:PARTITION_TABLE_OFFSET + 0xC00])
        return ImageInfo(path, size, "merged", _chip_id_at(data, 0), parts)
    if data[:2] == PT_MAGIC:
        return ImageInfo(path, size, "partition-table", None, parse_partition_table(data))
    if data[:1] == bytes([IMAGE_MAGIC]):
        return ImageInfo(path, size, "app", _chip_id_at(data, 0))
    return ImageInfo(path, size, "unknown")


def check_chip(info: ImageInfo) -> None:
    if info.chip_id is not None and info.chip_id != CHIP_IMAGE_ID:
        raise FlashError(
            f"{info.path.name} 是給其他晶片的韌體（chip id {info.chip_id}），"
            "AI Passport 是 ESP32-C3（chip id 5），已停止。")


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ================================================================ 燒錄計畫
@dataclass
class FlashItem:
    offset: int
    path: Path

    @property
    def size(self) -> int:
        return self.path.stat().st_size


@dataclass
class FlashPlan:
    title: str
    items: list[FlashItem]
    flash_args: list[str]                     # --flash-mode 等（esptool 5 寫法）
    resets_settings: bool                     # 會不會清掉 NVS（Wi-Fi、配網設定）
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [self.title]
        for it in self.items:
            lines.append(f"  0x{it.offset:06X}  {it.path.name}  ({human_size(it.size)})")
        if self.resets_settings:
            lines.append("⚠️ 會清除裝置上的設定（Wi-Fi／配網／個人資料），和官方社區安裝相同。")
        else:
            lines.append("✓ 保留裝置上的設定（NVS 分區不會被覆寫）。")
        lines += self.notes
        return "\n".join(lines)


def human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _check_fits(items: Iterable[FlashItem]) -> None:
    spans = []
    for it in items:
        end = it.offset + it.size
        if end > FLASH_BYTES:
            raise FlashError(f"{it.path.name} 寫到 0x{it.offset:X} 會超出 8 MB Flash（結尾 0x{end:X}）。")
        spans.append((it.offset, end, it.path.name))
    spans.sort()
    for (a0, a1, an), (b0, _b1, bn) in zip(spans, spans[1:]):
        if b0 < a1:
            raise FlashError(f"{an} 和 {bn} 寫入範圍重疊（0x{b0:X}），請確認位址。")


def _overlaps(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 < b1 and b0 < a1


KEEP_FLASH_ARGS = ["--flash-mode", "keep", "--flash-freq", "keep", "--flash-size", "keep"]


def plan_single_bin(path: str | Path, mode: str = "auto", custom_offset: int | None = None) -> FlashPlan:
    """一個 .bin 檔的燒錄計畫。mode: auto / full / app / custom。"""
    info = inspect_image(path)
    if info.kind == "unknown" and mode == "auto":
        raise FlashError(f"{info.path.name} 不是 ESP32 映像檔，無法自動判斷位址；確定沒錯的話請選「自訂位址」。")
    check_chip(info)

    if mode == "auto":
        mode = "full" if info.kind == "merged" else "app" if info.kind == "app" else "custom"
        if mode == "custom":
            raise FlashError(f"{info.path.name} 是{info.kind_label}，請選「自訂位址」並填寫位址。")

    if mode == "full":
        if info.kind not in ("merged",):
            raise FlashError(f"{info.path.name} 不含 bootloader 與分區表（{info.kind_label}），"
                             "寫到 0x0 會讓裝置無法開機。請改選「應用程式 0x10000」。")
        offset = 0
        title = "完整寫入（同官方社區安裝方式）"
    elif mode == "app":
        if info.kind == "merged":
            raise FlashError(f"{info.path.name} 是完整映像，必須寫到 0x0，請選「完整映像」。")
        offset = DEFAULT_APP_OFFSET
        title = "寫入應用程式到 0x10000（官方預設分區表的 factory 分區）"
    elif mode == "custom":
        if custom_offset is None:
            raise FlashError("請填寫寫入位址，例如 0x0 或 0x10000。")
        if custom_offset % 0x1000:
            raise FlashError(f"位址 0x{custom_offset:X} 沒有對齊 4 KB（0x1000）。")
        if info.kind == "merged" and custom_offset != 0:
            raise FlashError("完整映像只能寫到 0x0。")
        offset = custom_offset
        title = f"寫入到自訂位址 0x{offset:X}"
    else:
        raise ValueError(mode)

    item = FlashItem(offset, info.path)
    _check_fits([item])
    resets = _overlaps(offset, offset + info.size, 0x9000, 0xF000)
    notes = []
    if info.kind == "merged" and info.partitions:
        app = [p for p in info.partitions if p.type == 0]
        if not app:
            raise FlashError("這個完整映像的分區表裡沒有應用程式分區，看起來不是可開機的韌體。")
        nvs = [p for p in info.partitions if is_nvs(p)]
        resets = any(_overlaps(0, info.size, p.offset, p.end) for p in nvs) if nvs else resets
    if mode == "app":
        notes.append("提示：只寫 app 時，裝置上必須已經是官方預設分區表（factory 在 0x10000）。")
    return FlashPlan(title, [item], list(KEEP_FLASH_ARGS), resets, notes)


def read_flasher_args(build_dir: str | Path) -> dict:
    p = Path(build_dir) / "flasher_args.json"
    if not p.exists():
        raise FlashError(f"找不到 {p}，請先編譯專案。")
    return json.loads(p.read_text("utf-8"))


def plan_from_idf_build(build_dir: str | Path) -> FlashPlan:
    """依 ESP-IDF 編譯產生的 flasher_args.json 分段寫入（等同 idf.py flash）。"""
    build_dir = Path(build_dir)
    fa = read_flasher_args(build_dir)
    chip = (fa.get("extra_esptool_args") or {}).get("chip", CHIP)
    if chip != CHIP:
        raise FlashError(f"這個專案是編給 {chip} 的，AI Passport 是 esp32c3。"
                         "請在專案裡執行 idf.py set-target esp32c3 後重新編譯。")
    items = [FlashItem(int(off, 0), (build_dir / rel).resolve())
             for off, rel in fa.get("flash_files", {}).items()]
    if not items:
        raise FlashError("flasher_args.json 裡沒有要寫入的檔案。")
    for it in items:
        if not it.path.exists():
            raise FlashError(f"找不到 {it.path}，請重新編譯。")
        info = inspect_image(it.path)
        check_chip(info)
    items.sort(key=lambda i: i.offset)
    _check_fits(items)

    fs = fa.get("flash_settings") or {}
    args = []
    for key, opt in (("flash_mode", "--flash-mode"), ("flash_freq", "--flash-freq"), ("flash_size", "--flash-size")):
        if fs.get(key):
            args += [opt, str(fs[key])]

    parts: list[Partition] = []
    pt = build_dir / "partition_table" / "partition-table.bin"
    if pt.exists():
        parts = parse_partition_table(pt.read_bytes())
    nvs = [p for p in parts if is_nvs(p)] or [Partition("nvs", 1, 2, 0x9000, 0x6000)]
    resets = any(_overlaps(it.offset, it.offset + it.size, p.offset, p.end) for it in items for p in nvs)
    notes = []
    big = [p for p in parts if p.type == 1 and p.subtype not in (0, 1, 2) and
           any(it.offset == p.offset for it in items)]
    if big:
        notes.append("包含資料分區：" + "、".join(p.name for p in big) + "（例如錄音），第一次會比較久。")
    return FlashPlan("分段寫入（等同 idf.py flash）", items, args, resets, notes)


def find_merged_image(build_dir: str | Path) -> Path | None:
    build_dir = Path(build_dir)
    for pat in ("*-full.bin", "*merged*.bin"):
        hits = sorted(build_dir.glob(pat))
        if hits:
            return hits[0]
    return None


def merge_bin_args(build_dir: str | Path, out: str | Path) -> list[str]:
    """把分段韌體合成一個 0x0 開始的完整映像（esptool merge-bin）。"""
    fa = read_flasher_args(build_dir)
    fs = fa.get("flash_settings") or {}
    args = ["--chip", CHIP, "merge-bin", "-o", str(out)]
    for key, opt in (("flash_mode", "--flash-mode"), ("flash_freq", "--flash-freq"), ("flash_size", "--flash-size")):
        if fs.get(key):
            args += [opt, str(fs[key])]
    for off, rel in sorted(fa["flash_files"].items(), key=lambda kv: int(kv[0], 0)):
        args += [off, str((Path(build_dir) / rel).resolve())]
    return args


# ================================================================ esptool 指令
def esptool_major() -> int:
    try:
        from importlib.metadata import version
        return int(version("esptool").split(".")[0])
    except Exception:  # noqa: BLE001 — 沒裝或版本字串怪，當作新版
        return 5


def _legacy(args: list[str]) -> list[str]:
    """esptool 4.x 用底線（write_flash、--flash_mode）；5.x 用連字號。"""
    out = []
    for a in args:
        if a.startswith("--") or a in ("write-flash", "chip-id", "merge-bin", "default-reset", "hard-reset"):
            a = a.replace("-", "_") if not a.startswith("--") else "--" + a[2:].replace("-", "_")
        out.append(a)
    return out


def esptool_cmd(args: list[str]) -> list[str]:
    """回傳要執行的完整指令。打包成 .app 後用 app 自己當 esptool（見 app.py 的 --esptool）。"""
    if esptool_major() < 5:
        args = _legacy(args)
    if getattr(sys, "frozen", False):
        return [sys.executable, "--esptool", *args]
    return [sys.executable, "-m", "esptool", *args]


def write_flash_args(port: str, baud: int, plan: FlashPlan) -> list[str]:
    args = ["--chip", CHIP, "--port", port, "--baud", str(baud),
            "--before", "default-reset", "--after", "hard-reset",
            "write-flash", *plan.flash_args]
    for it in plan.items:
        args += [f"0x{it.offset:X}", str(it.path)]
    return args


def chip_id_args(port: str) -> list[str]:
    # 不指定 --chip，讓 esptool 自己偵測，才能告訴使用者接的是什麼
    return ["--port", port, "--before", "default-reset", "--after", "hard-reset", "chip-id"]


_PROGRESS_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%")
_CHIP_RE = re.compile(r"(?:Chip is|Chip type:|Detecting chip type\.\.\.)\s*(ESP[\w-]+)", re.I)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def parse_progress(line: str) -> float | None:
    if "Writing at" not in line and "Reading" not in line and "Downloading" not in line:
        return None
    m = _PROGRESS_RE.search(line)
    return float(m.group(1)) if m else None


def parse_chip(line: str) -> str | None:
    m = _CHIP_RE.search(line)
    return m.group(1).upper() if m else None


# ================================================================ 序列埠
@dataclass
class PortInfo:
    device: str
    description: str
    vid: int | None
    pid: int | None

    @property
    def is_passport(self) -> bool:
        return self.vid == ESPRESSIF_VID and self.pid == USB_JTAG_PID

    @property
    def label(self) -> str:
        tag = "✅ AI Passport（USB JTAG/serial debug unit）" if self.is_passport else self.description
        return f"{self.device} — {tag}"


def list_ports() -> list[PortInfo]:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    out = []
    for p in lp.comports():
        dev = p.device
        if sys.platform == "darwin":
            # macOS 每個裝置有 tty.* 與 cu.* 兩個；燒錄用 cu.*
            if dev.startswith("/dev/tty."):
                continue
            if "Bluetooth" in dev or "debug-console" in dev:
                continue
        out.append(PortInfo(dev, p.description or "", p.vid, p.pid))
    out.sort(key=lambda x: (not x.is_passport, "usbmodem" not in x.device, x.device))
    return out


# ================================================================ 網路
def _ssl_context() -> ssl.SSLContext:
    # python.org 的 macOS Python 預設沒裝根憑證，用 certifi 的
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def http_get(url: str, timeout: float = 20, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def http_get_json(url: str, timeout: float = 20, headers: dict | None = None):
    with http_get(url, timeout, headers) as r:
        return json.loads(r.read().decode("utf-8"))


def download_file(url: str, dest: str | Path, expected_sha256: str | None = None,
                  expected_size: int | None = None,
                  progress: Callable[[int, int | None], None] | None = None,
                  cancelled: Callable[[], bool] | None = None) -> Path:
    """下載到 dest（先寫 .part，驗證 SHA-256 後才改名）。已存在且雜湊相符就直接用。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and expected_sha256 and sha256_file(dest) == expected_sha256.lower():
        return dest
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    got = 0
    with http_get(url, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0) or expected_size
        while True:
            if cancelled and cancelled():
                f.close()
                part.unlink(missing_ok=True)
                raise FlashError("已取消下載。")
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            got += len(chunk)
            if progress:
                progress(got, total)
    if expected_size and got != expected_size:
        part.unlink(missing_ok=True)
        raise FlashError(f"下載不完整：收到 {got} bytes，應為 {expected_size} bytes。請再試一次。")
    if expected_sha256 and h.hexdigest() != expected_sha256.lower():
        part.unlink(missing_ok=True)
        raise FlashError("下載的韌體 SHA-256 不符，可能下載中斷或檔案被更改，已停止。")
    part.replace(dest)
    return dest


# ---------------------------------------------------------------- 官方社區
@dataclass
class Play:
    slug: str
    title: str
    title_en: str
    description: str
    instructions: str
    changelog: str
    author: str
    category: str
    downloads: int
    hearts: int
    official: bool
    source: str
    updated: str
    github_url: str
    thumbnail: str
    fw_available: bool
    fw_url: str
    fw_size: int
    fw_sha256: str
    fw_format: str

    @property
    def page_url(self) -> str:
        return f"{COMMUNITY_BASE}/plays/?play={urllib.parse.quote(self.slug)}"

    @property
    def download_url(self) -> str:
        return urllib.parse.urljoin(COMMUNITY_BASE, self.fw_url)

    @property
    def thumbnail_url(self) -> str:
        return urllib.parse.urljoin(COMMUNITY_BASE, self.thumbnail) if self.thumbnail else ""

    @property
    def cache_path(self) -> Path:
        safe = re.sub(r"[^\w.-]", "_", self.slug)
        return workspace_dir() / "firmware" / "community" / f"{safe}-{self.fw_sha256[:12]}.bin"

    @classmethod
    def from_api(cls, d: dict) -> "Play":
        def zh(v):
            if isinstance(v, dict):
                v = v.get("zh") or v.get("en") or ""
            return (v or "").replace("\r\n", "\n").replace("\r", "\n")
        fw = d.get("firmware") or {}
        return cls(
            slug=d.get("slug", ""),
            title=zh(d.get("title")),
            title_en=(d.get("title") or {}).get("en", "") if isinstance(d.get("title"), dict) else "",
            description=zh(d.get("description")),
            instructions=zh(d.get("instructions")),
            changelog=zh(d.get("changelog")),
            author=d.get("author") or (d.get("authorProfile") or {}).get("name", ""),
            category=d.get("categoryKey") or d.get("category") or "",
            downloads=int(d.get("downloads") or 0),
            hearts=int(d.get("hearts") or 0),
            official=bool(d.get("isOfficial")),
            source=d.get("source", ""),
            updated=(d.get("updatedAt") or "")[:10],
            github_url=d.get("githubUrl") or "",
            thumbnail=d.get("thumbnail") or "",
            fw_available=bool(fw.get("available")),
            fw_url=fw.get("url") or d.get("downloadUrl") or "",
            fw_size=int(fw.get("size") or d.get("firmwareSize") or 0),
            fw_sha256=(fw.get("sha256") or d.get("firmwareSha256") or "").lower(),
            fw_format=fw.get("format") or "",
        )


def fetch_plays(q: str = "", category: str = "", sort: str = "downloads",
                offset: int = 0, limit: int = 30) -> tuple[list[Play], int, bool]:
    params = {"q": q, "category": category, "sort": sort, "period": "all",
              "limit": str(min(limit, 50)), "offset": str(offset)}
    if not category:
        del params["category"]
    if not q:
        del params["q"]
    url = f"{COMMUNITY_BASE}/api/plays?{urllib.parse.urlencode(params)}"
    try:
        d = http_get_json(url)
    except Exception as e:  # noqa: BLE001
        raise FlashError(f"連不上官方社區（{e}）。請確認網路。") from e
    if not d.get("ok"):
        raise FlashError(f"官方社區回應錯誤：{d.get('detail') or d}")
    plays = [Play.from_api(p) for p in d.get("plays", [])]
    pg = d.get("pagination") or {}
    return plays, int(pg.get("total") or len(plays)), bool(pg.get("hasMore"))


def download_play(play: Play, progress=None, cancelled=None) -> Path:
    if not play.fw_available or not play.fw_url:
        raise FlashError("這個玩法目前沒有可下載的韌體。")
    if play.fw_format and play.fw_format != "esp-merged-0x0":
        raise FlashError(f"不認得的韌體格式 {play.fw_format}，為了安全不寫入。")
    return download_file(play.download_url, play.cache_path, play.fw_sha256 or None,
                         play.fw_size or None, progress, cancelled)


# ---------------------------------------------------------------- GitHub
_GH_RE = re.compile(r"^(?:https?://github\.com/|git@github\.com:)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?(?:[#?].*)?$")


def parse_github_repo(text: str) -> tuple[str, str]:
    text = text.strip()
    text = re.sub(r"^(https?://github\.com/[\w.-]+/[\w.-]+)/(?:tree|blob|releases)/.*$", r"\1", text)
    m = _GH_RE.match(text)
    if not m:
        raise FlashError(f"看不懂這個 GitHub 位址：{text}\n請輸入像 https://github.com/owner/repo 的網址。")
    return m.group(1), m.group(2)


def github_clone_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}.git"


def project_dir_for(owner: str, repo: str) -> Path:
    return workspace_dir() / "projects" / f"{owner}__{repo}"


@dataclass
class ReleaseAsset:
    tag: str
    name: str
    url: str
    size: int

    @property
    def label(self) -> str:
        return f"{self.tag} · {self.name} ({human_size(self.size)})"


def fetch_release_bins(owner: str, repo: str) -> list[ReleaseAsset]:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=15"
    try:
        rels = http_get_json(url, headers={"Accept": "application/vnd.github+json"})
    except Exception as e:  # noqa: BLE001
        raise FlashError(f"讀不到 {owner}/{repo} 的 Releases（{e}）。") from e
    out = []
    for r in rels:
        for a in r.get("assets", []):
            if a.get("name", "").lower().endswith(".bin"):
                out.append(ReleaseAsset(r.get("tag_name", ""), a["name"], a["browser_download_url"], a.get("size", 0)))
    return out


def release_cache_path(owner: str, repo: str, asset: ReleaseAsset) -> Path:
    safe = lambda s: re.sub(r"[^\w.-]", "_", s)  # noqa: E731
    return workspace_dir() / "firmware" / "github" / f"{owner}__{repo}" / safe(asset.tag) / safe(asset.name)


def git_sync_commands(url: str, dest: Path, ref: str = "") -> list[list[str]]:
    """第一次 clone，之後 pull；都會更新子模組。"""
    if (dest / ".git").exists():
        cmds = [["git", "-C", str(dest), "fetch", "--tags", "origin"]]
        if ref:
            cmds.append(["git", "-C", str(dest), "checkout", ref])
        # 在分支上才 pull（指定 tag 時是 detached HEAD，不需要也不能 pull）
        q = shlex.quote(str(dest))
        cmds.append(["/bin/bash", "-c",
                     f"if git -C {q} symbolic-ref -q HEAD >/dev/null; then git -C {q} pull --ff-only; "
                     f"else echo '使用指定版本：'$(git -C {q} describe --tags --always); fi"])
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmds = [["git", "clone", "--recursive", *(["--branch", ref] if ref else []), url, str(dest)]]
    cmds.append(["git", "-C", str(dest), "submodule", "update", "--init", "--recursive"])
    return cmds


def git_log(project: Path, n: int = 60) -> list[dict]:
    """專案的提交紀錄：hash、日期、標籤、說明。"""
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(project), "log", f"-{n}", "--format=%h%x1f%cs%x1f%D%x1f%s"],
                             capture_output=True, text=True, timeout=15, env=tool_env()).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            tags = [t.strip()[5:] for t in parts[2].split(",") if t.strip().startswith("tag: ")]
            rows.append({"hash": parts[0], "date": parts[1], "tags": tags, "subject": parts[3]})
    return rows


def project_version(project: Path) -> str:
    """版本字串：version.txt（有的話）＋目前提交。"""
    import subprocess
    ver = ""
    vt = project / "version.txt"
    if vt.exists():
        ver = vt.read_text("utf-8", "replace").strip()
    try:
        h = subprocess.run(["git", "-C", str(project), "describe", "--tags", "--always", "--dirty"],
                           capture_output=True, text=True, timeout=10, env=tool_env()).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        h = ""
    return " · ".join(x for x in (ver and f"v{ver}", h) if x)


# ---------------------------------------------------------------- ESP-IDF
def find_idf_candidates() -> list[Path]:
    seen, out = set(), []
    cands = []
    if os.environ.get("IDF_PATH"):
        cands.append(Path(os.environ["IDF_PATH"]))
    esp = Path.home() / "esp"
    cands += [esp / f"esp-idf-{DEFAULT_IDF_VERSION}", esp / "esp-idf"]
    if esp.is_dir():
        cands += sorted(esp.glob("esp-idf*"), reverse=True) + sorted(esp.glob("*/esp-idf"), reverse=True)
    for c in cands:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r not in seen and (r / "export.sh").exists():
            seen.add(r)
            out.append(r)
    return out


def idf_version(idf: Path) -> str:
    v = idf / "version.txt"
    if v.exists():
        return v.read_text().strip()
    m = re.search(r'IDF_VERSION_MAJOR\s+(\d+).*?IDF_VERSION_MINOR\s+(\d+).*?IDF_VERSION_PATCH\s+(\d+)',
                  (idf / "components/esp_common/include/esp_idf_version.h").read_text()
                  if (idf / "components/esp_common/include/esp_idf_version.h").exists() else "", re.S)
    return f"v{m.group(1)}.{m.group(2)}.{m.group(3)}" if m else "?"


def needs_set_target(project: Path) -> bool:
    sdk = project / "sdkconfig"
    if not sdk.exists():
        return True
    return f'CONFIG_IDF_TARGET="{CHIP}"' not in sdk.read_text("utf-8", "replace")


def is_idf_project(project: Path) -> bool:
    cm = project / "CMakeLists.txt"
    return cm.exists() and "project.cmake" in cm.read_text("utf-8", "replace")


def idf_build_command(idf: Path, project: Path) -> list[str]:
    steps = [f"source {shlex.quote(str(idf / 'export.sh'))} >/dev/null",
             f"cd {shlex.quote(str(project))}"]
    if needs_set_target(project):
        steps.append(f"idf.py set-target {CHIP}")
    steps.append("idf.py build")
    return ["/bin/bash", "-c", "set -e; " + " && ".join(steps)]


def idf_install_command(version: str = DEFAULT_IDF_VERSION) -> tuple[Path, list[str]]:
    dest = Path.home() / "esp" / f"esp-idf-{version}"
    script = (
        "set -e\n"
        "command -v git >/dev/null || { echo '缺少 git：請先在終端機執行 xcode-select --install'; exit 1; }\n"
        "for t in cmake ninja; do command -v $t >/dev/null || MISSING=\"$MISSING $t\"; done\n"
        "if [ -n \"$MISSING\" ]; then\n"
        "  if command -v brew >/dev/null; then echo \"安裝$MISSING …\"; brew install $MISSING dfu-util;\n"
        "  else echo \"缺少$MISSING：請先安裝 Homebrew（https://brew.sh）後執行 brew install cmake ninja dfu-util\"; exit 1; fi\n"
        "fi\n"
        f"mkdir -p {shlex.quote(str(dest.parent))}\n"
        f"if [ ! -d {shlex.quote(str(dest))}/.git ]; then\n"
        f"  git clone --recursive --depth 1 --shallow-submodules --branch {shlex.quote(version)} "
        f"https://github.com/espressif/esp-idf.git {shlex.quote(str(dest))}\n"
        "fi\n"
        f"cd {shlex.quote(str(dest))} && ./install.sh {CHIP}\n"
        "echo 'ESP-IDF 安裝完成'\n"
    )
    return dest, ["/bin/bash", "-c", script]
