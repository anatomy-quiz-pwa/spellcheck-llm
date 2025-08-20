import streamlit as st
import pandas as pd
import re, io, os, json
from pypdf import PdfReader
from datetime import datetime
import tempfile
import subprocess

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

try:
    # 檢查ffmpeg是否可用
    subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    HAVE_FFMPEG = True
except (subprocess.CalledProcessError, FileNotFoundError):
    HAVE_FFMPEG = False

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

# ---- Termbase schema ----
REQUIRED_COLS = ["en_canonical","zh_canonical","abbr","first_mention_style","variant (錯誤用法)","status","added_date","翻譯來源"]
def standardize_master(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=REQUIRED_COLS)
    for c in REQUIRED_COLS:
        if c not in df.columns: df[c] = ""
        df[c] = df[c].astype(str).fillna("").str.strip()
    df.loc[df["first_mention_style"]=="","first_mention_style"] = "ZH(EN;ABBR)"
    df.loc[df["status"]=="","status"] = "已確認"
    df.loc[df["翻譯來源"]=="","翻譯來源"] = "手動輸入"
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

# ---- PDF bilingual extraction ----
def extract_text_from_images(pdf_bytes, pages_text):
    """從PDF圖片中提取文字"""
    if not HAVE_OCR:
        return pages_text
    
    try:
        # 轉換PDF為圖片
        images = convert_from_bytes(pdf_bytes, fmt="png")
        
        # 對每一頁進行OCR
        for page_num, image in enumerate(images):
            try:
                # 使用OCR提取文字
                ocr_text = pytesseract.image_to_string(image, lang="chi_tra+eng")
                ocr_text = normalize_text(ocr_text)
                
                # 如果OCR提取的文字比原始文字多，則使用OCR文字
                if len(ocr_text) > len(pages_text[page_num]):
                    pages_text[page_num] = ocr_text
                    
            except Exception as e:
                print(f"OCR處理第{page_num+1}頁時出錯: {e}")
                continue
                
    except Exception as e:
        print(f"圖片轉換失敗: {e}")
    
    return pages_text

def parse_pdf_pairs_with_location(pages_text: list) -> pd.DataFrame:
    """從PDF文字中提取中英文對照，並記錄位置信息"""
    ZH = r"[一-龥]{2,30}"
    EN = r"[A-Za-z][A-Za-z0-9\-\s]{1,80}"
    ABBR = r"[A-Za-z][A-Za-z0-9\-]{1,10}"

    pairs=[]
    # 模式1: 中文(英文;縮寫)
    pat1 = re.compile(rf"(?P<zh>{ZH})\s*[\(（]\s*(?P<en>{EN})(?:\s*;\s*|；\s*)(?P<abbr>{ABBR})\s*[\)）]")
    # 模式2: 中文(英文)
    pat2 = re.compile(rf"(?P<zh>{ZH})\s*[\(（]\s*(?P<en>{EN})\s*[\)）]")
    # 模式3: 英文(中文)
    pat3 = re.compile(rf"(?P<en>{EN})\s*[\(（]\s*(?P<zh>{ZH})\s*[\)）]")
    # 模式4: 英文 - 中文
    pat4 = re.compile(rf"(?P<en>{EN})\s*[-－]\s*(?P<zh>{ZH})")
    # 模式5: 中文 - 英文
    pat5 = re.compile(rf"(?P<zh>{ZH})\s*[-－]\s*(?P<en>{EN})")

    # 遍歷每一頁
    for page_num, page_text in enumerate(pages_text, 1):
        # 模式1: 中文(英文;縮寫)
        for m in pat1.finditer(page_text):
            zh=m.group("zh").strip(); en=re.sub(r"\s+"," ", m.group("en").strip()); abbr=m.group("abbr").strip()
            context = page_text[max(0, m.start()-50):m.end()+50]
            pairs.append((en, zh, abbr, "ZH(EN;ABBR)", "", page_num, m.start(), context))
        
        # 模式2: 中文(英文)
        for m in pat2.finditer(page_text):
            zh=m.group("zh").strip(); en=re.sub(r"\s+"," ", m.group("en").strip())
            context = page_text[max(0, m.start()-50):m.end()+50]
            pairs.append((en, zh, "", "ZH(EN)", "", page_num, m.start(), context))
        
        # 模式3: 英文(中文)
        for m in pat3.finditer(page_text):
            en=re.sub(r"\s+"," ", m.group("en").strip()); zh=m.group("zh").strip()
            context = page_text[max(0, m.start()-50):m.end()+50]
            pairs.append((en, zh, "", "ZH(EN)", "", page_num, m.start(), context))
        
        # 模式4: 英文 - 中文
        for m in pat4.finditer(page_text):
            en=re.sub(r"\s+"," ", m.group("en").strip()); zh=m.group("zh").strip()
            context = page_text[max(0, m.start()-50):m.end()+50]
            pairs.append((en, zh, "", "ZH(EN)", "", page_num, m.start(), context))
        
        # 模式5: 中文 - 英文
        for m in pat5.finditer(page_text):
            zh=m.group("zh").strip(); en=re.sub(r"\s+"," ", m.group("en").strip())
            context = page_text[max(0, m.start()-50):m.end()+50]
            pairs.append((en, zh, "", "ZH(EN)", "", page_num, m.start(), context))

    # 創建DataFrame，包含位置信息
    df = pd.DataFrame(pairs, columns=["en_canonical", "zh_canonical", "abbr", "first_mention_style", "variant (錯誤用法)", "page", "position", "context"])
    if df.empty: 
        df = pd.DataFrame(columns=["en_canonical", "zh_canonical", "abbr", "first_mention_style", "variant (錯誤用法)", "page", "position", "context"])
    
    # 添加翻譯來源欄位
    df["翻譯來源"] = "PDF自動提取"
    
    df = df.drop_duplicates(subset=["en_canonical","zh_canonical","abbr"])
    return df

def detect_image_text_inconsistencies(pages_text: list, termbase_df: pd.DataFrame) -> list:
    """檢測圖片文字與詞庫的不一致性"""
    if termbase_df.empty:
        return []
    
    inconsistencies = []
    
    # 提取詞庫中的所有英文和中文詞彙
    en_terms = set(termbase_df["en_canonical"].str.lower().tolist())
    zh_terms = set(termbase_df["zh_canonical"].tolist())
    
    # 英文單詞模式
    en_word_pattern = re.compile(r'\b[A-Za-z][A-Za-z0-9\-\s]{1,20}\b')
    # 中文詞彙模式
    zh_word_pattern = re.compile(r'[一-龥]{2,10}')
    
    for page_num, page_text in enumerate(pages_text, 1):
        # 檢測英文詞彙
        for match in en_word_pattern.finditer(page_text):
            word = match.group().strip()
            word_lower = word.lower()
            
            # 檢查是否在詞庫中
            if word_lower in en_terms:
                # 找到對應的中文翻譯
                term_row = termbase_df[termbase_df["en_canonical"].str.lower() == word_lower]
                if not term_row.empty:
                    expected_zh = term_row.iloc[0]["zh_canonical"]
                    
                    # 檢查附近是否有中文翻譯
                    context_start = max(0, match.start() - 100)
                    context_end = min(len(page_text), match.end() + 100)
                    context = page_text[context_start:context_end]
                    
                    # 檢查上下文中是否包含預期的中文翻譯
                    if expected_zh not in context:
                        inconsistencies.append({
                            "type": "圖片英文缺少中文翻譯",
                            "page": page_num,
                            "position": match.start(),
                            "english_word": word,
                            "expected_chinese": expected_zh,
                            "context": context
                        })
        
        # 檢測中文詞彙
        for match in zh_word_pattern.finditer(page_text):
            zh_word = match.group().strip()
            
            # 檢查是否在詞庫中
            if zh_word in zh_terms:
                # 找到對應的英文翻譯
                term_row = termbase_df[termbase_df["zh_canonical"] == zh_word]
                if not term_row.empty:
                    expected_en = term_row.iloc[0]["en_canonical"]
                    
                    # 檢查附近是否有英文翻譯
                    context_start = max(0, match.start() - 100)
                    context_end = min(len(page_text), match.end() + 100)
                    context = page_text[context_start:context_end]
                    
                    # 檢查上下文中是否包含預期的英文翻譯
                    if expected_en.lower() not in context.lower():
                        inconsistencies.append({
                            "type": "圖片中文缺少英文翻譯",
                            "page": page_num,
                            "position": match.start(),
                            "chinese_word": zh_word,
                            "expected_english": expected_en,
                            "context": context
                        })
    
    return inconsistencies

# ---- Video subtitle extraction ----
def extract_subtitles_from_video(video_bytes, video_format="mp4"):
    """從影片中提取字幕"""
    if not HAVE_FFMPEG:
        return [], "❌ 需要安裝 ffmpeg 來處理影片"
    
    try:
        # 創建臨時文件
        with tempfile.NamedTemporaryFile(suffix=f".{video_format}", delete=False) as temp_video:
            temp_video.write(video_bytes)
            temp_video_path = temp_video.name
        
        # 使用ffmpeg提取字幕
        subtitle_path = temp_video_path.replace(f".{video_format}", ".srt")
        
        # 嘗試提取內嵌字幕
        cmd = [
            "ffmpeg", "-i", temp_video_path,
            "-map", "0:s:0",  # 提取第一個字幕軌道
            "-c:s", "srt",
            subtitle_path,
            "-y"  # 覆蓋現有文件
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0 and os.path.exists(subtitle_path):
            # 成功提取字幕
            with open(subtitle_path, 'r', encoding='utf-8') as f:
                subtitle_content = f.read()
            
            # 清理臨時文件
            os.unlink(temp_video_path)
            os.unlink(subtitle_path)
            
            return parse_srt_subtitles(subtitle_content), None
        else:
            # 嘗試使用語音識別（需要額外的依賴）
            return extract_audio_and_recognize(video_bytes, temp_video_path)
            
    except Exception as e:
        return [], f"❌ 影片處理失敗：{str(e)}"

def parse_srt_subtitles(srt_content):
    """解析SRT字幕格式"""
    subtitles = []
    lines = srt_content.strip().split('\n')
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # 跳過空行
        if not line:
            i += 1
            continue
        
        # 檢查是否為字幕序號
        if line.isdigit():
            # 讀取時間戳
            if i + 1 < len(lines):
                timestamp = lines[i + 1].strip()
                i += 2
                
                # 讀取字幕文本
                subtitle_text = []
                while i < len(lines) and lines[i].strip():
                    subtitle_text.append(lines[i].strip())
                    i += 1
                
                if subtitle_text:
                    subtitles.append({
                        'text': ' '.join(subtitle_text),
                        'timestamp': timestamp,
                        'start_time': parse_timestamp(timestamp.split(' --> ')[0]),
                        'end_time': parse_timestamp(timestamp.split(' --> ')[1]) if ' --> ' in timestamp else None
                    })
        i += 1
    
    return subtitles

def parse_timestamp(timestamp):
    """解析時間戳格式 (HH:MM:SS,mmm)"""
    try:
        time_part, ms_part = timestamp.split(',')
        h, m, s = map(int, time_part.split(':'))
        ms = int(ms_part)
        return h * 3600 + m * 60 + s + ms / 1000
    except:
        return 0

def extract_audio_and_recognize(video_bytes, video_path):
    """提取音頻並進行語音識別（簡化版本）"""
    try:
        # 提取音頻
        audio_path = video_path.replace('.mp4', '.wav')
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            audio_path,
            "-y"
        ]
        
        result = subprocess.run(cmd, capture_output=True)
        
        if result.returncode == 0:
            # 這裡可以添加語音識別功能
            # 目前返回空列表，表示需要手動輸入字幕
            os.unlink(video_path)
            os.unlink(audio_path)
            return [], "ℹ️ 影片沒有內嵌字幕，請手動輸入字幕內容"
        else:
            os.unlink(video_path)
            return [], "❌ 音頻提取失敗"
            
    except Exception as e:
        return [], f"❌ 音頻處理失敗：{str(e)}"

def detect_subtitle_inconsistencies(subtitles, termbase_df):
    """檢測字幕中的翻譯不一致性"""
    if termbase_df.empty or not subtitles:
        return []
    
    inconsistencies = []
    
    # 提取詞庫中的所有英文和中文詞彙
    en_terms = set(termbase_df["en_canonical"].str.lower().tolist())
    zh_terms = set(termbase_df["zh_canonical"].tolist())
    
    # 英文單詞模式
    en_word_pattern = re.compile(r'\b[A-Za-z][A-Za-z0-9\-\s]{1,20}\b')
    # 中文詞彙模式
    zh_word_pattern = re.compile(r'[一-龥]{2,10}')
    
    for subtitle in subtitles:
        text = subtitle['text']
        timestamp = subtitle['timestamp']
        
        # 檢測英文詞彙
        for match in en_word_pattern.finditer(text):
            word = match.group().strip()
            word_lower = word.lower()
            
            # 檢查是否在詞庫中
            if word_lower in en_terms:
                # 找到對應的中文翻譯
                term_row = termbase_df[termbase_df["en_canonical"].str.lower() == word_lower]
                if not term_row.empty:
                    expected_zh = term_row.iloc[0]["zh_canonical"]
                    
                    # 檢查字幕中是否包含預期的中文翻譯
                    if expected_zh not in text:
                        inconsistencies.append({
                            "type": "字幕英文缺少中文翻譯",
                            "timestamp": timestamp,
                            "english_word": word,
                            "expected_chinese": expected_zh,
                            "subtitle_text": text
                        })
        
        # 檢測中文詞彙
        for match in zh_word_pattern.finditer(text):
            zh_word = match.group().strip()
            
            # 檢查是否在詞庫中
            if zh_word in zh_terms:
                # 找到對應的英文翻譯
                term_row = termbase_df[termbase_df["zh_canonical"] == zh_word]
                if not term_row.empty:
                    expected_en = term_row.iloc[0]["en_canonical"]
                    
                    # 檢查字幕中是否包含預期的英文翻譯
                    if expected_en.lower() not in text.lower():
                        inconsistencies.append({
                            "type": "字幕中文缺少英文翻譯",
                            "timestamp": timestamp,
                            "chinese_word": zh_word,
                            "expected_english": expected_en,
                            "subtitle_text": text
                        })
    
    return inconsistencies

def parse_pdf_pairs(full_text: str) -> pd.DataFrame:
    """從PDF文字中提取中英文對照（保持向後兼容）"""
    ZH = r"[一-龥]{2,30}"
    EN = r"[A-Za-z][A-Za-z0-9\-\s]{1,80}"
    ABBR = r"[A-Za-z][A-Za-z0-9\-]{1,10}"

    pairs=[]
    # 模式1: 中文(英文;縮寫)
    pat1 = re.compile(rf"(?P<zh>{ZH})\s*[\(（]\s*(?P<en>{EN})(?:\s*;\s*|；\s*)(?P<abbr>{ABBR})\s*[\)）]")
    # 模式2: 中文(英文)
    pat2 = re.compile(rf"(?P<zh>{ZH})\s*[\(（]\s*(?P<en>{EN})\s*[\)）]")
    # 模式3: 英文(中文)
    pat3 = re.compile(rf"(?P<en>{EN})\s*[\(（]\s*(?P<zh>{ZH})\s*[\)）]")
    # 模式4: 英文 - 中文
    pat4 = re.compile(rf"(?P<en>{EN})\s*[-－]\s*(?P<zh>{ZH})")
    # 模式5: 中文 - 英文
    pat5 = re.compile(rf"(?P<zh>{ZH})\s*[-－]\s*(?P<en>{EN})")

    for m in pat1.finditer(full_text):
        zh=m.group("zh").strip(); en=re.sub(r"\s+"," ", m.group("en").strip()); abbr=m.group("abbr").strip()
        pairs.append((en, zh, abbr, "ZH(EN;ABBR)", ""))
    for m in pat2.finditer(full_text):
        zh=m.group("zh").strip(); en=re.sub(r"\s+"," ", m.group("en").strip())
        pairs.append((en, zh, "", "ZH(EN)", ""))
    for m in pat3.finditer(full_text):
        en=re.sub(r"\s+"," ", m.group("en").strip()); zh=m.group("zh").strip()
        pairs.append((en, zh, "", "ZH(EN)", ""))
    for m in pat4.finditer(full_text):
        en=re.sub(r"\s+"," ", m.group("en").strip()); zh=m.group("zh").strip()
        pairs.append((en, zh, "", "ZH(EN)", ""))
    for m in pat5.finditer(full_text):
        zh=m.group("zh").strip(); en=re.sub(r"\s+"," ", m.group("en").strip())
        pairs.append((en, zh, "", "ZH(EN)", ""))

    # 創建DataFrame，包含所有必要的列（除了status和added_date）
    df = pd.DataFrame(pairs, columns=["en_canonical", "zh_canonical", "abbr", "first_mention_style", "variant (錯誤用法)"])
    if df.empty: 
        df = pd.DataFrame(columns=["en_canonical", "zh_canonical", "abbr", "first_mention_style", "variant (錯誤用法)"])
    
    # 添加翻譯來源欄位
    df["翻譯來源"] = "PDF自動提取"
    
    df = df.drop_duplicates(subset=["en_canonical","zh_canonical","abbr"])
    return df

def detect_differences(extracted_df: pd.DataFrame, termbase_df: pd.DataFrame) -> tuple:
    """檢測提取的內容與詞庫的差異"""
    # 標準化兩個DataFrame
    extracted_clean = extracted_df[["en_canonical","zh_canonical","abbr"]].copy()
    termbase_clean = termbase_df[["en_canonical","zh_canonical","abbr"]].copy()
    
    # 找出新增的內容（在提取中但不在詞庫中）
    merged = extracted_clean.merge(termbase_clean, on=["en_canonical","zh_canonical","abbr"], how="left", indicator=True)
    new_items = merged[merged["_merge"]=="left_only"][["en_canonical","zh_canonical","abbr"]]
    
    # 找出可能的錯誤（在詞庫中但與提取的內容不一致）
    potential_errors = []
    
    # 檢查英文相同但中文不同的情況
    en_merge = extracted_clean.merge(termbase_clean, on="en_canonical", how="inner", suffixes=("_ext", "_term"))
    en_conflicts = en_merge[en_merge["zh_canonical_ext"] != en_merge["zh_canonical_term"]]
    
    # 檢查中文相同但英文不同的情況
    zh_merge = extracted_clean.merge(termbase_clean, on="zh_canonical", how="inner", suffixes=("_ext", "_term"))
    zh_conflicts = zh_merge[zh_merge["en_canonical_ext"] != zh_merge["en_canonical_term"]]
    
    for _, row in en_conflicts.iterrows():
        potential_errors.append({
            "type": "英文相同，中文不同",
            "en_canonical": row["en_canonical"],
            "zh_extracted": row["zh_canonical_ext"],
            "zh_termbase": row["zh_canonical_term"],
            "abbr_extracted": row["abbr_ext"],
            "abbr_termbase": row["abbr_term"]
        })
    
    for _, row in zh_conflicts.iterrows():
        potential_errors.append({
            "type": "中文相同，英文不同",
            "zh_canonical": row["zh_canonical"],
            "en_extracted": row["en_canonical_ext"],
            "en_termbase": row["en_canonical_term"],
            "abbr_extracted": row["abbr_ext"],
            "abbr_termbase": row["abbr_term"]
        })
    
    return new_items, potential_errors

def detect_differences_with_location(extracted_df_with_location: pd.DataFrame, termbase_df: pd.DataFrame) -> tuple:
    """檢測提取的內容與詞庫的差異（包含位置信息）"""
    # 標準化兩個DataFrame
    extracted_clean = extracted_df_with_location[["en_canonical","zh_canonical","abbr"]].copy()
    termbase_clean = termbase_df[["en_canonical","zh_canonical","abbr"]].copy()
    
    # 找出新增的內容（在提取中但不在詞庫中）
    merged = extracted_clean.merge(termbase_clean, on=["en_canonical","zh_canonical","abbr"], how="left", indicator=True)
    new_items = merged[merged["_merge"]=="left_only"][["en_canonical","zh_canonical","abbr"]]
    
    # 找出可能的錯誤（在詞庫中但與提取的內容不一致）
    potential_errors = []
    
    # 檢查英文相同但中文不同的情況
    en_merge = extracted_clean.merge(termbase_clean, on="en_canonical", how="inner", suffixes=("_ext", "_term"))
    en_conflicts = en_merge[en_merge["zh_canonical_ext"] != en_merge["zh_canonical_term"]]
    
    # 檢查中文相同但英文不同的情況
    zh_merge = extracted_clean.merge(termbase_clean, on="zh_canonical", how="inner", suffixes=("_ext", "_term"))
    zh_conflicts = zh_merge[zh_merge["en_canonical_ext"] != zh_merge["en_canonical_term"]]
    
    for _, row in en_conflicts.iterrows():
        # 找到對應的位置信息
        matching_row = extracted_df_with_location[
            (extracted_df_with_location["en_canonical"] == row["en_canonical"]) &
            (extracted_df_with_location["zh_canonical"] == row["zh_canonical_ext"])
        ]
        
        if not matching_row.empty:
            location_row = matching_row.iloc[0]
            
            # 檢查詞庫中對應條目的狀態
            termbase_row = termbase_df[termbase_df["en_canonical"] == row["en_canonical"]]
            status = termbase_row.iloc[0]["status"] if not termbase_row.empty else "未知"
            
            potential_errors.append({
                "type": "英文相同，中文不同",
                "en_canonical": row["en_canonical"],
                "zh_extracted": row["zh_canonical_ext"],
                "zh_termbase": row["zh_canonical_term"],
                "abbr_extracted": row["abbr_ext"],
                "abbr_termbase": row["abbr_term"],
                "termbase_status": status,
                "page": location_row["page"],
                "position": location_row["position"],
                "context": location_row["context"]
            })
    
    for _, row in zh_conflicts.iterrows():
        # 找到對應的位置信息
        matching_row = extracted_df_with_location[
            (extracted_df_with_location["zh_canonical"] == row["zh_canonical"]) &
            (extracted_df_with_location["en_canonical"] == row["en_canonical_ext"])
        ]
        
        if not matching_row.empty:
            location_row = matching_row.iloc[0]
            
            # 檢查詞庫中對應條目的狀態
            termbase_row = termbase_df[termbase_df["zh_canonical"] == row["zh_canonical"]]
            status = termbase_row.iloc[0]["status"] if not termbase_row.empty else "未知"
            
            potential_errors.append({
                "type": "中文相同，英文不同",
                "zh_canonical": row["zh_canonical"],
                "en_extracted": row["en_canonical_ext"],
                "en_termbase": row["en_canonical_term"],
                "abbr_extracted": row["abbr_ext"],
                "abbr_termbase": row["abbr_term"],
                "termbase_status": status,
                "page": location_row["page"],
                "position": location_row["position"],
                "context": location_row["context"]
            })
    
    return new_items, potential_errors

# ---- Streamlit UI ----
st.set_page_config(page_title="PDF自動提取中英文翻譯對比系統", layout="wide")
st.title("📄 PDF自動提取中英文翻譯對比系統")
st.markdown("**功能：** 自動提取PDF中的中英文翻譯，對比Google Sheets詞庫，檢測差異並自動新增")

# 簡化的使用說明
with st.expander("📖 快速使用說明", expanded=False):
    st.markdown("""
    ### 🚀 三步驟完成：
    1. **上傳PDF** - 選擇包含中英文對照的投影片
    2. **自動處理** - 系統自動提取並對比詞庫
    3. **一鍵新增** - 點擊按鈕自動新增到Google Sheets
    
    ### 📋 支援的格式：
    - `中文(英文;縮寫)`
    - `中文(英文)`
    - `英文(中文)`
    - `英文 - 中文`
    - `中文 - 英文`
    
    ### ⚙️ 自動化功能：
    - 自動連線Google Sheets
    - 自動提取中英文對照
    - 自動檢測新增內容
    - 自動標記為"新增待確認"
    """)

# 預設設定
DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1UbBCtcUscJ65lCBkjLmZod3L7lR5e85ztgyPcetwGWs/edit?usp=sharing"
DEFAULT_WS_NAME = "termbase_master"
DEFAULT_JSON_PATH = "service_account_key.json"

# 側邊欄配置
with st.sidebar:
    st.subheader("🔗 Google Sheets 設定")
    use_gs = st.toggle("使用 Google Sheets", value=True)
    
    # 自動填入預設值
    sheet_url = st.text_input("Sheet URL 或 ID", value=DEFAULT_SHEET_URL, disabled=not use_gs)
    ws_name = st.text_input("工作表名稱", value=DEFAULT_WS_NAME, disabled=not use_gs)
    
    # 顯示當前使用的 JSON 文件
    if os.path.exists(DEFAULT_JSON_PATH):
        st.success(f"✅ 使用預設 JSON: {DEFAULT_JSON_PATH}")
    else:
        st.error(f"❌ 找不到預設 JSON: {DEFAULT_JSON_PATH}")
        creds_file = st.file_uploader("上傳 service account JSON", type=["json"], disabled=not use_gs)

    st.subheader("🔍 OCR 設定")
    ocr_enabled = st.toggle("啟用 OCR 後備", value=True)
    ocr_thresh = st.slider("OCR 觸發：抽取字元少於", 0, 200, 10, disabled=not ocr_enabled)
    ocr_lang = st.text_input("OCR 語言", value="chi_tra+eng", disabled=not ocr_enabled)

    st.subheader("⚙️ 自動化設定")
    auto_add_new = st.toggle("自動新增新內容", value=True)
    auto_mark_pending = st.toggle("自動標記為待確認", value=True)

# 載入詞庫
if use_gs:
    if not HAVE_GS:
        st.error("❌ 未安裝 gspread / google-auth：`pip install gspread google-auth`")
        st.stop()
    
    # 自動使用預設 JSON 文件
    if os.path.exists(DEFAULT_JSON_PATH):
        try:
            with open(DEFAULT_JSON_PATH, 'r', encoding='utf-8') as f:
                creds_json = json.load(f)
            ws = open_worksheet(creds_json, sheet_url, ws_name)
            termbase = read_master_from_ws(ws)
            st.success(f"✅ 已自動連線：{ws.spreadsheet.title} / {ws.title}")
        except Exception as e:
            st.error(f"❌ 自動連線 Google Sheets 失敗：{e}")
            st.info("ℹ️ 請檢查 JSON 文件或手動上傳憑證。")
            ws = None
            termbase = standardize_master(pd.DataFrame())
    else:
        st.error(f"❌ 找不到預設 JSON 文件：{DEFAULT_JSON_PATH}")
        st.info("ℹ️ 請手動上傳 JSON 憑證文件。")
        ws = None
        termbase = standardize_master(pd.DataFrame())
else:
    ws = None
    termbase = standardize_master(pd.DataFrame())

# 顯示詞庫統計
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("詞庫總條目", len(termbase))
with col2:
    confirmed_count = len(termbase[termbase["status"] == "已確認"])
    st.metric("已確認條目", confirmed_count)
with col3:
    pending_count = len(termbase[termbase["status"] == "新增待確認"])
    st.metric("待確認條目", pending_count)
with col4:
    pdf_source_count = len(termbase[termbase["翻譯來源"] == "PDF自動提取"])
    st.metric("PDF來源", pdf_source_count)

# 主要功能區域
st.write("---")
st.subheader("🚀 快速開始：上傳文件")

# 選擇文件類型
file_type = st.radio(
    "選擇文件類型：",
    ["📄 PDF 投影片", "🎬 影片文件"],
    horizontal=True
)

if file_type == "📄 PDF 投影片":
    # 上傳PDF
    pdf_file = st.file_uploader("📤 上傳投影片 PDF", type=["pdf"], help="選擇包含中英文對照的PDF投影片文件")
    video_file = None
else:
    # 上傳影片
    video_file = st.file_uploader("📤 上傳影片文件", type=["mp4", "avi", "mov", "mkv"], help="選擇包含字幕的影片文件")
    pdf_file = None
    
    # 顯示ffmpeg狀態
    if not HAVE_FFMPEG:
        st.warning("⚠️ 需要安裝 ffmpeg 來處理影片文件")
        st.info("安裝方法：`brew install ffmpeg` (macOS) 或 `sudo apt install ffmpeg` (Ubuntu)")
    else:
        st.success("✅ ffmpeg 已安裝，可以處理影片文件")

if pdf_file or video_file:
    if pdf_file:
        with st.spinner("正在處理PDF..."):
            pdf_bytes = pdf_file.read()
            
            # 提取PDF文字（包含OCR後備）
            reader = PdfReader(io.BytesIO(pdf_bytes))
            pages_text = []
            
            for pno, page in enumerate(reader.pages, start=1):
                try:
                    raw = page.extract_text() or ""
                except Exception:
                    raw = ""
                
                norm = normalize_text(raw)
                
                # 如果文字太少且啟用OCR，嘗試OCR
                if ocr_enabled and len(norm) < ocr_thresh and HAVE_OCR:
                    try:
                        images = convert_from_bytes(pdf_bytes, first_page=pno, last_page=pno, fmt="png")
                        if images:
                            txt = pytesseract.image_to_string(images[0], lang=ocr_lang)
                            norm = normalize_text(txt)
                    except Exception:
                        pass
                
                pages_text.append(norm)
            
            # 圖片文字掃描（增強OCR）
            if ocr_enabled and HAVE_OCR:
                pages_text = extract_text_from_images(pdf_bytes, pages_text)
            
            full_text = "\n".join(pages_text)
            
            # 提取中英文對照（帶位置信息）
            extracted_pairs_with_location = parse_pdf_pairs_with_location(pages_text)
            extracted_pairs = parse_pdf_pairs(full_text)  # 保持向後兼容
            
            # 檢測差異（使用帶位置信息的函數）
            new_items, potential_errors = detect_differences_with_location(extracted_pairs_with_location, termbase)
            
            # 檢測圖片文字一致性
            image_inconsistencies = detect_image_text_inconsistencies(pages_text, termbase)
            
            # 影片相關變數設為空
            subtitles = []
            subtitle_inconsistencies = []
            
    elif video_file:
        with st.spinner("正在處理影片..."):
            video_bytes = video_file.read()
            video_format = video_file.name.split('.')[-1].lower()
            
            # 提取字幕
            subtitles, error_msg = extract_subtitles_from_video(video_bytes, video_format)
            
            if error_msg:
                st.warning(error_msg)
                
                # 提供手動輸入字幕的選項
                st.subheader("📝 手動輸入字幕")
                manual_subtitles = st.text_area(
                    "請輸入字幕內容（每行一個字幕）：",
                    height=200,
                    help="格式：時間戳 字幕內容，例如：00:01:30,000 --> 00:01:35,000 這是字幕內容"
                )
                
                if manual_subtitles:
                    # 簡單解析手動輸入的字幕
                    lines = manual_subtitles.strip().split('\n')
                    subtitles = []
                    for line in lines:
                        if ' --> ' in line:
                            parts = line.split(' --> ')
                            if len(parts) == 2:
                                timestamp = line
                                text = parts[1].split(' ', 1)[1] if len(parts[1].split(' ', 1)) > 1 else ""
                                subtitles.append({
                                    'text': text,
                                    'timestamp': timestamp,
                                    'start_time': parse_timestamp(parts[0]),
                                    'end_time': parse_timestamp(parts[1])
                                })
            
            # 檢測字幕不一致性
            subtitle_inconsistencies = detect_subtitle_inconsistencies(subtitles, termbase)
            
            # 從字幕中提取中英文對照
            subtitle_text = "\n".join([sub['text'] for sub in subtitles])
            extracted_pairs_with_location = parse_pdf_pairs_with_location([subtitle_text])
            extracted_pairs = parse_pdf_pairs(subtitle_text)
            
            # 檢測差異
            new_items, potential_errors = detect_differences_with_location(extracted_pairs_with_location, termbase)
            
            # PDF相關變數設為空
            pages_text = []
            image_inconsistencies = []
        
        # 顯示結果
        st.success(f"✅ 處理完成！")
        
        # 統計資訊
        if pdf_file:
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric("提取的對照", len(extracted_pairs))
            with col2:
                st.metric("新增內容", len(new_items))
            with col3:
                st.metric("潛在錯誤", len(potential_errors))
            with col4:
                st.metric("圖片文字問題", len(image_inconsistencies))
            with col5:
                st.metric("PDF頁數", len(pages_text))
        else:
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric("提取的對照", len(extracted_pairs))
            with col2:
                st.metric("新增內容", len(new_items))
            with col3:
                st.metric("潛在錯誤", len(potential_errors))
            with col4:
                st.metric("字幕問題", len(subtitle_inconsistencies))
            with col5:
                st.metric("字幕數量", len(subtitles))
        
        # 快速上傳按鈕（如果有新內容）
        if not new_items.empty:
            st.write("---")
            st.subheader("🚀 快速上傳")
            
            # 檢查非重複內容
            non_duplicate_count = 0
            for _, new_row in new_items.iterrows():
                existing_match = termbase[
                    (termbase["en_canonical"] == new_row["en_canonical"]) |
                    (termbase["zh_canonical"] == new_row["zh_canonical"])
                ]
                if existing_match.empty:
                    non_duplicate_count += 1
            
            if non_duplicate_count > 0:
                st.success(f"🎯 發現 {non_duplicate_count} 條非重複內容可上傳")
                
                if st.button("📤 立即上傳到 Google Sheets", type="primary", use_container_width=True, help="點擊此按鈕快速上傳非重複內容"):
                    if ws is not None:
                        try:
                            # 準備非重複的新內容
                            new_data = []
                            current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            
                            for _, row in new_items.iterrows():
                                # 檢查是否重複
                                existing_match = termbase[
                                    (termbase["en_canonical"] == row["en_canonical"]) |
                                    (termbase["zh_canonical"] == row["zh_canonical"])
                                ]
                                
                                if existing_match.empty:  # 只有非重複的才添加
                                    new_row = {
                                        "en_canonical": row["en_canonical"],
                                        "zh_canonical": row["zh_canonical"],
                                        "abbr": row["abbr"],
                                        "first_mention_style": "ZH(EN;ABBR)",
                                        "variant (錯誤用法)": "",
                                        "status": "新增待確認" if auto_mark_pending else "已確認",
                                        "added_date": current_date,
                                        "翻譯來源": "PDF自動提取"
                                    }
                                    new_data.append(new_row)
                            
                            if new_data:
                                new_df = pd.DataFrame(new_data)
                                # 合併現有詞庫和新內容
                                combined = pd.concat([termbase, new_df], ignore_index=True)
                                combined = standardize_master(combined)
                                
                                # 寫入Google Sheets
                                write_master_to_ws(ws, combined)
                                st.success(f"🎉 成功上傳 {len(new_df)} 條內容到 Google Sheets！")
                                
                                # 更新本地詞庫
                                termbase = combined
                                
                                # 顯示慶祝效果
                                st.balloons()
                                
                                # 重新載入頁面
                                st.rerun()
                            else:
                                st.warning("⚠️ 沒有非重複內容可上傳")
                                
                        except Exception as e:
                            st.error(f"❌ 上傳失敗：{e}")
                            st.error("請檢查 Google Sheets 連線和權限設定")
                    else:
                        st.error("❌ 無法連線到 Google Sheets")
            else:
                st.info("ℹ️ 所有內容都與現有詞庫重複，無需上傳")
        
        # 分頁顯示結果
        if pdf_file:
            tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📋 提取的對照", "🔍 錯字檢測", "🖼️ 圖片文字檢測", "🆕 新增內容", "⚠️ 潛在錯誤", "📄 原始文字"])
        else:
            tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📋 提取的對照", "🔍 錯字檢測", "📺 字幕檢測", "🆕 新增內容", "⚠️ 潛在錯誤", "📄 字幕內容"])
        
        with tab1:
            st.subheader("📋 從PDF中提取的中英文對照（含位置信息）")
            if not extracted_pairs_with_location.empty:
                # 顯示帶位置信息的表格
                display_df = extracted_pairs_with_location[["en_canonical", "zh_canonical", "abbr", "page", "position", "context"]].copy()
                display_df.columns = ["英文", "中文", "縮寫", "頁碼", "位置", "上下文"]
                st.dataframe(display_df, use_container_width=True)
                
                # 錯字檢查功能
                st.write("---")
                st.subheader("🔍 錯字檢查")
                
                # 讓用戶選擇要檢查的項目
                if not extracted_pairs_with_location.empty:
                    selected_items = st.multiselect(
                        "選擇要檢查的項目：",
                        options=[f"第{row['page']}頁: {row['zh_canonical']} ({row['en_canonical']})" 
                                for _, row in extracted_pairs_with_location.iterrows()],
                        help="選擇您懷疑有錯字的項目進行檢查"
                    )
                    
                    if selected_items:
                        st.write("### 📍 選中項目的詳細位置信息：")
                        for item in selected_items:
                            # 解析選中的項目
                            page_match = re.search(r"第(\d+)頁:", item)
                            zh_match = re.search(r": (.+?) \(", item)
                            en_match = re.search(r"\((.+?)\)", item)
                            
                            if page_match and zh_match and en_match:
                                page_num = int(page_match.group(1))
                                zh_text = zh_match.group(1)
                                en_text = en_match.group(1)
                                
                                # 找到對應的行
                                matching_row = extracted_pairs_with_location[
                                    (extracted_pairs_with_location["page"] == page_num) &
                                    (extracted_pairs_with_location["zh_canonical"] == zh_text) &
                                    (extracted_pairs_with_location["en_canonical"] == en_text)
                                ]
                                
                                if not matching_row.empty:
                                    row = matching_row.iloc[0]
                                    st.write(f"**📍 位置：第 {row['page']} 頁，位置 {row['position']}**")
                                    st.write(f"**📝 內容：{row['zh_canonical']} ({row['en_canonical']})**")
                                    st.write(f"**📄 上下文：**")
                                    st.code(row['context'], language="text")
                                    st.write("---")
            else:
                st.info("未找到中英文對照")
        
        with tab2:
            st.subheader("🆕 新增內容（詞庫中沒有的）")
            if not new_items.empty:
                st.info(f"🎯 發現 {len(new_items)} 個新內容，需要添加到詞庫")
                
                # 準備新增的資料
                new_data = []
                current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                for _, row in new_items.iterrows():
                    new_row = {
                        "en_canonical": row["en_canonical"],
                        "zh_canonical": row["zh_canonical"],
                        "abbr": row["abbr"],
                        "first_mention_style": "ZH(EN;ABBR)",
                        "variant (錯誤用法)": "",
                        "status": "新增待確認" if auto_mark_pending else "已確認",
                        "added_date": current_date,
                        "翻譯來源": "PDF自動提取"
                    }
                    new_data.append(new_row)
                
                new_df = pd.DataFrame(new_data)
                
                # 顯示新增內容
                st.write("📋 **即將新增的內容：**")
                st.dataframe(new_df, use_container_width=True)
                
                # 檢查重複內容
                st.write("🔍 **重複檢查：**")
                duplicate_check = []
                for _, new_row in new_df.iterrows():
                    # 檢查是否與現有詞庫重複
                    existing_match = termbase[
                        (termbase["en_canonical"] == new_row["en_canonical"]) |
                        (termbase["zh_canonical"] == new_row["zh_canonical"])
                    ]
                    
                    if not existing_match.empty:
                        duplicate_check.append({
                            "新內容": f"{new_row['zh_canonical']} ({new_row['en_canonical']})",
                            "重複項目": f"{existing_match.iloc[0]['zh_canonical']} ({existing_match.iloc[0]['en_canonical']})",
                            "狀態": "⚠️ 重複"
                        })
                    else:
                        duplicate_check.append({
                            "新內容": f"{new_row['zh_canonical']} ({new_row['en_canonical']})",
                            "重複項目": "無",
                            "狀態": "✅ 可新增"
                        })
                
                duplicate_df = pd.DataFrame(duplicate_check)
                st.dataframe(duplicate_df, use_container_width=True)
                
                # 過濾掉重複的內容
                non_duplicate_df = new_df.copy()
                for _, new_row in new_df.iterrows():
                    existing_match = termbase[
                        (termbase["en_canonical"] == new_row["en_canonical"]) |
                        (termbase["zh_canonical"] == new_row["zh_canonical"])
                    ]
                    if not existing_match.empty:
                        non_duplicate_df = non_duplicate_df[
                            ~((non_duplicate_df["en_canonical"] == new_row["en_canonical"]) &
                              (non_duplicate_df["zh_canonical"] == new_row["zh_canonical"]))
                        ]
                
                if not non_duplicate_df.empty:
                    st.success(f"✅ 過濾後有 {len(non_duplicate_df)} 條非重複內容可新增")
                    
                    # 主要上傳按鈕
                    st.write("---")
                    st.subheader("🚀 上傳到 Google Sheets")
                    
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        if st.button("📤 一鍵上傳到 Google Sheets", type="primary", use_container_width=True, help="點擊此按鈕將非重複內容自動上傳到 Google Sheets"):
                            if ws is not None:
                                try:
                                    # 合併現有詞庫和非重複新內容
                                    combined = pd.concat([termbase, non_duplicate_df], ignore_index=True)
                                    combined = standardize_master(combined)
                                    
                                    # 寫入Google Sheets
                                    write_master_to_ws(ws, combined)
                                    st.success(f"🎉 成功上傳 {len(non_duplicate_df)} 條內容到 Google Sheets！")
                                    
                                    # 更新本地詞庫
                                    termbase = combined
                                    
                                    # 顯示更新後的統計
                                    st.balloons()
                                    
                                    # 重新載入頁面顯示更新後的統計
                                    st.rerun()
                                    
                                except Exception as e:
                                    st.error(f"❌ 上傳失敗：{e}")
                                    st.error("請檢查 Google Sheets 連線和權限設定")
                            else:
                                st.error("❌ 無法連線到 Google Sheets")
                    
                    with col2:
                        if st.button("🔄 重新檢查", use_container_width=True):
                            st.rerun()
                else:
                    st.warning("⚠️ 所有內容都與現有詞庫重複，無需新增")
            else:
                st.success("✅ 沒有發現新內容")
        
        with tab2:
            st.subheader("🔍 錯字檢測")
            st.write("**功能：** 自動檢測PDF中的中英文對照是否與詞庫一致，找出可能的錯字")
            
            if not extracted_pairs_with_location.empty and not termbase.empty:
                # 檢測錯字
                typo_detections = []
                
                for _, extracted_row in extracted_pairs_with_location.iterrows():
                    # 檢查是否在詞庫中存在
                    existing_match = termbase[
                        (termbase["en_canonical"] == extracted_row["en_canonical"]) |
                        (termbase["zh_canonical"] == extracted_row["zh_canonical"])
                    ]
                    
                    if not existing_match.empty:
                        # 檢查是否完全匹配
                        exact_match = existing_match[
                            (existing_match["en_canonical"] == extracted_row["en_canonical"]) &
                            (existing_match["zh_canonical"] == extracted_row["zh_canonical"])
                        ]
                        
                        if exact_match.empty:
                            # 部分匹配，可能是錯字
                            typo_detections.append({
                                "頁碼": extracted_row["page"],
                                "位置": extracted_row["position"],
                                "PDF內容": f"{extracted_row['zh_canonical']} ({extracted_row['en_canonical']})",
                                "詞庫內容": f"{existing_match.iloc[0]['zh_canonical']} ({existing_match.iloc[0]['en_canonical']})",
                                "問題類型": "中英文不匹配",
                                "上下文": extracted_row["context"]
                            })
                    else:
                        # 完全不在詞庫中
                        typo_detections.append({
                            "頁碼": extracted_row["page"],
                            "位置": extracted_row["position"],
                            "PDF內容": f"{extracted_row['zh_canonical']} ({extracted_row['en_canonical']})",
                            "詞庫內容": "未找到",
                            "問題類型": "詞庫中不存在",
                            "上下文": extracted_row["context"]
                        })
                
                if typo_detections:
                    st.warning(f"⚠️ 發現 {len(typo_detections)} 個可能的錯字或問題")
                    
                    # 顯示錯字檢測結果
                    typo_df = pd.DataFrame(typo_detections)
                    st.dataframe(typo_df, use_container_width=True)
                    
                    # 詳細查看功能
                    st.write("---")
                    st.subheader("📍 詳細位置信息")
                    
                    for i, typo in enumerate(typo_detections):
                        with st.expander(f"問題 {i+1}: 第{typo['頁碼']}頁 - {typo['PDF內容']}"):
                            st.write(f"**📍 位置：** 第 {typo['頁碼']} 頁，位置 {typo['位置']}")
                            st.write(f"**📝 PDF內容：** {typo['PDF內容']}")
                            st.write(f"**📚 詞庫內容：** {typo['詞庫內容']}")
                            st.write(f"**⚠️ 問題類型：** {typo['問題類型']}")
                            st.write(f"**📄 上下文：**")
                            st.code(typo['上下文'], language="text")
                            
                            # 提供修正建議
                            if typo['問題類型'] == "中英文不匹配":
                                st.write("**💡 修正建議：**")
                                st.write("1. 檢查中文翻譯是否正確")
                                st.write("2. 檢查英文拼寫是否正確")
                                st.write("3. 確認是否為同義詞或近義詞")
                            elif typo['問題類型'] == "詞庫中不存在":
                                st.write("**💡 修正建議：**")
                                st.write("1. 檢查是否為新術語")
                                st.write("2. 檢查是否有拼寫錯誤")
                                st.write("3. 考慮添加到詞庫")
                else:
                    st.success("✅ 未發現錯字，所有中英文對照都與詞庫一致")
            else:
                if extracted_pairs_with_location.empty:
                    st.info("ℹ️ 未提取到中英文對照")
                if termbase.empty:
                    st.info("ℹ️ 詞庫為空，無法進行錯字檢測")
        
        with tab3:
            if pdf_file:
                st.subheader("🖼️ 圖片文字檢測")
                st.write("**功能：** 檢測圖片中的文字是否與詞庫一致，找出缺少翻譯的內容")
                
                if image_inconsistencies:
                    st.warning(f"⚠️ 發現 {len(image_inconsistencies)} 個圖片文字問題")
                    
                    # 創建顯示表格
                    display_inconsistencies = []
                    for item in image_inconsistencies:
                        if item["type"] == "圖片英文缺少中文翻譯":
                            display_inconsistencies.append({
                                "頁碼": item["page"],
                                "位置": item["position"],
                                "問題類型": item["type"],
                                "英文詞彙": item["english_word"],
                                "缺少的中文": item["expected_chinese"],
                                "上下文": item["context"][:100] + "..." if len(item["context"]) > 100 else item["context"]
                            })
                        else:
                            display_inconsistencies.append({
                                "頁碼": item["page"],
                                "位置": item["position"],
                                "問題類型": item["type"],
                                "中文詞彙": item["chinese_word"],
                                "缺少的英文": item["expected_english"],
                                "上下文": item["context"][:100] + "..." if len(item["context"]) > 100 else item["context"]
                            })
                    
                    inconsistencies_df = pd.DataFrame(display_inconsistencies)
                    st.dataframe(inconsistencies_df, use_container_width=True)
                    
                    # 詳細查看功能
                    st.write("---")
                    st.subheader("📍 詳細問題信息")
                    
                    for i, item in enumerate(image_inconsistencies):
                        if item["type"] == "圖片英文缺少中文翻譯":
                            with st.expander(f"問題 {i+1}: 第{item['page']}頁 - {item['english_word']} 缺少中文翻譯"):
                                st.write(f"**📍 位置：** 第 {item['page']} 頁，位置 {item['position']}")
                                st.write(f"**📝 英文詞彙：** {item['english_word']}")
                                st.write(f"**📚 缺少的中文翻譯：** {item['expected_chinese']}")
                                st.write(f"**📄 上下文：**")
                                st.code(item['context'], language="text")
                                st.write("**💡 建議：** 在圖片中添加中文翻譯或檢查是否為新術語")
                        else:
                            with st.expander(f"問題 {i+1}: 第{item['page']}頁 - {item['chinese_word']} 缺少英文翻譯"):
                                st.write(f"**📍 位置：** 第 {item['page']} 頁，位置 {item['position']}")
                                st.write(f"**📝 中文詞彙：** {item['chinese_word']}")
                                st.write(f"**📚 缺少的英文翻譯：** {item['expected_english']}")
                                st.write(f"**📄 上下文：**")
                                st.code(item['context'], language="text")
                                st.write("**💡 建議：** 在圖片中添加英文翻譯或檢查是否為新術語")
                else:
                    st.success("✅ 圖片文字檢測完成，未發現問題")
                    st.info("ℹ️ 所有圖片中的文字都與詞庫一致，或詞庫為空無法檢測")
            else:
                st.subheader("📺 字幕檢測")
                st.write("**功能：** 檢測字幕中的翻譯是否與詞庫一致，找出缺少翻譯的內容")
                
                if subtitle_inconsistencies:
                    st.warning(f"⚠️ 發現 {len(subtitle_inconsistencies)} 個字幕問題")
                    
                    # 創建顯示表格
                    display_inconsistencies = []
                    for item in subtitle_inconsistencies:
                        if item["type"] == "字幕英文缺少中文翻譯":
                            display_inconsistencies.append({
                                "時間戳": item["timestamp"],
                                "問題類型": item["type"],
                                "英文詞彙": item["english_word"],
                                "缺少的中文": item["expected_chinese"],
                                "字幕內容": item["subtitle_text"][:100] + "..." if len(item["subtitle_text"]) > 100 else item["subtitle_text"]
                            })
                        else:
                            display_inconsistencies.append({
                                "時間戳": item["timestamp"],
                                "問題類型": item["type"],
                                "中文詞彙": item["chinese_word"],
                                "缺少的英文": item["expected_english"],
                                "字幕內容": item["subtitle_text"][:100] + "..." if len(item["subtitle_text"]) > 100 else item["subtitle_text"]
                            })
                    
                    inconsistencies_df = pd.DataFrame(display_inconsistencies)
                    st.dataframe(inconsistencies_df, use_container_width=True)
                    
                    # 詳細查看功能
                    st.write("---")
                    st.subheader("📍 詳細問題信息")
                    
                    for i, item in enumerate(subtitle_inconsistencies):
                        if item["type"] == "字幕英文缺少中文翻譯":
                            with st.expander(f"問題 {i+1}: {item['timestamp']} - {item['english_word']} 缺少中文翻譯"):
                                st.write(f"**⏰ 時間戳：** {item['timestamp']}")
                                st.write(f"**📝 英文詞彙：** {item['english_word']}")
                                st.write(f"**📚 缺少的中文翻譯：** {item['expected_chinese']}")
                                st.write(f"**📄 字幕內容：**")
                                st.code(item['subtitle_text'], language="text")
                                st.write("**💡 建議：** 在字幕中添加中文翻譯或檢查是否為新術語")
                        else:
                            with st.expander(f"問題 {i+1}: {item['timestamp']} - {item['chinese_word']} 缺少英文翻譯"):
                                st.write(f"**⏰ 時間戳：** {item['timestamp']}")
                                st.write(f"**📝 中文詞彙：** {item['chinese_word']}")
                                st.write(f"**📚 缺少的英文翻譯：** {item['expected_english']}")
                                st.write(f"**📄 字幕內容：**")
                                st.code(item['subtitle_text'], language="text")
                                st.write("**💡 建議：** 在字幕中添加英文翻譯或檢查是否為新術語")
                else:
                    st.success("✅ 字幕檢測完成，未發現問題")
                    st.info("ℹ️ 所有字幕都與詞庫一致，或詞庫為空無法檢測")
        
        with tab4:
            st.subheader("🆕 新增內容（詞庫中沒有的）")
            if not new_items.empty:
                st.info(f"🎯 發現 {len(new_items)} 個新內容，需要添加到詞庫")
        
        with tab5:
            st.subheader("⚠️ 潛在錯誤（需要人工檢查）")
            if potential_errors:
                # 根據狀態分類錯誤
                confirmed_errors = [e for e in potential_errors if e.get('termbase_status') == '已確認']
                pending_errors = [e for e in potential_errors if e.get('termbase_status') == '新增待確認']
                other_errors = [e for e in potential_errors if e.get('termbase_status') not in ['已確認', '新增待確認']]
                
                # 顯示統計信息
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("總錯誤數", len(potential_errors))
                with col2:
                    st.metric("已確認詞庫錯誤", len(confirmed_errors), delta=f"{len(confirmed_errors)}個嚴重錯誤")
                with col3:
                    st.metric("待確認詞庫錯誤", len(pending_errors), delta=f"{len(pending_errors)}個待審核")
                
                # 優先顯示已確認詞庫的錯誤（最嚴重）
                if confirmed_errors:
                    st.error(f"🚨 發現 {len(confirmed_errors)} 個與已確認詞庫不符的嚴重錯誤！")
                    
                    # 創建已確認錯誤的顯示表格
                    display_confirmed_errors = []
                    for error in confirmed_errors:
                        if 'page' in error:
                            display_confirmed_errors.append({
                                "頁碼": error['page'],
                                "位置": error['position'],
                                "問題類型": error['type'],
                                "詞庫狀態": "✅ 已確認",
                                "提取內容": f"{error.get('zh_extracted', error.get('zh_canonical', ''))} ({error.get('en_extracted', error.get('en_canonical', ''))})",
                                "詞庫內容": f"{error.get('zh_termbase', '')} ({error.get('en_termbase', '')})"
                            })
                        else:
                            display_confirmed_errors.append({
                                "頁碼": "未知",
                                "位置": "未知",
                                "問題類型": error['type'],
                                "詞庫狀態": "✅ 已確認",
                                "提取內容": f"{error.get('zh_extracted', error.get('zh_canonical', ''))} ({error.get('en_extracted', error.get('en_canonical', ''))})",
                                "詞庫內容": f"{error.get('zh_termbase', '')} ({error.get('en_termbase', '')})"
                            })
                    
                    confirmed_errors_df = pd.DataFrame(display_confirmed_errors)
                    st.dataframe(confirmed_errors_df, use_container_width=True)
                
                # 顯示待確認詞庫的錯誤
                if pending_errors:
                    st.warning(f"⚠️ 發現 {len(pending_errors)} 個與待確認詞庫不符的錯誤")
                    
                    # 創建待確認錯誤的顯示表格
                    display_pending_errors = []
                    for error in pending_errors:
                        if 'page' in error:
                            display_pending_errors.append({
                                "頁碼": error['page'],
                                "位置": error['position'],
                                "問題類型": error['type'],
                                "詞庫狀態": "⏳ 待確認",
                                "提取內容": f"{error.get('zh_extracted', error.get('zh_canonical', ''))} ({error.get('en_extracted', error.get('en_canonical', ''))})",
                                "詞庫內容": f"{error.get('zh_termbase', '')} ({error.get('en_termbase', '')})"
                            })
                        else:
                            display_pending_errors.append({
                                "頁碼": "未知",
                                "位置": "未知",
                                "問題類型": error['type'],
                                "詞庫狀態": "⏳ 待確認",
                                "提取內容": f"{error.get('zh_extracted', error.get('zh_canonical', ''))} ({error.get('en_extracted', error.get('en_canonical', ''))})",
                                "詞庫內容": f"{error.get('zh_termbase', '')} ({error.get('en_termbase', '')})"
                            })
                    
                    pending_errors_df = pd.DataFrame(display_pending_errors)
                    st.dataframe(pending_errors_df, use_container_width=True)
                
                # 顯示其他錯誤
                if other_errors:
                    st.info(f"ℹ️ 發現 {len(other_errors)} 個其他狀態的錯誤")
                    
                    # 創建其他錯誤的顯示表格
                    display_other_errors = []
                    for error in other_errors:
                        if 'page' in error:
                            display_other_errors.append({
                                "頁碼": error['page'],
                                "位置": error['position'],
                                "問題類型": error['type'],
                                "詞庫狀態": error.get('termbase_status', '未知'),
                                "提取內容": f"{error.get('zh_extracted', error.get('zh_canonical', ''))} ({error.get('en_extracted', error.get('en_canonical', ''))})",
                                "詞庫內容": f"{error.get('zh_termbase', '')} ({error.get('en_termbase', '')})"
                            })
                        else:
                            display_other_errors.append({
                                "頁碼": "未知",
                                "位置": "未知",
                                "問題類型": error['type'],
                                "詞庫狀態": error.get('termbase_status', '未知'),
                                "提取內容": f"{error.get('zh_extracted', error.get('zh_canonical', ''))} ({error.get('en_extracted', error.get('en_canonical', ''))})",
                                "詞庫內容": f"{error.get('zh_termbase', '')} ({error.get('en_termbase', '')})"
                            })
                    
                    other_errors_df = pd.DataFrame(display_other_errors)
                    st.dataframe(other_errors_df, use_container_width=True)
                
                # 提供修正選項
                st.write("---")
                st.subheader("📍 詳細位置信息")
                for i, error in enumerate(potential_errors):
                    if 'page' in error:
                        with st.expander(f"錯誤 {i+1}: 第{error['page']}頁 - {error['type']}"):
                            st.write(f"**📍 位置：** 第 {error['page']} 頁，位置 {error['position']}")
                            
                            if error['type'] == "英文相同，中文不同":
                                st.write(f"**📝 英文:** {error['en_canonical']}")
                                st.write(f"**📝 提取的中文:** {error['zh_extracted']}")
                                st.write(f"**📚 詞庫中的中文:** {error['zh_termbase']}")
                            else:
                                st.write(f"**📝 中文:** {error['zh_canonical']}")
                                st.write(f"**📝 提取的英文:** {error['en_extracted']}")
                                st.write(f"**📚 詞庫中的英文:** {error['en_termbase']}")
                            
                            if 'context' in error:
                                st.write(f"**📄 上下文：**")
                                st.code(error['context'], language="text")
                            
                            st.write("**💡 請檢查哪個版本是正確的**")
                    else:
                        with st.expander(f"錯誤 {i+1}: {error['type']}"):
                            if error['type'] == "英文相同，中文不同":
                                st.write(f"**📝 英文:** {error['en_canonical']}")
                                st.write(f"**📝 提取的中文:** {error['zh_extracted']}")
                                st.write(f"**📚 詞庫中的中文:** {error['zh_termbase']}")
                            else:
                                st.write(f"**📝 中文:** {error['zh_canonical']}")
                                st.write(f"**📝 提取的英文:** {error['en_extracted']}")
                                st.write(f"**📚 詞庫中的英文:** {error['en_termbase']}")
                            
                            st.write("**💡 請檢查哪個版本是正確的**")
            else:
                st.success("✅ 沒有發現潛在錯誤")
        
        with tab6:
            if pdf_file:
                st.subheader("📄 PDF原始文字（前1000字符）")
                st.text_area("提取的文字", full_text[:1000] + "..." if len(full_text) > 1000 else full_text, height=300)
            else:
                st.subheader("📄 字幕內容")
                if subtitles:
                    # 顯示字幕列表
                    subtitle_df = pd.DataFrame([
                        {
                            "序號": i+1,
                            "時間戳": sub["timestamp"],
                            "字幕內容": sub["text"]
                        }
                        for i, sub in enumerate(subtitles)
                    ])
                    st.dataframe(subtitle_df, use_container_width=True)
                    
                    # 顯示完整字幕文本
                    st.write("---")
                    st.subheader("📝 完整字幕文本")
                    full_subtitle_text = "\n\n".join([
                        f"{sub['timestamp']}\n{sub['text']}"
                        for sub in subtitles
                    ])
                    st.text_area("字幕內容", full_subtitle_text, height=400)
                else:
                    st.info("ℹ️ 沒有提取到字幕內容")

# 顯示詞庫內容
st.write("---")
st.subheader("📚 當前詞庫內容")
if not termbase.empty:
    # 過濾選項
    status_filter = st.selectbox("按狀態過濾", ["全部", "已確認", "新增待確認"])
    if status_filter != "全部":
        filtered_termbase = termbase[termbase["status"] == status_filter]
    else:
        filtered_termbase = termbase
    
    # 顯示詞庫表格
    st.dataframe(filtered_termbase, use_container_width=True)
    
    # 下載選項
    csv = filtered_termbase.to_csv(index=False)
    st.download_button(
        label="📥 下載詞庫 (CSV)",
        data=csv,
        file_name=f"termbase_{status_filter}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv"
    )
else:
    st.info("ℹ️ 詞庫為空")
