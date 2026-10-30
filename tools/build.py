"""Build vocab2000.js from the three source PDFs.

usage: python build.py <moe.pdf> <b1200.pdf> <junyi.pdf> <old vocab2000.js> <out.js> <report.txt>
"""
import sys, re, json, io, os
import pdfplumber
from parse_moe import parse as parse_moe

moe_pdf, b1200_pdf, junyi_pdf, old_js, out_js, report_path = sys.argv[1:7]
rep = io.open(report_path, "w", encoding="utf-8")

def norm(en):
    en = en.replace("’", "'").replace("‘", "'").replace(" xxx ", " … ")
    return re.sub(r"\s+", " ", en).strip()

POS_1200 = {"n": "名詞", "v": "動詞", "vt": "動詞", "vi": "動詞", "adj": "形容詞", "adv": "副詞", "prep": "介系詞",
            "pron": "代名詞", "conj": "連接詞", "num": "數詞", "int": "感嘆詞", "interj": "感嘆詞", "aux": "助動詞", "片": "片語", "art": "冠詞"}

def short_1200(d):
    """'能力、才幹(n) 其他(vt)' -> ('能力；才幹', '名詞'): first sense only, at most 3 glosses."""
    m = re.search(r"\((n|v|vt|vi|adj|adv|prep|pron|conj|int|interj|aux|片|art|num)(?:[、, ]+[a-z]+)*\)", d)
    head = d[:m.start()] if m else d
    glosses = [g.strip() for g in re.split(r"[、;；\s]+", head) if g.strip()][:3]
    return "；".join(glosses), POS_1200.get(m.group(1), "") if m else ""

def key(en):
    return norm(en).lower().replace("-", " ")

# ---------- 1. MOE 114 2000 list (primary) ----------
FIX = {396: dict(pos="名詞", zh="堂(表)兄弟姊妹"), 551: dict(word="either", pos="副詞/連接詞")}
ZH_TYPO = {"漫書": "漫畫"}
moe = parse_moe(moe_pdf)
for r in moe:
    r.update(FIX.get(r["no"], {}))
    r["word"] = norm(r["word"])
    r["zh"] = re.sub(r"\d+$", "", r["zh"])  # trailing page numbers
    for a, b in ZH_TYPO.items():
        r["zh"] = r["zh"].replace(a, b)
assert len(moe) == 2000 and all(r["word"] and r["pos"] and r["zh"] for r in moe)

# ---------- 2. 1200 basic list ----------
b1200 = {}
with pdfplumber.open(b1200_pdf) as pdf:
    for pg in pdf.pages:
        ws = [w for w in pg.extract_words() if 70 < w["top"] < pg.height - 40 and w["text"] not in ("單字", "解釋")]
        heads = {}
        for w in ws:
            if w["x0"] < 160:
                heads.setdefault(round(w["top"]), []).append(w)
        anchors = [(t, " ".join(x["text"] for x in sorted(v, key=lambda x: x["x0"]))) for t, v in heads.items()]
        if not anchors:
            continue
        defs = {t: [] for t, _ in anchors}
        for w in ws:
            if w["x0"] >= 160:
                t = min(anchors, key=lambda a: abs(a[0] - w["top"]))[0]
                defs[t].append(w)
        for t, en in anchors:
            d = " ".join(x["text"] for x in sorted(defs[t], key=lambda x: (round(x["top"] / 6), x["x0"])))
            b1200[key(en)] = (norm(en), d)
rep.write(f"1200 list entries parsed: {len(b1200)}\n")

# ---------- 3. Junyi 2000 list (cross-check only: has no definitions) ----------
junyi_text = ""
with pdfplumber.open(junyi_pdf) as pdf:
    for pg in pdf.pages:
        ws = [w for w in pg.extract_words() if 285 <= w["x0"] < 380 and w["top"] > 75]
        ws.sort(key=lambda w: (round(w["top"]), w["x0"]))
        prev_top = None
        for w in ws:
            if prev_top is not None and w["top"] - prev_top > 15:  # gap between 20-word groups
                junyi_text += ","
            junyi_text += " " + w["text"]
            prev_top = w["top"]
        junyi_text += ","
junyi = [norm(x) for x in junyi_text.split(",") if x.strip()]
rep.write(f"Junyi list entries parsed: {len(junyi)}\n")

# ---------- 4. old vocab2000.js (keep kk / example sentences) ----------
old = {}
src = io.open(old_js, encoding="utf-8").read()
for m in re.finditer(r"\{\s*en:.*?\}", src):
    obj = {}
    for fm in re.finditer(r'(\w+):\s*("(?:[^"\\]|\\.)*"|\d+)', m.group(0)):
        obj[fm.group(1)] = json.loads(fm.group(2))
    old.setdefault(key(obj["en"]), obj)

# ---------- merge ----------
entries = {}
for r in moe:
    k = key(r["word"])
    e = entries.get(k)
    if e is None:
        entries[k] = dict(en=r["word"], zh=r["zh"], pos=r["pos"], no=r["no"])
    else:  # same word listed under several parts of speech
        if r["pos"] not in e["pos"].split("/"):
            e["pos"] += "/" + r["pos"]
        if r["zh"] not in e["zh"].split("；"):
            e["zh"] += "；" + r["zh"]
rep.write(f"MOE rows 2000 -> unique words {len(entries)}\n")

only1200 = [v for k, v in b1200.items() if k not in entries]
FIX_1200 = {  # page-break / multi-line / run-on defs
    "bar": ("條；棒；酒吧", "名詞"), "Mid-Autumn": ("中秋（節）", "名詞"),
    "unit": ("單位；單元", "名詞"), "cell": ("細胞；單元；小房間", "名詞"), "better": ("較好的；更好的", "形容詞"),
}
for en, d in only1200:
    zh, pos = FIX_1200.get(en) or short_1200(d)
    entries[key(en)] = dict(en=en, zh=zh, pos=pos, no=None)

for k, e in entries.items():
    if k in b1200:
        e["in1200"] = True
    o = old.get(k)
    if o:
        for f in ("kk", "sentenceEn", "sentenceZh"):
            if o.get(f):
                e[f] = o[f]

def loose(en):
    """'baby sitter' / 'babysitter', 'backwards' / 'backward', 'Mr.' / 'Mr' compare equal."""
    k = re.sub(r"\(s\)$", "", en.lower())
    k = re.sub(r"[^a-z]", "", k)
    return k[:-1] if k.endswith("s") and len(k) > 3 else k

loose_keys = {loose(e["en"]) for e in entries.values()}
junyi_keys = {key(x) for x in junyi}
moe_keys = {key(r["word"]) for r in moe}
LOOSE_FALSE_MATCH = {"new"}  # 'new' is not a variant of 'news'
junyi_only = sorted({x for x in junyi if key(x) not in entries
                     and (loose(x) not in loose_keys or x in LOOSE_FALSE_MATCH)}, key=str.lower)

# Junyi-only words carry no definition in the PDF; glosses are hand-written in junyi_zh.json
JUNYI_ZH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "junyi_zh.json")
junyi_zh = json.load(io.open(JUNYI_ZH_PATH, encoding="utf-8")) if os.path.exists(JUNYI_ZH_PATH) else {}
added_junyi, missing_zh = [], []
for en in junyi_only:
    g = junyi_zh.get(en)
    if g:
        entries[key(en)] = dict(en=en, zh=g["zh"], pos=g["pos"], no=None, junyi=True)
        added_junyi.append(en)
    else:
        missing_zh.append(en)
rep.write(f"Junyi-only words added with hand-written zh: {len(added_junyi)}\n")
old_only = sorted(o["en"] for k, o in old.items() if k not in entries)

rep.write(f"words only in 1200 list (added, zh from 1200): {len(only1200)}\n  " + ", ".join(en for en, _ in only1200) + "\n")
rep.write(f"words only in Junyi list with no zh in junyi_zh.json (NOT added): {len(missing_zh)}\n  " + ", ".join(missing_zh) + "\n")
rep.write(f"MOE words missing from Junyi list: {len(moe_keys - junyi_keys)}\n")
rep.write(f"old vocab2000.js words not in any PDF (dropped): {len(old_only)}\n  " + ", ".join(old_only) + "\n")
rep.write(f"entries kept kk/sentences from old file: {sum(1 for e in entries.values() if 'kk' in e)}\n")

final = sorted(entries.values(), key=lambda e: (e["no"] is None, e["no"] or 0, e["en"].lower()))
for e in final:
    if e["no"] is None:
        del e["no"]
    if not e["pos"]:
        del e["pos"]
rep.write(f"TOTAL entries: {len(final)}\n")

bad = [e for e in final if not e["zh"]]
rep.write(f"entries with empty zh: {len(bad)} {[e['en'] for e in bad]}\n")
FIELDS = ("en", "zh", "pos", "kk", "no", "in1200", "junyi", "sentenceEn", "sentenceZh")
lines = []
for e in final:
    parts = [f"{f}: {json.dumps(e[f], ensure_ascii=False)}" for f in FIELDS if f in e]
    lines.append("  { " + ", ".join(parts) + " }")
with io.open(out_js, "w", encoding="utf-8", newline="\n") as f:
    f.write(f"""// ========================================================
// 英語單字特訓 App - 題庫檔案 (共 {len(final):,} 字)
// 由 build.py 自動產生，請勿手動編輯。來源：
//   1. 國中單字王 2000 單字題庫表 (114年版)  -> en / zh / pos / no
//   2. 國中基礎1200單字                    -> in1200 標記，並補入 2000 表未收錄的字 (no 欄位為空)
//   3. 均一 國中英文單字 2000 講義           -> 補入前兩表未收錄的字 (junyi 標記；講義無中文，zh 取自 tools/junyi_zh.json 人工撰寫)
// kk / sentenceEn / sentenceZh 僅保留自舊版題庫中已有的字
// ========================================================

window.EXTERNAL_VOCAB = [
""")
    f.write(",\n".join(lines))
    f.write("\n];\n")
print("done", len(final))
