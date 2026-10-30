"""Parse 國中單字王 2000 單字題庫表 (114年版): two columns of [no, word, pos, zh]."""
import sys, json, re, pdfplumber

# column layouts: (x_min, x_max) for each field, per column
COLS = [
    dict(no=(25, 70), word=(70, 160), pos=(160, 215), zh=(215, 297)),
    dict(no=(297, 340), word=(340, 435), pos=(435, 490), zh=(490, 600)),
]

def field_of(x, col):
    for k, (a, b) in col.items():
        if a <= x < b:
            return k
    return None

def parse(path):
    rows = []
    with pdfplumber.open(path) as pdf:
        for pi, pg in enumerate(pdf.pages):
            words = [w for w in pg.extract_words() if 70 < w["top"] < pg.height - 30
                     and w["text"] not in ("單字、詞", "詞性", "中文字義")]
            for col in COLS:
                lo, hi = col["no"][0], max(b for a, b in col.values())
                cw = [w for w in words if lo <= w["x0"] < hi]
                anchors = [w for w in cw if field_of(w["x0"], col) == "no" and w["text"].isdigit()]
                if not anchors:
                    continue
                buckets = {id(a): {"no": int(a["text"]), "top": a["top"], "word": [], "pos": [], "zh": []} for a in anchors}
                for w in cw:
                    f = field_of(w["x0"], col)
                    if f == "no" and not w["text"].isdigit():
                        f = "word"  # long phrases start left of the word column
                    if f in (None, "no"):
                        continue
                    a = min(anchors, key=lambda a: abs(a["top"] - w["top"]))
                    buckets[id(a)][f].append(w)
                for b in buckets.values():
                    order = lambda ws: sorted(ws, key=lambda w: (round(w["top"] / 4), w["x0"]))
                    b["word"] = " ".join(w["text"] for w in sorted(b["word"], key=lambda w: w["x0"]))
                    b["pos"] = "".join(w["text"] for w in order(b["pos"]))
                    b["zh"] = "".join(w["text"] for w in order(b["zh"]))
                    b["page"] = pi + 1
                    del b["top"]
                    rows.append(b)
    rows.sort(key=lambda r: r["no"])
    return rows

if __name__ == "__main__":
    rows = parse(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=0)
    nos = [r["no"] for r in rows]
    missing = sorted(set(range(1, max(nos) + 1)) - set(nos))
    dup = sorted({n for n in nos if nos.count(n) > 1})
    bad = [r for r in rows if not r["word"] or not r["pos"] or not r["zh"] or not re.fullmatch(r"[A-Za-z .'\-/()]+", r["word"])]
    print("rows", len(rows), "max", max(nos), "missing", missing[:50], "dup", dup[:50], "suspicious", len(bad))
    with open(sys.argv[2] + ".bad.txt", "w", encoding="utf-8") as f:
        for r in bad:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
