import streamlit as st
import pandas as pd
import re, io
from pypdf import PdfReader

# OCR deps
try:
    from pdf2image import convert_from_bytes
    import pytesseract
    HAVE_OCR = True
except Exception:
    HAVE_OCR = False

# ===== Header mapping (tolerant) =====
FULLWIDTH_PAREN_MAP={'（':'(', '）':')'}
def normalize_header(h): 
    if not isinstance(h,str): h=str(h)
    h=h.strip()
    for k,v in FULLWIDTH_PAREN_MAP.items(): h=h.replace(k,v)
    return re.sub(r'\s+',' ',h).lower()

ALIASES={
 "en_canonical":["en_canonical","english","en","term_en","canonical_en","英文","標準英文"],
 "zh_canonical":["zh_canonical","chinese","zh","term_zh","canonical_zh","中文","繁體中文","標準中文"],
 "abbr":["abbr","abbreviation","縮寫","縮略"],
 "first_mention_style":["first_mention_style","style","首次顯示規則","first-mention","fmt"],
 "variant (錯誤用法)":["variant (錯誤用法)","variant","variants","錯誤用法","錯字","typo","misspelling"]
}
def map_headers(df: pd.DataFrame):
    found={normalize_header(c):c for c in df.columns}
    mapping={}
    for req,alist in ALIASES.items():
        for a in alist:
            key=normalize_header(a)
            if key in found: mapping[req]=found[key]; break
    out=pd.DataFrame(); warns=[]
    for col in ["en_canonical","zh_canonical","variant (錯誤用法)"]:
        if col in mapping: out[col]=df[mapping[col]].astype(str)
        else: out[col]=""; warns.append(f"缺少必要欄位：{col}")
    out["abbr"]=df[mapping["abbr"]].astype(str) if "abbr" in mapping else ""
    out["first_mention_style"]=df[mapping["first_mention_style"]].astype(str) if "first_mention_style" in mapping else "ZH(EN;ABBR)"
    return out,mapping,warns

# ===== Matching utils =====
ZERO_WIDTH="".join(["\u200b","\u200c","\u200d","\ufeff","\u00ad"])
def normalize_text(s:str)->str:
    if not s: return ""
    s=(s.replace('（','(').replace('）',')')
         .replace('；',';').replace('，',',')
         .replace('。','.').replace('、','/'))
    for ch in ZERO_WIDTH: s=s.replace(ch,'')
    s=re.sub(r'\s+',' ',s)
    return s

def is_cjk(ch:str)->bool:
    return '\u4e00'<=ch<='\u9fff' or '\u3400'<=ch<='\u4dbf' or '\uf900'<=ch<='\ufaff'

def cjk_pat(tok:str)->str:
    out=[]
    for ch in tok.strip():
        if ch.isspace(): out.append(r'\s+')
        elif is_cjk(ch): out.append(re.escape(ch)+r'\s*')
        elif ch=='-': out.append(r'\s*-\s*')
        else: out.append(re.escape(ch))
    return "".join(out)

def split_variants(cell:str):
    if not isinstance(cell,str): return []
    parts=re.split(r'[,，;/、]+', cell.strip())
    return [p for p in parts if p]

# ===== Streamlit UI =====
st.set_page_config(page_title="醫學投影片 PDF 錯字掃描（含 OCR）", layout="wide")
st.title("醫學投影片 PDF 錯字掃描（含 OCR）")
st.caption("支援彈性 CSV 抬頭 + CJK 智慧比對；可選擇對抽不到文字的頁面進行 OCR（圖片式 PDF）。")

with st.sidebar:
    st.subheader("OCR 選項")
    ocr_enabled = st.toggle("啟用 OCR 後備", value=False, help="對抽不到文字或字元數低於門檻的頁面使用 OCR。")
    ocr_thresh = st.slider("OCR 觸發門檻：抽取字元少於", 0, 200, 5, disabled=not ocr_enabled)
    ocr_lang = st.text_input("Tesseract 語言（lang）", value="chi_tra+eng", help="繁中建議 chi_tra+eng；若無法使用，請安裝 tesseract 語言包。", disabled=not ocr_enabled)

csv_file = st.file_uploader("上傳一表 CSV（欄位名可彈性）", type=["csv"])
pdf_file = st.file_uploader("上傳投影片 PDF", type=["pdf"])

if csv_file and pdf_file:
    raw = pd.read_csv(csv_file)
    df, mapping, warnings = map_headers(raw)
    with st.expander("CSV 欄位對應與提醒"):
        st.json(mapping)
        if warnings: st.warning("；".join(warnings))

    # Build patterns
    pats=[]
    for _,row in df.iterrows():
        for v in split_variants(row["variant (錯誤用法)"]):
            pats.append((re.compile(cjk_pat(v), flags=re.I), v, row))

    # Read PDF bytes once (for OCR if needed)
    pdf_bytes = pdf_file.read()
    reader = PdfReader(io.BytesIO(pdf_bytes))

    hits=[]; stats=[]
    for pno, page in enumerate(reader.pages, start=1):
        try: raw_txt = page.extract_text() or ""
        except Exception: raw_txt = ""
        norm = normalize_text(raw_txt)
        chars = len(norm)
        used_ocr = False
        # OCR fallback
        if ocr_enabled and HAVE_OCR and chars < ocr_thresh:
            try:
                images = convert_from_bytes(pdf_bytes, first_page=pno, last_page=pno, fmt="png")
                if images:
                    txt = pytesseract.image_to_string(images[0], lang=ocr_lang)
                    norm = normalize_text(txt)
                    chars = len(norm)
                    used_ocr = True
            except Exception as e:
                st.error(f"OCR 失敗（第 {pno} 頁）：{e}")

        stats.append({"page": pno, "chars": chars, "ocr_used": used_ocr})
        for rx, v, row in pats:
            for m in rx.finditer(norm):
                hits.append({
                    "page": pno,
                    "variant": v,
                    "zh_canonical": row["zh_canonical"],
                    "en_canonical": row["en_canonical"],
                    "abbr": row.get("abbr",""),
                    "first_mention_style": row.get("first_mention_style",""),
                    "context": norm[max(0,m.start()-60):m.end()+60],
                    "ocr": used_ocr
                })

    st.success(f"掃描完成，命中 {len(hits)} 個")
    st.dataframe(pd.DataFrame(hits)) if hits else st.info("未發現錯誤。")

    st.subheader("頁面抽取統計")
    st.dataframe(pd.DataFrame(stats))
    if ocr_enabled and not HAVE_OCR:
        st.warning("目前環境未安裝 OCR 相依套件（pdf2image 或 pytesseract）。請參考下方安裝說明。")

else:
    st.info("請先上傳 CSV 與 PDF。")

# ===== 安裝說明 =====
st.write("---")
st.subheader("安裝說明（macOS）")
st.markdown("""
1. 安裝外部工具（一次）
   ```bash
   brew install poppler tesseract tesseract-lang
   ```

2. 在你的虛擬環境安裝 Python 套件
   ```bash
   pip install pdf2image pytesseract pillow
   ```

3. 常用語言參數：
   - 繁體中文 + 英文：`chi_tra+eng`
   - 簡體中文 + 英文：`chi_sim+eng`
   - 僅英文：`eng`
""")
