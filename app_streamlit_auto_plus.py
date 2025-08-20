
import streamlit as st
import pandas as pd
import re, io, os
from pypdf import PdfReader

# Optional fuzzy matcher for unknown terms
try:
    from rapidfuzz import process, fuzz
    HAVE_RF = True
except Exception:
    HAVE_RF = False

ZERO_WIDTH = "".join(["\u200b","\u200c","\u200d","\ufeff","\u00ad"])

def normalize_text(s: str) -> str:
    if not s: return ""
    s = (s.replace('（','(').replace('）',')')
           .replace('；',';').replace('，',',')
           .replace('。','.').replace('、','/'))
    for ch in ZERO_WIDTH: s = s.replace(ch,'')
    s = re.sub(r'\s+', ' ', s)
    return s

def load_master(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

def standardize_master(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["en_canonical","zh_canonical","abbr","first_mention_style","variant (錯誤用法)"]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    for c in ["en_canonical","zh_canonical","abbr","first_mention_style","variant (錯誤用法)"]:
        df[c] = df[c].astype(str).fillna("").str.strip()
    df.loc[df["first_mention_style"]=="","first_mention_style"] = "ZH(EN;ABBR)"
    df = df.drop_duplicates(subset=["en_canonical","zh_canonical","abbr"])
    return df[cols]

def parse_pdf_pairs(pdf_bytes: bytes) -> pd.DataFrame:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text_all = []
    for p in reader.pages:
        try:
            raw = p.extract_text() or ""
        except Exception:
            raw = ""
        text_all.append(normalize_text(raw))
    full = "\n".join(text_all)

    ZH = r"[一-龥]{2,30}"
    EN = r"[A-Za-z][A-Za-z0-9\-\s]{1,80}"
    ABBR = r"[A-Za-z][A-Za-z0-9\-]{1,10}"

    pairs = []
    pat1 = re.compile(rf"(?P<zh>{ZH})\s*[\(（]\s*(?P<en>{EN})(?:\s*;\s*|；\s*)(?P<abbr>{ABBR})\s*[\)）]")
    pat2 = re.compile(rf"(?P<zh>{ZH})\s*[\(（]\s*(?P<en>{EN})\s*[\)）]")
    pat3 = re.compile(rf"(?P<en>{EN})\s*[\(（]\s*(?P<zh>{ZH})\s*[\)）]")

    for m in pat1.finditer(full):
        zh = m.group("zh").strip()
        en = re.sub(r"\s+", " ", m.group("en").strip())
        abbr = m.group("abbr").strip()
        pairs.append((en, zh, abbr, "ZH(EN;ABBR)"))
    for m in pat2.finditer(full):
        zh = m.group("zh").strip()
        en = re.sub(r"\s+", " ", m.group("en").strip())
        pairs.append((en, zh, "", "ZH(EN)"))
    for m in pat3.finditer(full):
        en = re.sub(r"\s+", " ", m.group("en").strip())
        zh = m.group("zh").strip()
        pairs.append((en, zh, "", "ZH(EN)"))

    df = pd.DataFrame(pairs, columns=["en_canonical","zh_canonical","abbr","first_mention_style"])
    if df.empty:
        df = pd.DataFrame(columns=["en_canonical","zh_canonical","abbr","first_mention_style"])
    df["variant (錯誤用法)"] = ""
    df = df.drop_duplicates(subset=["en_canonical","zh_canonical","abbr"])
    return df

def cjk_tokens(text: str, min_len=3, max_len=8):
    toks = "".join([ch for ch in text if '\u4e00'<=ch<='\u9fff'])
    out = set()
    n = len(toks)
    for L in range(min_len, min(max_len+1, n+1)):
        for i in range(0, n-L+1):
            out.add(toks[i:i+L])
    return list(out)

def en_tokens(text: str):
    # up to 4-word phrases
    return re.findall(r"[A-Za-z][A-Za-z0-9\-]*(?:\s+[A-Za-z][A-Za-z0-9\-]*){0,3}", text)

# ---------------- Streamlit App ----------------
st.set_page_config(page_title="自動建立詞庫 + 未知詞偵測", layout="wide")
st.title("自動建立詞庫 + 未知詞偵測（PDF → 詞庫）")
st.caption("從 PDF 自動擷取 **中文↔英文（含縮寫）** 對照，與現有詞庫合併，並偵測 **未知英文/中文術語** 供你一鍵加入。")

MASTER_PATH = "termbase_master.csv"

with st.sidebar:
    st.subheader("未知詞偵測設定")
    zh_ngram_len = st.slider("中文 n-gram 長度（3–8）", 3, 8, 4)
    zh_thresh = st.slider("中文相似度門檻（%）", 70, 100, 86, help="需安裝 rapidfuzz 才有最佳效果")
    en_thresh = st.slider("英文相似度門檻（%）", 70, 100, 88)
    st.caption("提示：未安裝 rapidfuzz 時改用簡易相似度，可能較保守。")

# Load/Upload master
master_df = load_master(MASTER_PATH)
uploaded_master = st.file_uploader("（可選）上傳初始/更新詞庫 CSV", type=["csv"])
if uploaded_master:
    try:
        master_df = pd.read_csv(uploaded_master)
        st.success("已載入你上傳的詞庫，將與自動擷取結果合併。")
    except Exception as e:
        st.error(f"讀取失敗：{e}")

master_df = standardize_master(master_df)
st.write(f"目前詞庫條目：{len(master_df)}")
st.dataframe(master_df.head(50), use_container_width=True)

pdf_file = st.file_uploader("上傳投影片 PDF（自動抽取術語 + 未知詞偵測）", type=["pdf"])

if pdf_file:
    pdf_bytes = pdf_file.read()

    # 1) 擷取中英對照
    extracted_df = parse_pdf_pairs(pdf_bytes)
    st.subheader("從 PDF 擷取到的候選對照")
    if extracted_df.empty:
        st.info("未偵測到中英對照樣式。")
    else:
        st.dataframe(extracted_df, use_container_width=True)

    # 合併、找新條目
    merged = extracted_df.merge(master_df[["en_canonical","zh_canonical","abbr"]],
                                on=["en_canonical","zh_canonical","abbr"],
                                how="left", indicator=True)
    new_rows = merged[merged["_merge"]=="left_only"][extracted_df.columns]
    st.subheader(f"新增條目（不在現有詞庫中）：{len(new_rows)}")
    st.dataframe(new_rows, use_container_width=True)

    # 2) 未知詞偵測
    st.subheader("未知詞偵測")
    # concatenate all text for tokenization
    reader = PdfReader(io.BytesIO(pdf_bytes))
    texts = []
    for p in reader.pages:
        try:
            raw = p.extract_text() or ""
        except Exception:
            raw = ""
        texts.append(normalize_text(raw))
    full_text = "\n".join(texts)

    # Known sets
    known_ens = set(master_df["en_canonical"].str.lower().tolist() + master_df["abbr"].str.lower().tolist())
    known_zhs = set(master_df["zh_canonical"].tolist())

    # English unknowns
    en_cands = []
    for tok in set(en_tokens(full_text)):
        t = tok.strip()
        if not t: continue
        if t.lower() in known_ens: continue
        # fuzzy to nearest en in master
        if HAVE_RF and len(master_df) > 0:
            match = process.extractOne(t, master_df["en_canonical"].tolist() + master_df["abbr"].tolist(), scorer=fuzz.WRatio)
            if match and match[1] >= en_thresh:
                en_cands.append({"type":"UNKNOWN_EN","candidate":t,"suggest":match[0],"score":round(float(match[1]),1)})
        else:
            # simple heuristic: length>=4
            if len(t) >= 4:
                en_cands.append({"type":"UNKNOWN_EN","candidate":t,"suggest":"","score":0})

    # Chinese unknowns
    zh_cands = []
    for gram in set(cjk_tokens(full_text, zh_ngram_len, zh_ngram_len)):
        if gram in known_zhs: continue
        if HAVE_RF and len(master_df) > 0:
            match = process.extractOne(gram, master_df["zh_canonical"].tolist(), scorer=fuzz.WRatio)
            if match and match[1] >= zh_thresh:
                zh_cands.append({"type":"UNKNOWN_ZH","candidate":gram,"suggest":match[0],"score":round(float(match[1]),1)})
        else:
            # simple heuristic: treat as candidate directly
            zh_cands.append({"type":"UNKNOWN_ZH","candidate":gram,"suggest":"","score":0})

    unk_df = pd.DataFrame(en_cands + zh_cands)
    if unk_df.empty:
        st.info("本次未偵測到疑似未知詞。")
    else:
        st.dataframe(unk_df.sort_values(["type","score"], ascending=[True, False]), use_container_width=True)

        # 構建加入詞庫的表單（需人工填正確對應）
        st.write("✅ 勾選並補齊對應中英文後，加入詞庫：")
        to_add = pd.DataFrame(columns=["en_canonical","zh_canonical","abbr","first_mention_style","variant (錯誤用法)"])
        editor_rows = []
        for _, r in unk_df.iterrows():
            if r["type"] == "UNKNOWN_EN":
                editor_rows.append({"add": False, "en_canonical": r["candidate"], "zh_canonical": r.get("suggest",""), "abbr":"", "first_mention_style":"ZH(EN;ABBR)", "variant (錯誤用法)":""})
            else:
                editor_rows.append({"add": False, "en_canonical": "", "zh_canonical": r["candidate"], "abbr":"", "first_mention_style":"ZH(EN;ABBR)", "variant (錯誤用法)":""})
        if editor_rows:
            editable = pd.DataFrame(editor_rows)
            edited = st.data_editor(editable, use_container_width=True, num_rows="dynamic")
            approved = edited[(edited["add"]==True) & (edited["zh_canonical"].astype(str)!="") & (edited["en_canonical"].astype(str)!="")].drop(columns=["add"])

            col1, col2 = st.columns(2)
            with col1:
                if not approved.empty and st.button("儲存加入詞庫（termbase_master.csv）", type="primary"):
                    combined = pd.concat([master_df, approved], ignore_index=True)
                    combined = standardize_master(combined)
                    combined.to_csv(MASTER_PATH, index=False, encoding="utf-8-sig")
                    st.success(f"已寫入 {MASTER_PATH}（共 {len(combined)} 條）。")

            with col2:
                if not approved.empty:
                    st.download_button("⬇️ 下載這次核准的新詞條 CSV", approved.to_csv(index=False).encode("utf-8-sig"),
                                       file_name="approved_new_terms.csv", mime="text/csv")

# Footer
st.write("---")
st.caption("小提示：為了更準確的未知詞建議，建議安裝 `rapidfuzz`： `pip install rapidfuzz`")
