"""v6.2.9 by Claude：由國中教育會考官方「英語（聽力）」壓縮檔產生聽力真題題庫 cap_listening_bank.js 與壓縮音檔

來源：國中教育會考官網 https://cap.rcpet.edu.tw/exam/{年度}/{年度}P_Listening.zip (113 年為 113_Listening.zip)
  壓縮檔內含聽力試題本 {年度}P_Listening.pdf 與每題一個官方音檔「NN 第X題.mp3」
答案：{年度}P_Answer.pdf 選擇題參考答案一覽表的「英語聽力」欄 (先轉成 {年度}P_Answer.txt，見 build_cap_bank.py)
只收錄有文字選項的題目 (基本問答、言談理解)；看圖作答的題目 (辨識句意、選項為圖片的題目) 因 App 無法顯示圖片而略過。
音檔轉成 48kbps 單聲道 (原檔 192kbps 立體聲)，語音清楚且檔案約縮小為 1/4。

v6.3.0 by Claude：擴充到 104–108、110–115 年。104–108、111 參考試題本、114、115 的聽力檔與參考答案放在官網連結的 Google 雲端硬碟
  (104/105 壓縮檔內還有 rar，需用 7-Zip 解開)；109 年官網未提供聽力檔；102/103 年為試辦，參考答案表沒有聽力答案，不收錄。
  111 參考試題本的題目全部沿用 104–107 年原題，會被判定為重複而略過，因此 111c 要放在參數最後。

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
    s = s.replace("：", ":")  # v6.3.0 by Claude：105 年選項「12：30」用全形冒號，統一為半形
    return re.sub(r"\s+", " ", s).strip()

def parse_booklet(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    items, part = {}, None
    # 依「第X部分」與「第N題」切段；示例題不在「第N題」之內
    # v6.3.0 by Claude：104 年標題為「第三部分: 言談理解」(半形冒號加空白)，冒號改為全形/半形皆可
    tokens = re.split(r"(第[一二三]部分\s*[：:]\s*\S+|第\s*\d+\s*題)", text)
    for i, tok in enumerate(tokens):
        if tok.startswith("第") and "部分" in tok:
            part = re.sub(r"[（(].*", "", re.split(r"[：:]\s*", tok, maxsplit=1)[1]); continue
        qm = re.match(r"第\s*(\d+)\s*題$", tok)
        if qm and i + 1 < len(tokens):
            body = clean(tokens[i + 1])
            # v6.3.0 by Claude：選項 (C) 之後的頁碼與「聽力試題播音結束/完畢」結尾字樣不屬於選項，一併截掉
            om = re.search(r"\(A\)\s*(.*?)\s*\(B\)\s*(.*?)\s*\(C\)\s*(.*?)(?:\s+\d+)?(?:\s*試題結束|\s*聽力試題播音(?:完畢|結束).*|$)", body)
            # v6.3.0 by Claude：選項皆為英文；遇到中文字 (含 PDF 內的相容漢字，如 111 參考試題本結尾的播音完畢字樣) 即截斷
            items[int(qm.group(1))] = {"part": part, "options": [re.sub(r"\s*[　-鿿豈-﫿].*$", "", clean(o)) for o in om.groups()] if om else None}
    return items

# v6.3.0 by Claude：105 年音檔以國字題號命名 (例「04第一題.mp3」)，加入國字 1–21 的對照
ZH_NUM = {n: z for n, z in enumerate("一 二 三 四 五 六 七 八 九 十 十一 十二 十三 十四 十五 十六 十七 十八 十九 二十 二十一 二十二".split(), 1)}

def find_audio(folder, n):
    for f in glob.glob(os.path.join(folder, "**", "*.mp3"), recursive=True):
        if re.search(rf"第\s*(?:{n}|{ZH_NUM[n]})\s*題", os.path.basename(f)):
            return f
    return None

if __name__ == "__main__":
    ans_dir, out_js, audio_dir = sys.argv[1:4]
    pairs = [a.split("=", 1) for a in sys.argv[4:]]
    os.makedirs(audio_dir, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    bank, report, seen = [], [], {}  # v6.3.0 by Claude：seen 記錄已收錄的選項組合，用來排除重複題
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
            # v6.3.0 by Claude：111 年參考試題本大多沿用 104–107 年的原題，選項完全相同者視為重複不再收錄
            dup = seen.get(tuple(opts))
            if dup:
                skipped.append((n, f"與 {dup} 重複")); continue
            src = find_audio(folder, n)
            if not src:
                skipped.append((n, "找不到音檔")); continue
            rel = f"listen/{year}-{n:02d}.mp3"
            dst = os.path.join(audio_dir, f"{year}-{n:02d}.mp3")
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "22050", "-b:a", "48k", dst], check=True)
            bank.append({"id": f"{year}-L{n:02d}", "year": year, "no": n, "part": it["part"], "options": opts, "answer": ans[n], "audio": rel})
            seen[tuple(opts)] = f"{year}-L{n:02d}"
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
