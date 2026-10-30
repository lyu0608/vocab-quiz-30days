"""v6.2.9 by Claude：由國中教育會考官方「英語（聽力）」壓縮檔產生聽力真題題庫 cap_listening_bank.js 與壓縮音檔

來源：國中教育會考官網 https://cap.rcpet.edu.tw/exam/{年度}/{年度}P_Listening.zip (113 年為 113_Listening.zip)
  壓縮檔內含聽力試題本 {年度}P_Listening.pdf 與每題一個官方音檔「NN 第X題.mp3」
答案：{年度}P_Answer.pdf 選擇題參考答案一覽表的「英語聽力」欄 (先轉成 {年度}P_Answer.txt，見 build_cap_bank.py)
只收錄有文字選項的題目 (基本問答、言談理解)；看圖作答的題目 (辨識句意、選項為圖片的題目) 因 App 無法顯示圖片而略過。
音檔轉成 48kbps 單聲道 (原檔 192kbps 立體聲)，語音清楚且檔案約縮小為 1/4。

usage: python tools/build_cap_listening.py <答案 txt 資料夾> <輸出 js> <輸出音檔資料夾> 113=<解壓資料夾> 112=<解壓資料夾> ...
"""
import sys, os, re, io, json, glob, subprocess
import pdfplumber
import imageio_ffmpeg

def listening_answers(path):
    """答案表欄位齊全的列：題號 國文 英語閱讀 英語聽力 數學 社會 自然 → 取英語聽力欄"""
    ans = {}
    for line in io.open(path, encoding="utf-8").read().splitlines():
        m = re.match(r"^\s*(\d{1,2})\s+([A-D](?:\s+[A-D]){5})\s*$", line)
        if m:
            ans[int(m.group(1))] = m.group(2).split()[2]
    return ans

def clean(s):
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\s*\d*\s*請翻頁繼續作答\s*", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def parse_booklet(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    items, part = {}, None
    # 依「第X部分」與「第N題」切段；示例題不在「第N題」之內
    tokens = re.split(r"(第[一二三]部分：\S+|第\s*\d+\s*題)", text)
    for i, tok in enumerate(tokens):
        pm = re.match(r"第[一二三]部分：(\S+?)\s*[（(]", tok + "(") if tok.startswith("第") and "部分" in tok else None
        if pm:
            part = re.sub(r"[（(].*", "", tok.split("：", 1)[1]); continue
        qm = re.match(r"第\s*(\d+)\s*題$", tok)
        if qm and i + 1 < len(tokens):
            body = clean(tokens[i + 1])
            om = re.search(r"\(A\)\s*(.*?)\s*\(B\)\s*(.*?)\s*\(C\)\s*(.*?)(?:\s+\d+)?(?:\s*試題結束|\s*聽力試題播音完畢|$)", body)
            items[int(qm.group(1))] = {"part": part, "options": [clean(o) for o in om.groups()] if om else None}
    return items

def find_audio(folder, n):
    for f in glob.glob(os.path.join(folder, "**", "*.mp3"), recursive=True):
        if re.search(rf"第\s*{n}\s*題", os.path.basename(f)):
            return f
    return None

if __name__ == "__main__":
    ans_dir, out_js, audio_dir = sys.argv[1:4]
    pairs = [a.split("=", 1) for a in sys.argv[4:]]
    os.makedirs(audio_dir, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    bank, report = [], []
    for year, folder in pairs:
        pdf = (glob.glob(os.path.join(folder, "**", "*Listening*.pdf"), recursive=True) or glob.glob(os.path.join(folder, "**", "*.pdf"), recursive=True))[0]
        items = parse_booklet(pdf)
        ans = listening_answers(os.path.join(ans_dir, f"{year}P_Answer.txt"))
        kept, skipped = 0, []
        for n in sorted(items):
            it = items[n]
            opts = it["options"]
            if it["part"] == "辨識句意" or not opts or any(not o for o in opts):
                skipped.append((n, "看圖題")); continue
            if ans.get(n) not in ("A", "B", "C"):
                skipped.append((n, f"無官方答案 {ans.get(n)}")); continue
            src = find_audio(folder, n)
            if not src:
                skipped.append((n, "找不到音檔")); continue
            rel = f"listen/{year}-{n:02d}.mp3"
            dst = os.path.join(audio_dir, f"{year}-{n:02d}.mp3")
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "22050", "-b:a", "48k", dst], check=True)
            bank.append({"id": f"{year}-L{n:02d}", "year": year, "no": n, "part": it["part"], "options": opts, "answer": ans[n], "audio": rel})
            kept += 1
        report.append(f"{year}: 收錄 {kept} 題，略過 {skipped}")
    with io.open(out_js, "w", encoding="utf-8", newline="\n") as f:
        f.write("// ========================================================\n")
        f.write(f"// 國中教育會考 英語科聽力 官方真題 (共 {len(bank)} 題)\n")
        f.write("// 由 tools/build_cap_listening.py 從官方聽力試題本、官方音檔與參考答案自動產生，請勿手動編輯。\n")
        f.write("// 資料來源：國中教育會考官網 https://cap.rcpet.edu.tw/examination.html\n")
        f.write("// 欄位：id, year, no (題號), part (題型), options (A-C), answer (官方答案), audio (官方音檔，已壓縮)\n")
        f.write("// ========================================================\n\n")
        f.write("window.CAP_LISTENING_OFFICIAL = [\n")
        f.write(",\n".join("  " + json.dumps(q, ensure_ascii=False) for q in bank))
        f.write("\n];\n")
    print("\n".join(report))
    print("TOTAL", len(bank))
