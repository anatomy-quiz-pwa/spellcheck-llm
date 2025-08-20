import streamlit as st, pandas as pd, re
from pypdf import PdfReader

# header mapping
FULLWIDTH_PAREN_MAP={'（':'(', '）':')'}
def normalize_header(h): return re.sub(r'\s+',' ',h.strip().lower()) if isinstance(h,str) else str(h)
ALIASES={
 "en_canonical":["en_canonical","english","en","term_en","英文"],
 "zh_canonical":["zh_canonical","chinese","zh","term_zh","中文"],
 "abbr":["abbr","abbreviation","縮寫"],
 "first_mention_style":["first_mention_style","style","首次顯示規則"],
 "variant (錯誤用法)":["variant (錯誤用法)","variant","variants","錯誤用法","錯字","typo"]
}
def map_headers(df):
    found={normalize_header(c):c for c in df.columns}
    mapping={}
    for req,alist in ALIASES.items():
        for a in alist:
            if normalize_header(a) in found:
                mapping[req]=found[normalize_header(a)]; break
    out=pd.DataFrame();warns=[]
    for col in ["en_canonical","zh_canonical","variant (錯誤用法)"]:
        out[col]=df[mapping[col]].astype(str) if col in mapping else ""
        if col not in mapping: warns.append(f"缺少 {col}")
    out["abbr"]=df[mapping["abbr"]].astype(str) if "abbr" in mapping else ""
    out["first_mention_style"]=df[mapping["first_mention_style"]].astype(str) if "first_mention_style" in mapping else "ZH(EN;ABBR)"
    return out,mapping,warns

# utils
def normalize_text(s):
    if not s: return ""
    return re.sub(r'\s+',' ',s)

def is_cjk(ch): return '\u4e00'<=ch<='\u9fff'
def cjk_pat(tok):
    out=[]; 
    for ch in tok.strip():
        if is_cjk(ch): out.append(re.escape(ch)+r'\s*')
        else: out.append(re.escape(ch))
    return ''.join(out)
def split_variants(c): return [p for p in re.split(r'[,\uFF0C;/、]+',str(c)) if p]

# Streamlit UI
st.title("醫學投影片 PDF 錯字掃描（寬鬆 CSV 抬頭）")
csv=st.file_uploader("上傳一表 CSV",type="csv")
pdf=st.file_uploader("上傳投影片 PDF",type="pdf")
if csv and pdf:
    raw=pd.read_csv(csv)
    df,mapping,warns=map_headers(raw)
    st.write("欄位對應:",mapping)
    if warns: st.warning("；".join(warns))
    # build patterns
    pats=[]
    for _,row in df.iterrows():
        for v in split_variants(row["variant (錯誤用法)"]):
            pats.append((re.compile(cjk_pat(v),flags=re.I),v,row))
    # scan
    reader=PdfReader(pdf);hits=[]
    for pno,page in enumerate(reader.pages,start=1):
        norm=normalize_text(page.extract_text() or "")
        for rx,v,row in pats:
            for m in rx.finditer(norm):
                hits.append({
                    "page":pno,"variant":v,
                    "zh_canonical":row["zh_canonical"],
                    "en_canonical":row["en_canonical"],
                    "abbr":row["abbr"],
                    "first_mention_style":row["first_mention_style"],
                    "context":norm[max(0,m.start()-60):m.end()+60]
                })
    st.success(f"找到 {len(hits)} 筆")
    st.dataframe(pd.DataFrame(hits))
