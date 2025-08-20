
import streamlit as st
import pandas as pd
import re, io, os, json
from pypdf import PdfReader

# ---- Optional dependencies ----
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

# ---- Text utilities ----
ZERO_WIDTH = "".join(["\u200b","\u200c","\u200d","\ufeff","\u00ad"])
def normalize_text(s: str) -> str:
    if not s: return ""
    s=(s.replace('（','(').replace('）',')')
         .replace('；',';').replace('，',',')
         .replace('。','.').replace('、','/'))
    for ch in ZERO_WIDTH: s=s.replace(ch,'')
    s=re.sub(r'\s+',' ',s)
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
    if not values: return pd.DataFrame(columns=REQUIRED_COLS)
    df = pd.DataFrame(values[1:], columns=values[0])
    return standardize_master(df)

def write_master_to_ws(ws, df: pd.DataFrame):
    df = standardize_master(df)
    values = [df.columns.tolist()] + df.values.tolist()
    ws.clear(); ws.update(values)

# ---- Local CSV fallback ----
LOCAL_PATH = "termbase_master.csv"
def load_local_master() -> pd.DataFrame:
    if os.path.exists(LOCAL_PATH):
        try: return pd.read_csv(LOCAL_PATH)
        except Exception: return pd.DataFrame()
    return pd.DataFrame()
def save_local_master(df: pd.DataFrame):
    df.to_csv(LOCAL_PATH, index=False, encoding="utf-8-sig")

# ---- PDF bilingual extraction ----
def parse_pdf_pairs(full_text: str) -> pd.DataFrame:
    ZH = r"[一-龥]{2,30}"
    EN = r"[A-Za-z][A-Za-z0-9\-\s]{1,80}"
    ABBR = r"[A-Za-z][A-Za-z0-9\-]{1,10}"

    pairs=[]
    pat1 = re.compile(rf"(?P<zh>{ZH})\s*[\(（]\s*(?P<en>{EN})(?:\s*;\s*|；\s*)(?P<abbr>{ABBR})\s*[\)）]")
    pat2 = re.compile(rf"(?P<zh>{ZH})\s*[\(（]\s*(?P<en>{EN})\s*[\)）]")
    pat3 = re.compile(rf"(?P<en>{EN})\s*[\(（]\s*(?P<zh>{ZH})\s*[\)）]")

    for m in pat1.finditer(full_text):
        zh=m.group("zh").strip(); en=re.sub(r"\s+"," ", m.group("en").strip()); abbr=m.group("abbr").strip()
        pairs.append((en, zh, abbr, "ZH(EN;ABBR)"))
    for m in pat2.finditer(full_text):
        zh=m.group("zh").strip(); en=re.sub(r"\s+"," ", m.group("en").strip())
        pairs.append((en, zh, "", "ZH(EN)"))
    for m in pat3.finditer(full_text):
        en=re.sub(r"\s+"," ", m.group("en").strip()); zh=m.group("zh").strip()
        pairs.append((en, zh, "", "ZH(EN)"))

    df = pd.DataFrame(pairs, columns=REQUIRED_COLS[:-1])
    if df.empty: df = pd.DataFrame(columns=REQUIRED_COLS[:-1])
    df["variant (錯誤用法)"] = ""
    df = df.drop_duplicates(subset=["en_canonical","zh_canonical","abbr"])
    return df

# ---- Streamlit UI ----
st.set_page_config(page_title="One-Page 超級版 — PDF 校對 + 詞庫（GSheets/CSV）", layout="wide")
st.title("One-Page 超級版：PDF 錯字掃描 × 中英一致 × 首次格式 × 未知詞 × 詞庫（GSheets/CSV）")

with st.sidebar:
    st.subheader("Google Sheets 後端")
    use_gs = st.toggle("使用 Google Sheets", value=False)
    sheet_url = st.text_input("Sheet URL 或 ID", value="", disabled=not use_gs)
    ws_name = st.text_input("工作表名稱", value="termbase_master", disabled=not use_gs)
    creds_file = st.file_uploader("上傳 service account JSON", type=["json"], disabled=not use_gs)

    st.subheader("OCR")
    ocr_enabled = st.toggle("啟用 OCR 後備", value=False)
    ocr_thresh = st.slider("OCR 觸發：抽取字元少於", 0, 200, 5, disabled=not ocr_enabled)
    ocr_lang = st.text_input("OCR 語言", value="chi_tra+eng", disabled=not ocr_enabled)

    st.subheader("檢查參數")
    window = st.slider("中英對照搜尋視窗（字數）", 10, 200, 80)
    zh_ngram = st.slider("中文未知詞 n-gram 長度", 3, 8, 4)
    zh_thresh = st.slider("中文相似度門檻（%）", 70, 100, 86)
    en_thresh = st.slider("英文相似度門檻（%）", 70, 100, 88)
    autosave_pairs = st.toggle("自動寫入新對照（跳過審核）", value=False)

# Load termbase
if use_gs:
    if not HAVE_GS:
        st.error("未安裝 gspread / google-auth：pip install gspread google-auth")
        st.stop()
    if not (sheet_url and creds_file):
        st.info("請在側邊欄提供 Sheet URL/ID 與 JSON 憑證。")
        ws=None; termbase = standardize_master(load_local_master())
    else:
        try:
            creds_json = json.load(creds_file)
            ws = open_worksheet(creds_json, sheet_url, ws_name)
            termbase = read_master_from_ws(ws)
            st.success(f"已連線：{ws.spreadsheet.title} / {ws.title}")
        except Exception as e:
            st.error(f"連線 Google Sheets 失敗：{e}")
            ws=None; termbase = standardize_master(load_local_master())
else:
    ws=None; termbase = standardize_master(load_local_master())

st.write(f"目前詞庫條目：{len(termbase)}")
st.dataframe(termbase.head(50), use_container_width=True)

# Upload PDF
pdf_file = st.file_uploader("上傳投影片 PDF", type=["pdf"])

if pdf_file:
    pdf_bytes = pdf_file.read()

    # Extract full text (with page OCR fallback)
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages_text=[]
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

    # ---------------- 1) 已知錯字（variant） ----------------
    known_hits=[]
    for _, row in termbase.iterrows():
        variants = re.split(r'[,\uFF0C;/、]+', str(row["variant (錯誤用法)"] or "").strip())
        for v in [x for x in variants if x]:
            rx = re.compile(cjk_pat(v), flags=re.I)
            for pno, text in enumerate(pages_text, start=1):
                for m in rx.finditer(text):
                    known_hits.append({
                        "type":"KNOWN_VARIANT","page":pno,"variant":v,
                        "zh_canonical":row["zh_canonical"],"en_canonical":row["en_canonical"],
                        "context": text[max(0,m.start()-60):m.end()+60]
                    })
    # ---------------- 2) 擷取 PDF 中英對照 ----------------
    pairs_df = parse_pdf_pairs(full_text)
    merged = pairs_df.merge(termbase[["en_canonical","zh_canonical","abbr"]],
                            on=["en_canonical","zh_canonical","abbr"], how="left", indicator=True)
    new_pairs = merged[merged["_merge"]=="left_only"][pairs_df.columns]

    # ---------------- 3) 中英一致性 ----------------
    pair_issues=[]
    # build regex for canonical terms
    en_pats=[(re.compile(re.escape(row["en_canonical"]).replace(r'\-', r'\s*-\s*'), flags=re.I), row)
             for _,row in termbase.iterrows() if row["en_canonical"].strip()]
    zh_pats=[(re.compile(cjk_pat(row["zh_canonical"]), flags=re.I), row)
             for _,row in termbase.iterrows() if row["zh_canonical"].strip()]

    for pno, text in enumerate(pages_text, start=1):
        # find en & zh hits on this page
        en_hits=[]; zh_hits=[]
        for rx,row in en_pats:
            for m in rx.finditer(text): en_hits.append((m.start(), m.end(), row))
        for rx,row in zh_pats:
            for m in rx.finditer(text): zh_hits.append((m.start(), m.end(), row))

        for es,ee,erow in en_hits:
            exp_zh = erow["zh_canonical"]
            found=False
            for zs,ze,zrow in zh_hits:
                if abs(zs-es) <= window and zrow["zh_canonical"]==exp_zh:
                    found=True; break
            if not found:
                pair_issues.append({
                    "type":"MISSING_OR_WRONG_ZH","page":pno,
                    "en_canonical":erow["en_canonical"],
                    "expected_zh": exp_zh,
                    "context": text[max(0, es-window): ee+window]
                })

    # ---------------- 4) 首次出現格式 ----------------
    first_mentions=[]
    seen=set()
    for pno, text in enumerate(pages_text, start=1):
        # reuse en_pats / zh_pats
        zh_hits=[(m.start(),m.end(),row) for rx,row in zh_pats for m in rx.finditer(text)]
        for rx,row in en_pats:
            for m in rx.finditer(text):
                key=row[1]["en_canonical"].lower()
                if key in seen: continue
                seen.add(key)
                if str(row[1].get("first_mention_style","ZH(EN;ABBR)")).upper().startswith("ZH("):
                    exp_zh=row[1]["zh_canonical"]
                    has_zh_near = any(abs(zs-m.start())<=window and zrow["zh_canonical"]==exp_zh for zs,ze,zrow in zh_hits)
                    if not has_zh_near:
                        first_mentions.append({
                            "type":"FIRST_MENTION_MISSING_ZH","page":pno,
                            "en_canonical": row[1]["en_canonical"],
                            "expected_zh": exp_zh,
                            "abbr": row[1].get("abbr",""),
                            "context": text[max(0, m.start()-window): m.end()+window]
                        })

    # ---------------- 5) 未知詞偵測 ----------------
    known_ens = set(termbase["en_canonical"].str.lower().tolist() + termbase["abbr"].str.lower().tolist())
    known_zhs = set(termbase["zh_canonical"].tolist())
    en_cands=[]; zh_cands=[]

    for tok in set(en_tokens(full_text)):
        t=tok.strip()
        if not t or t.lower() in known_ens: continue
        suggest=""; score=0
        if HAVE_RF and len(termbase)>0:
            pool = termbase["en_canonical"].tolist() + termbase["abbr"].tolist()
            res = process.extractOne(t, pool, scorer=fuzz.WRatio)
            if res: suggest, score = res[0], float(res[1])
        if not HAVE_RF and len(t)<4: continue
        if score >=  en_thresh or (not HAVE_RF):
            en_cands.append({"type":"UNKNOWN_EN","candidate":t,"suggest":suggest,"score":round(score,1)})

    for gram in set(cjk_tokens(full_text, zh_ngram, zh_ngram)):
        if gram in known_zhs: continue
        suggest=""; score=0
        if HAVE_RF and len(termbase)>0:
            res = process.extractOne(gram, termbase["zh_canonical"].tolist(), scorer=fuzz.WRatio)
            if res: suggest, score = res[0], float(res[1])
        if score >= zh_thresh or (not HAVE_RF):
            zh_cands.append({"type":"UNKNOWN_ZH","candidate":gram,"suggest":suggest,"score":round(score,1)})

    # ---------------- UI Tabs + Actions ----------------
    st.success(f"完成：已知錯字 {len(known_hits)}｜對照問題 {len(pair_issues)}｜首次問題 {len(first_mentions)}｜未知詞 {len(en_cands)+len(zh_cands)}｜新對照 {len(new_pairs)}")

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["已知錯字", "中英對照問題", "首次出現格式", "未知詞偵測", "新擷取對照"])
    with tab1:
        st.dataframe(pd.DataFrame(known_hits), use_container_width=True) if known_hits else st.info("None")
    with tab2:
        st.dataframe(pd.DataFrame(pair_issues), use_container_width=True) if pair_issues else st.info("None")
    with tab3:
        st.dataframe(pd.DataFrame(first_mentions), use_container_width=True) if first_mentions else st.info("None")
    with tab4:
        unk_df = pd.DataFrame(en_cands + zh_cands).sort_values(["type","score"], ascending=[True, False])
        if not unk_df.empty:
            st.dataframe(unk_df, use_container_width=True)
            st.write("✅ 勾選並補齊對應中英文 → 加入詞庫：")
            rows=[]
            for _, r in unk_df.iterrows():
                if r["type"]=="UNKNOWN_EN":
                    rows.append({"add":False, "en_canonical":r["candidate"], "zh_canonical":r.get("suggest",""), "abbr":"", "first_mention_style":"ZH(EN;ABBR)", "variant (錯誤用法)":""})
                else:
                    rows.append({"add":False, "en_canonical":"", "zh_canonical":r["candidate"], "abbr":"", "first_mention_style":"ZH(EN;ABBR)", "variant (錯誤用法)":""})
            editable = pd.DataFrame(rows)
            approved = st.data_editor(editable, use_container_width=True, num_rows="dynamic")
            to_write = approved[(approved["add"]==True) & (approved["en_canonical"].astype(str)!="") & (approved["zh_canonical"].astype(str)!="")].drop(columns=["add"])
            if not to_write.empty and st.button("寫入核准的未知詞 → 詞庫"):
                combined = pd.concat([termbase, to_write], ignore_index=True)
                combined = standardize_master(combined)
                if ws is not None: write_master_to_ws(ws, combined)
                else: save_local_master(combined)
                st.success(f"已寫入 {len(to_write)} 條新詞。")
        else:
            st.info("未偵測到未知詞。")
    with tab5:
        st.dataframe(new_pairs, use_container_width=True) if not new_pairs.empty else st.info("None")
        if autosave_pairs and not new_pairs.empty:
            combined = pd.concat([termbase, new_pairs], ignore_index=True)
            combined = standardize_master(combined)
            if ws is not None: write_master_to_ws(ws, combined)
            else: save_local_master(combined)
            st.success(f"已自動寫入新對照 {len(new_pairs)} 條。")
        elif not autosave_pairs and not new_pairs.empty:
            selectable = new_pairs.copy()
            selectable.insert(0, "add", False)
            edited = st.data_editor(selectable, use_container_width=True, num_rows="dynamic")
            to_add = edited[edited["add"]==True].drop(columns=["add"])
            if not to_add.empty and st.button("寫入選取新對照 → 詞庫"):
                combined = pd.concat([termbase, to_add], ignore_index=True)
                combined = standardize_master(combined)
                if ws is not None: write_master_to_ws(ws, combined)
                else: save_local_master(combined)
                st.success(f"已寫入 {len(to_add)} 條。")

st.write("---")
st.caption("依賴：streamlit, pypdf, pdf2image, pytesseract, pillow, rapidfuzz, gspread, google-auth。若使用 GSheets，務必將 Service Account 的 email 加為試算表編輯者。")
