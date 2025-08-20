
import streamlit as st
import pandas as pd
import re, io, os, json
from pypdf import PdfReader

# Optional: Google Sheets backend
USE_GSHEETS = False
try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAVE_GS = True
except Exception:
    HAVE_GS = False

# ------------- Text utils -------------
ZERO_WIDTH="".join(["\u200b","\u200c","\u200d","\ufeff","\u00ad"])
def normalize_text(s: str) -> str:
    if not s: return ""
    s=(s.replace('（','(').replace('）',')')
         .replace('；',';').replace('，',',')
         .replace('。','.').replace('、','/'))
    for ch in ZERO_WIDTH: s=s.replace(ch,'')
    s=re.sub(r'\s+',' ',s)
    return s

# ------------- Termbase schema helpers -------------
REQUIRED_COLS = ["en_canonical","zh_canonical","abbr","first_mention_style","variant (錯誤用法)"]

def standardize_master(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=REQUIRED_COLS)
    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = ""
    for c in REQUIRED_COLS:
        df[c] = df[c].astype(str).fillna("").str.strip()
    df.loc[df["first_mention_style"]=="","first_mention_style"] = "ZH(EN;ABBR)"
    df = df.drop_duplicates(subset=["en_canonical","zh_canonical","abbr"])
    return df[REQUIRED_COLS]

# ------------- PDF bilingual pair extraction -------------
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

    df = pd.DataFrame(pairs, columns=REQUIRED_COLS[:-1])  # without variant
    if df.empty:
        df = pd.DataFrame(columns=REQUIRED_COLS[:-1])
    df["variant (錯誤用法)"] = ""
    df = df.drop_duplicates(subset=["en_canonical","zh_canonical","abbr"])
    return df

# ------------- Local CSV backend -------------
LOCAL_PATH = "termbase_master.csv"
def load_local_master() -> pd.DataFrame:
    if os.path.exists(LOCAL_PATH):
        try: return pd.read_csv(LOCAL_PATH)
        except Exception: return pd.DataFrame()
    return pd.DataFrame()

def save_local_master(df: pd.DataFrame):
    df.to_csv(LOCAL_PATH, index=False, encoding="utf-8-sig")

# ------------- Google Sheets backend -------------
def extract_sheet_id(url_or_id: str) -> str:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()

def open_worksheet(creds_dict: dict, url_or_id: str, worksheet_name: str):
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(extract_sheet_id(url_or_id))
    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet_name, rows=1000, cols=10)
        ws.update([REQUIRED_COLS])  # header
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

# ------------- Streamlit App -------------
st.set_page_config(page_title="Google Sheets 詞庫後端", layout="wide")
st.title("Google Sheets 詞庫後端（PDF → 詞庫，自動寫入）")
st.caption("可選擇使用 **Google Sheets** 作為 `termbase_master` 的儲存後端；若未設定，預設寫入本地 CSV。")

with st.sidebar:
    use_gs = st.toggle("使用 Google Sheets 後端", value=False, help="開啟後，上傳 service account JSON，並填入 Sheet URL/ID 與工作表名稱。")
    sheet_url = st.text_input("Google Sheet URL 或 ID", value="", disabled=not use_gs)
    ws_name = st.text_input("工作表名稱", value="termbase_master", disabled=not use_gs)
    creds_file = st.file_uploader("上傳 service account JSON", type=["json"], disabled=not use_gs)

# Load master
if use_gs:
    if not HAVE_GS:
        st.error("此環境尚未安裝 gspread / google-auth。請先安裝：`pip install gspread google-auth`")
        st.stop()
    if not (sheet_url and creds_file):
        st.info("請在側邊欄提供 Google Sheet URL/ID 與憑證 JSON 後再繼續。")
        master_df = pd.DataFrame(columns=REQUIRED_COLS)
        ws = None
    else:
        try:
            creds_dict = json.load(creds_file)
            ws = open_worksheet(creds_dict, sheet_url, ws_name)
            master_df = read_master_from_ws(ws)
            st.success(f"已連線到工作表：{ws.spreadsheet.title} / {ws.title}")
        except Exception as e:
            st.error(f"連線 Google Sheets 失敗：{e}")
            ws = None
            master_df = standardize_master(load_local_master())
else:
    ws = None
    master_df = standardize_master(load_local_master())

st.write(f"目前詞庫條目：{len(master_df)}")
st.dataframe(master_df.head(50), use_container_width=True)

# PDF Upload
pdf_file = st.file_uploader("上傳投影片 PDF（自動擷取中英對照）", type=["pdf"])
autosave = st.toggle("自動寫入新對照（跳過審核）", value=False)

if pdf_file:
    pdf_bytes = pdf_file.read()
    extracted_df = parse_pdf_pairs(pdf_bytes)
    st.subheader("從 PDF 擷取到的候選對照")
    st.dataframe(extracted_df, use_container_width=True)

    merged = extracted_df.merge(master_df[["en_canonical","zh_canonical","abbr"]],
                                on=["en_canonical","zh_canonical","abbr"],
                                how="left", indicator=True)
    new_rows = merged[merged["_merge"]=="left_only"][extracted_df.columns]
    st.subheader(f"新增條目（不在現有詞庫中）：{len(new_rows)}")
    st.dataframe(new_rows, use_container_width=True)

    if autosave and not new_rows.empty:
        combined = pd.concat([master_df, new_rows], ignore_index=True)
        combined = standardize_master(combined)
        if ws is not None:
            write_master_to_ws(ws, combined)
            st.success(f"已自動寫入 Google Sheet，共 {len(combined)} 條。")
        else:
            save_local_master(combined)
            st.success(f"已自動寫入本地 CSV：{len(combined)} 條。")
    elif not autosave:
        if not new_rows.empty:
            selectable = new_rows.copy()
            selectable.insert(0, "add", False)
            edited = st.data_editor(selectable, use_container_width=True, num_rows="dynamic")
            to_add = edited[edited["add"]==True].drop(columns=["add"])
        else:
            to_add = pd.DataFrame(columns=extracted_df.columns)

        cols = st.columns(2)
        with cols[0]:
            if not to_add.empty and st.button("儲存合併到詞庫", type="primary"):
                combined = pd.concat([master_df, to_add], ignore_index=True)
                combined = standardize_master(combined)
                if ws is not None:
                    write_master_to_ws(ws, combined)
                    st.success(f"已寫入 Google Sheet，共 {len(combined)} 條。")
                else:
                    save_local_master(combined)
                    st.success(f"已寫入本地 CSV，共 {len(combined)} 條。")
        with cols[1]:
            if not new_rows.empty:
                st.download_button("⬇️ 下載新增條目 CSV", new_rows.to_csv(index=False).encode("utf-8-sig"),
                                   file_name="new_terms_from_pdf.csv", mime="text/csv")

st.write("---")
st.markdown("**使用說明**：在 Google Cloud Console 建立 Service Account，啟用 *Google Sheets API*，下載 JSON 憑證，並將目標試算表分享給該 Service Account 的 email。")
