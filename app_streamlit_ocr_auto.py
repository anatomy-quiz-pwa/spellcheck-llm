
import streamlit as st
import pandas as pd
import re, io, os, json
from pypdf import PdfReader

# ---- Optional deps ----
try:
    from pdf2image import convert_from_bytes
    import pytesseract
    HAVE_OCR = True
except Exception:
    HAVE_OCR = False

try:
    from rapidfuzz import process, fuzz
    HAVE_RF = True
except Exception:
    HAVE_RF = False

try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAVE_GS = True
except Exception:
    HAVE_GS = False

# ---- Text utils ----
ZERO_WIDTH = "".join(["\u200b","\u200c","\u200d","\ufeff","\u00ad"])
def normalize_text(s: str) -> str:
    if not s: return ""
    s = (s.replace('（','(').replace('）',')')
           .replace('；',';').replace('，',',')
           .replace('。','.').replace('、','/'))
    for ch in ZERO_WIDTH: s = s.replace(ch,'')
    s = re.sub(r'\s+', ' ', s)
    return s

def is_cjk(ch: str) -> bool:
    return '\u4e00'<=ch<='\u9fff' or '\u3400'<=ch<='\u4dbf' or '\uf900'<=ch<='\ufaff'

def cjk_pat(tok: str) -> str:
    out=[]
    for ch in tok.strip():
        if ch.isspace(): out.append(r'\s+')
        elif is_cjk(ch): out.append(re.escape(ch)+r'\s*')
        elif ch=='-': out.append(r'\s*-\s*')
        else: out.append(re.escape(ch))
    return "".join(out)

def en_tokens(text: str):
    return re.findall(r"[A-Za-z][A-Za-z0-9\-]*(?:\s+[A-Za-z][A-Za-z0-9\-]*){0,3}", text)

def cjk_tokens(text: str, min_len=3, max_len=8):
    toks = "".join([ch for ch in text if is_cjk(ch)])
    out=set(); n=len(toks)
    for L in range(min_len, min(max_len+1, n+1)):
        for i in range(0, n-L+1):
            out.add(toks[i:i+L])
    return list(out)

# ---- Termbase schema ----
REQUIRED_COLS = ["en_canonical","zh_canonical","abbr","first_mention_style","variant (錯誤用法)"]
def standardize_master(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=REQUIRED_COLS)
    for c in REQUIRED_COLS:
        if c not in df.columns: df[c] = ""
        df[c] = df[c].astype(str).fillna("").str.strip()
    df.loc[df["first_mention_style"]=="","first_mention_style"] = "ZH(EN;ABBR)"
    df = df.drop_duplicates(subset=["en_canonical","zh_canonical","abbr"])
    return df[REQUIRED_COLS]

# ---- PDF extraction ----
def parse_pdf_pairs(pdf_bytes: bytes) -> pd.DataFrame:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text_all = []
    for p in reader.pages:
        try: raw = p.extract_text() or ""
        except Exception: raw = ""
        text_all.append(normalize_text(raw))
    full = "\n".join(text_all)

    ZH = r"[一-龥]{2,30}"
    EN = r"[A-Za-z][A-Za-z0-9\-\s]{1,80}"
    ABBR = r"[A-Za-z][A-Za-z0-9\-]{1,10}"

    pairs=[]
    pat1 = re.compile(rf"(?P<zh>{ZH})\s*[\(（]\s*(?P<en>{EN})(?:\s*;\s*|；\s*)(?P<abbr>{ABBR})\s*[\)）]")
    pat2 = re.compile(rf"(?P<zh>{ZH})\s*[\(（]\s*(?P<en>{EN})\s*[\)）]")
    pat3 = re.compile(rf"(?P<en>{EN})\s*[\(（]\s*(?P<zh>{ZH})\s*[\)）]")

    for m in pat1.finditer(full):
        zh = m.group("zh").strip()
        en = re.sub(r"\s+"," ", m.group("en").strip())
        abbr = m.group("abbr").strip()
        pairs.append((en, zh, abbr, "ZH(EN;ABBR)"))
    for m in pat2.finditer(full):
        zh = m.group("zh").strip()
        en = re.sub(r"\s+"," ", m.group("en").strip())
        pairs.append((en, zh, "", "ZH(EN)"))
    for m in pat3.finditer(full):
        en = re.sub(r"\s+"," ", m.group("en").strip())
        zh = m.group("zh").strip()
        pairs.append((en, zh, "", "ZH(EN)"))

    df = pd.DataFrame(pairs, columns=REQUIRED_COLS[:-1])
    if df.empty:
        df = pd.DataFrame(columns=REQUIRED_COLS[:-1])
    df["variant (錯誤用法)"] = ""
    df = df.drop_duplicates(subset=["en_canonical","zh_canonical","abbr"])
    return df

# ---- Local CSV backend ----
LOCAL_PATH = "termbase_master.csv"
def load_local_master() -> pd.DataFrame:
    if os.path.exists(LOCAL_PATH):
        try: return pd.read_csv(LOCAL_PATH)
        except Exception: return pd.DataFrame()
    return pd.DataFrame()

def save_local_master(df: pd.DataFrame):
    df.to_csv(LOCAL_PATH, index=False, encoding="utf-8-sig")

# ---- Google Sheets backend ----
def extract_sheet_id(url_or_id: str) -> str:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()

def open_worksheet(creds_json: dict, url_or_id: str, ws_name: str):
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(extract_sheet_id(url_or_id))
    try:
        ws = sh.worksheet(ws_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=ws_name, rows=1000, cols=10)
        ws.update([REQUIRED_COLS])
    return ws

def read_master_from_ws(ws) -> pd.DataFrame:
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame(columns=REQUIRED_COLS)
    df = pd.DataFrame(values[1:], columns=values[0])
    return standardize_master(df)

def write_master_to_ws(ws, df: pd.DataFrame):
    df = standardize_master(df)
    values = [df.columns.tolist()] + df.values.tolist()
    ws.clear()
    ws.update(values)

# ---- Streamlit App ----
st.set_page_config(page_title="OCR + 未知詞 + Google Sheets 詞庫", layout="wide")
st.title("OCR + 未知詞 + Google Sheets 詞庫（PDF → 詞庫，自動/半自動）")

with st.sidebar:
    st.subheader("Google Sheets 後端")
    use_gs = st.toggle("使用 Google Sheets", value=False)
    sheet_url = st.text_input("Sheet URL 或 ID", value="", disabled=not use_gs)
    ws_name = st.text_input("工作表名稱", value="termbase_master", disabled=not use_gs)
    creds_file = st.file_uploader("上傳 service account JSON", type=["json"], disabled=not use_gs)

    st.subheader("OCR 選項")
    ocr_enabled = st.toggle("啟用 OCR 後備", value=False)
    ocr_thresh = st.slider("OCR 觸發：抽取字元少於", 0, 200, 5, disabled=not ocr_enabled)
    ocr_lang = st.text_input("OCR 語言", value="chi_tra+eng", disabled=not ocr_enabled)

    st.subheader("未知詞偵測")
    zh_ngram_len = st.slider("中文 n-gram 長度", 3, 8, 4)
    zh_thresh = st.slider("中文相似度門檻（%）", 70, 100, 86)
    en_thresh = st.slider("英文相似度門檻（%）", 70, 100, 88)

    autosave_pairs = st.toggle("自動寫入新對照（跳過審核）", value=False)

# Load master
if use_gs:
    if not HAVE_GS:
        st.error("此環境尚未安裝 gspread / google-auth。請先安裝：pip install gspread google-auth")
        st.stop()
    if not (sheet_url and creds_file):
        st.info("請在側邊欄提供 Sheet URL/ID 與 JSON 憑證。")
        master_df = standardize_master(load_local_master())
        ws = None
    else:
        try:
            creds_json = json.load(creds_file)
            ws = open_worksheet(creds_json, sheet_url, ws_name)
            master_df = read_master_from_ws(ws)
            st.success(f"已連線 Google Sheet：{ws.spreadsheet.title} / {ws.title}")
        except Exception as e:
            st.error(f"連線 Google Sheets 失敗：{e}")
            ws = None
            master_df = standardize_master(load_local_master())
else:
    ws = None
    master_df = standardize_master(load_local_master())

st.write(f"目前詞庫條目：{len(master_df)}")
st.dataframe(master_df.head(50), use_container_width=True)

# Upload PDF
pdf_file = st.file_uploader("上傳投影片 PDF", type=["pdf"])

if pdf_file:
    pdf_bytes = pdf_file.read()

    # Extract all text per page (with optional OCR)
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages_text = []
    for pno, page in enumerate(reader.pages, start=1):
        try: raw = page.extract_text() or ""
        except Exception: raw = ""
        norm = normalize_text(raw)
        if ocr_enabled and len(norm) < ocr_thresh and HAVE_OCR:
            try:
                images = convert_from_bytes(pdf_bytes, first_page=pno, last_page=pno, fmt="png")
                if images:
                    txt = pytesseract.image_to_string(images[0], lang=ocr_lang)
                    norm = normalize_text(txt)
            except Exception:
                pass
        pages_text.append(norm)
    full_text = "\n".join(pages_text)

    # 1) Extract bilingual pairs
    extracted_df = parse_pdf_pairs(pdf_bytes)
    st.subheader("擷取到的中英對照")
    st.dataframe(extracted_df, use_container_width=True)

    merged = extracted_df.merge(master_df[["en_canonical","zh_canonical","abbr"]],
                                on=["en_canonical","zh_canonical","abbr"],
                                how="left", indicator=True)
    new_pairs = merged[merged["_merge"]=="left_only"][extracted_df.columns]
    st.subheader(f"新增對照（不在詞庫中）：{len(new_pairs)}")
    st.dataframe(new_pairs, use_container_width=True)

    # autosave or manual approve
    if autosave_pairs and not new_pairs.empty:
        combined = pd.concat([master_df, new_pairs], ignore_index=True)
        combined = standardize_master(combined)
        if ws is not None: write_master_to_ws(ws, combined)
        else: save_local_master(combined)
        st.success(f"已自動寫入新對照，共 {len(new_pairs)} 條。")
    elif not autosave_pairs:
        if not new_pairs.empty:
            selectable = new_pairs.copy()
            selectable.insert(0, "add", False)
            edited = st.data_editor(selectable, use_container_width=True, num_rows="dynamic")
            to_add = edited[edited["add"]==True].drop(columns=["add"])
        else:
            to_add = pd.DataFrame(columns=new_pairs.columns)
        if not to_add.empty and st.button("寫入選取對照"):
            combined = pd.concat([master_df, to_add], ignore_index=True)
            combined = standardize_master(combined)
            if ws is not None: write_master_to_ws(ws, combined)
            else: save_local_master(combined)
            st.success(f"已寫入 {len(to_add)} 條。")

    # 2) Unknown term detection
    st.subheader("未知詞偵測（需人工配對後加入）")
    known_ens = set(master_df["en_canonical"].str.lower().tolist() + master_df["abbr"].str.lower().tolist())
    known_zhs = set(master_df["zh_canonical"].tolist())

    en_cands = []
    for tok in set(en_tokens(full_text)):
        t = tok.strip()
        if not t: continue
        if t.lower() in known_ens: continue
        suggest=""; score=0
        if HAVE_RF and len(master_df)>0:
            pool = master_df["en_canonical"].tolist() + master_df["abbr"].tolist()
            res = process.extractOne(t, pool, scorer=fuzz.WRatio)
            if res: suggest, score = res[0], float(res[1])
        if not HAVE_RF and len(t) < 4: continue
        if score >= en_thresh or (not HAVE_RF):
            en_cands.append({"type":"UNKNOWN_EN","candidate":t,"suggest":suggest,"score":round(score,1)})

    zh_cands = []
    for gram in set(cjk_tokens(full_text, zh_ngram_len, zh_ngram_len)):
        if gram in known_zhs: continue
        suggest=""; score=0
        if HAVE_RF and len(master_df)>0:
            res = process.extractOne(gram, master_df["zh_canonical"].tolist(), scorer=fuzz.WRatio)
            if res: suggest, score = res[0], float(res[1])
        if score >= zh_thresh or (not HAVE_RF):
            zh_cands.append({"type":"UNKNOWN_ZH","candidate":gram,"suggest":suggest,"score":round(score,1)})

    unk_df = pd.DataFrame(en_cands + zh_cands).sort_values(["type","score"], ascending=[True, False])
    st.dataframe(unk_df, use_container_width=True) if not unk_df.empty else st.info("未偵測到未知詞。")

    # Approval editor to add unknown terms
    if not unk_df.empty:
        st.write("✅ 勾選並補齊對應中英文後加入詞庫：")
        rows=[]
        for _, r in unk_df.iterrows():
            if r["type"]=="UNKNOWN_EN":
                rows.append({"add":False, "en_canonical":r["candidate"], "zh_canonical":r.get("suggest",""), "abbr":"", "first_mention_style":"ZH(EN;ABBR)", "variant (錯誤用法)":""})
            else:
                rows.append({"add":False, "en_canonical":"", "zh_canonical":r["candidate"], "abbr":"", "first_mention_style":"ZH(EN;ABBR)", "variant (錯誤用法)":""})
        editable = pd.DataFrame(rows)
        approved = st.data_editor(editable, use_container_width=True, num_rows="dynamic")
        to_write = approved[(approved["add"]==True) & (approved["en_canonical"].astype(str)!="") & (approved["zh_canonical"].astype(str)!="")].drop(columns=["add"])
        if not to_write.empty and st.button("寫入核准的未知詞"):
            combined = pd.concat([master_df, to_write], ignore_index=True)
            combined = standardize_master(combined)
            if ws is not None: write_master_to_ws(ws, combined)
            else: save_local_master(combined)
            st.success(f"已寫入 {len(to_write)} 條新詞至詞庫。")

# Footer
st.write("---")
st.caption("提示：若使用 Google Sheets，請先於試算表中將 Service Account email 加為編輯者。建議安裝 rapidfuzz 以獲得更準確的未知詞建議。")
