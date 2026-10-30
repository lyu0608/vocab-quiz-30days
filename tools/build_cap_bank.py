"""v6.2.8 by Claude：由國中教育會考官方英語閱讀試題本與參考答案，產生真題題庫 cap_exam_bank.js

來源 (國中教育會考官網 https://cap.rcpet.edu.tw/examination.html 歷屆試題)：
  {年度}P_English.pdf (或 {年度}P_English150DPI.pdf，存成 {年度}P_English.pdf)  英語科閱讀試題本
  {年度}P_Answer.pdf                                                        選擇題參考答案一覽表
只收錄「第一部分：單題」；需看圖作答的題目 (題幹含 picture) 因 App 無法顯示圖片而略過。

usage: python tools/build_cap_bank.py <放 PDF 的資料夾> <輸出 cap_exam_bank.js> 113 112 111 110 109 108
"""
import sys, os, re, io, json
import pdfplumber

BLANK = "______"

def page_lines(page):
    """依座標重建文字行；試題的空格是畫出來的底線 (不是文字)，在底線位置補回 BLANK"""
    words = page.extract_words(use_text_flow=True)
    segs = []
    for l in page.lines + page.rects:
        if abs(l["bottom"] - l["top"]) < 2.5 and l["x1"] - l["x0"] > 12:
            segs.append((l["x0"], l["x1"], (l["top"] + l["bottom"]) / 2))
    rows = []
    for w in sorted(words, key=lambda w: (round(w["bottom"]), w["x0"])):
        for r in rows:
            if abs(r["bottom"] - w["bottom"]) < 3:
                r["words"].append(w); break
        else:
            rows.append({"bottom": w["bottom"], "words": [w]})
    out = []
    for r in sorted(rows, key=lambda r: r["bottom"]):
        items = [(w["x0"], w["text"]) for w in r["words"]]
        for x0, x1, y in segs:
            if -2 <= y - r["bottom"] <= 6 and not any(w["x0"] < x1 - 2 and w["x1"] > x0 + 2 for w in r["words"]):
                items.append((x0, BLANK))
        out.append(" ".join(t for _, t in sorted(items)))
    return out

def official_answers(path):
    """答案表每列：題號 國文 英語閱讀 (英語聽力) 數學 社會 自然；只取欄位齊全的列的英語閱讀欄"""
    ans = {}
    for line in io.open(path, encoding="utf-8").read().splitlines():
        m = re.match(r"^\s*(\d{1,2})\s+([A-D](?:\s+[A-D]){4,5})\s*$", line)
        if m:
            ans[int(m.group(1))] = m.group(2).split()[1]
    return ans

def clean(s):
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"_{3,}", BLANK, s)
    s = re.sub(r"\s+\d{1,2}(\s*請翻頁繼續作答)?\s*$", "", s)  # 頁碼 / 翻頁提示黏在最後一個選項
    s = re.sub(r"\s*請翻頁繼續作答\s*", " ", s)
    s = re.sub(r"\s+([,.?!;:])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()

def extract(pdf_dir, year):
    pdf_path = os.path.join(pdf_dir, f"{year}P_English.pdf")
    with pdfplumber.open(pdf_path) as pdf:
        lines = [ln for p in pdf.pages for ln in page_lines(p)]
    text = "\n".join(lines)
    m = re.search(r"單題\s*[（(]?\s*第\s*1\s*[-–~～至]\s*(\d+)\s*題", text)
    if not m:
        raise SystemExit(f"{year}: 找不到「單題」段落")
    count, body = int(m.group(1)), text[m.end():]
    ans = official_answers(os.path.join(pdf_dir, f"{year}P_Answer.txt"))
    out, skipped = [], []
    for n in range(1, count + 1):
        mm = re.search(rf"(?m)^{n}\s*\.\s*(.+?)(?=^{n+1}\s*\.\s|第二部分|\Z)", body, re.S)
        block = clean(mm.group(1)) if mm else ""
        om = re.search(r"\(A\)\s*(.+?)\s*\(B\)\s*(.+?)\s*\(C\)\s*(.+?)\s*\(D\)\s*(.+)$", block)
        if not om:
            skipped.append((n, "無法解析選項")); continue
        stem = clean(block[:om.start()])
        options = [clean(o) for o in om.groups()]
        if re.search(r"\bpictures?\b", stem, re.I):
            skipped.append((n, "看圖題")); continue
        a = ans.get(n)
        if a not in ("A", "B", "C", "D"):
            skipped.append((n, "無官方答案")); continue
        if len(set(o.lower() for o in options)) != 4:
            skipped.append((n, "選項重複")); continue
        out.append({"id": f"{year}-{n:02d}", "year": year, "no": n, "stem": stem, "options": options, "answer": a})
    return out, skipped

if __name__ == "__main__":
    pdf_dir, out_js, years = sys.argv[1], sys.argv[2], sys.argv[3:]
    bank, report = [], []
    for y in years:
        qs, skipped = extract(pdf_dir, y)
        bank += qs
        report.append(f"{y}: 收錄 {len(qs)} 題，略過 {skipped}")
    with io.open(out_js, "w", encoding="utf-8", newline="\n") as f:
        f.write("// ========================================================\n")
        f.write(f"// 國中教育會考 英語科閱讀「單題」官方真題 (共 {len(bank)} 題，{years[-1]}–{years[0]} 年)\n")
        f.write("// 由 tools/build_cap_bank.py 從官方試題本與參考答案自動產生，請勿手動編輯。\n")
        f.write("// 資料來源：國中教育會考官網 https://cap.rcpet.edu.tw/examination.html\n")
        f.write("// 欄位：id, year (年度), no (題號), stem (題幹，______ 為空格), options (A-D), answer (官方答案)\n")
        f.write("// ========================================================\n\n")
        f.write("window.CAP_OFFICIAL_BANK = [\n")
        f.write(",\n".join("  " + json.dumps(q, ensure_ascii=False) for q in bank))
        f.write("\n];\n")
    print("\n".join(report))
    print("TOTAL", len(bank))
