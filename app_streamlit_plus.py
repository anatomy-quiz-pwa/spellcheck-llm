
import streamlit as st
import pandas as pd
import re, io
from pypdf import PdfReader

try:
    from pdf2image import convert_from_bytes
    import pytesseract
    HAVE_OCR = True
except Exception:
    HAVE_OCR = False

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
    parts=re.split(r'[,\uFF0C;/、]+', cell.strip())
    return [p for p in parts if p]

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

st.set_page_config(page_title="醫學投影片 PDF 錯字掃描（含 OCR / 中英一致性）", layout="wide")
st.title("醫學投影片 PDF 錯字掃描（含 OCR / 中英一致性）")
st.caption("新增：1) 已知錯字比對 2) 中英對照一致性 3) 首次出現格式檢查；支援 OCR 後備。")

with st.sidebar:
    st.subheader("選項")
    ocr_enabled = st.toggle("啟用 OCR 後備", value=False)
    ocr_thresh = st.slider("OCR 觸發：抽取字元少於", 0, 200, 5, disabled=not ocr_enabled)
    ocr_lang = st.text_input("OCR 語言", value="chi_tra+eng", disabled=not ocr_enabled)
    check_pair = st.toggle("啟用中英對照一致性檢查", value=True)
    check_first = st.toggle("啟用首次出現格式檢查", value=True)
    window = st.slider("中英配對搜尋視窗（字數）", 10, 200, 80, 10)

csv_file = st.file_uploader("上傳一表 CSV（欄位名可彈性）", type=["csv"])
pdf_file = st.file_uploader("上傳投影片 PDF", type=["pdf"])

if csv_file and pdf_file:
    raw = pd.read_csv(csv_file)
    term_df, mapping, warnings = map_headers(raw)

    # build regex for variants & canonical terms
    variant_patterns = []
    for _, row in term_df.iterrows():
        for v in split_variants(row["variant (錯誤用法)"]):
            variant_patterns.append((re.compile(cjk_pat(v), flags=re.I), v, row))

    # canonical english patterns (exact word tokens, allow hyphen spaces)
    en_pats = []
    for _, row in term_df.iterrows():
        en = row["en_canonical"].strip()
        if en:
            pat = re.escape(en).replace(r'\-', r'\s*-\s*')
            en_pats.append((re.compile(pat, flags=re.I), row))
    # canonical zh patterns
    zh_pats = []
    for _, row in term_df.iterrows():
        zh = row["zh_canonical"].strip()
        if zh:
            zh_pats.append((re.compile(cjk_pat(zh), flags=re.I), row))

    # read PDF
    pdf_bytes = pdf_file.read()
    reader = PdfReader(io.BytesIO(pdf_bytes))

    known_hits = []
    pair_issues = []
    first_mentions = []
    seen_terms = set()  # track first mention by en term

    for pno, page in enumerate(reader.pages, start=1):
        try: raw_txt = page.extract_text() or ""
        except Exception: raw_txt = ""
        norm = normalize_text(raw_txt)
        # OCR fallback
        used_ocr = False
        if ocr_enabled and len(norm) < ocr_thresh and HAVE_OCR:
            try:
                images = convert_from_bytes(pdf_bytes, first_page=pno, last_page=pno, fmt="png")
                if images:
                    txt = pytesseract.image_to_string(images[0], lang=ocr_lang)
                    norm = normalize_text(txt)
                    used_ocr = True
            except Exception:
                pass

        # 1) known variants
        for rx, v, row in variant_patterns:
            for m in rx.finditer(norm):
                known_hits.append({
                    "type":"KNOWN_VARIANT","page":pno,"variant":v,
                    "zh_canonical":row["zh_canonical"],"en_canonical":row["en_canonical"],
                    "context": norm[max(0,m.start()-60):m.end()+60],"ocr": used_ocr
                })

        # 2) bilingual pairing
        if check_pair or check_first:
            # find all en and zh locations
            en_hits = []
            for rx, row in en_pats:
                for m in rx.finditer(norm):
                    en_hits.append((m.start(), m.end(), row))
            zh_hits = []
            for rx, row in zh_pats:
                for m in rx.finditer(norm):
                    zh_hits.append((m.start(), m.end(), row))

            # check pair proximity
            if check_pair:
                for es, ee, erow in en_hits:
                    # search zh around this en
                    expected_zh = erow["zh_canonical"]
                    found_pair = False
                    for zs, ze, zrow in zh_hits:
                        if abs(zs - es) <= window:
                            # consider paired if zh equals expected
                            if zrow["zh_canonical"] == expected_zh:
                                found_pair = True
                                break
                    if not found_pair:
                        pair_issues.append({
                            "type":"MISSING_OR_WRONG_ZH","page":pno,
                            "en_canonical":erow["en_canonical"],
                            "expected_zh": expected_zh,
                            "context": norm[max(0, es-window): ee+window],
                            "ocr": used_ocr
                        })
                # also check zh that don't have matching en nearby (optional)

            # 3) first mention style
            if check_first:
                for es, ee, erow in en_hits:
                    key = erow["en_canonical"].lower()
                    if key not in seen_terms:
                        seen_terms.add(key)
                        # first mention should include zh(en;abbr) if zh present nearby
                        fmt = erow.get("first_mention_style","ZH(EN;ABBR)")
                        if fmt.upper().startswith("ZH("):
                            # require presence of Chinese within window
                            expected_zh = erow["zh_canonical"]
                            has_zh_near = any(abs(zs - es) <= window and zrow["zh_canonical"]==expected_zh for zs,ze,zrow in zh_hits)
                            if not has_zh_near:
                                first_mentions.append({
                                    "type":"FIRST_MENTION_MISSING_ZH","page":pno,
                                    "en_canonical": erow["en_canonical"],
                                    "expected_zh": expected_zh,
                                    "abbr": erow.get("abbr",""),
                                    "context": norm[max(0, es-window): ee+window],
                                    "ocr": used_ocr
                                })

    st.success(f"掃描完成：已知錯字 {len(known_hits)} 筆；中英對照問題 {len(pair_issues)} 筆；首次出現問題 {len(first_mentions)} 筆")
    tabs = st.tabs(["已知錯字", "中英對照問題", "首次出現格式"])
    with tabs[0]:
        st.dataframe(pd.DataFrame(known_hits)) if known_hits else st.info("未發現已知錯字。")
    with tabs[1]:
        st.dataframe(pd.DataFrame(pair_issues)) if pair_issues else st.info("未發現中英對照問題。")
    with tabs[2]:
        st.dataframe(pd.DataFrame(first_mentions)) if first_mentions else st.info("未發現首次出現格式問題。")

    # download buttons
    if known_hits or pair_issues or first_mentions:
        out = pd.concat([
            pd.DataFrame(known_hits),
            pd.DataFrame(pair_issues),
            pd.DataFrame(first_mentions)
        ], ignore_index=True, sort=False)
        st.download_button("⬇️ 下載綜合報告 CSV", out.to_csv(index=False).encode("utf-8-sig"),
                           file_name="pdf_check_report.csv", mime="text/csv")

else:
    st.info("請先上傳 CSV 與 PDF。")
