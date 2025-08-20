#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import gspread
from google.oauth2.service_account import Credentials

def extract_sheet_id(url_or_id: str) -> str:
    """從URL或ID中提取Sheet ID"""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()

def test_gsheets_connection():
    """測試Google Sheets連線"""
    print("🔍 開始測試Google Sheets連線...")
    
    # 讀取JSON金鑰
    try:
        with open('service_account_key.json', 'r', encoding='utf-8') as f:
            creds_json = json.load(f)
        print("✅ JSON金鑰讀取成功")
    except Exception as e:
        print(f"❌ JSON金鑰讀取失敗: {e}")
        return False
    
    # 設定Google Sheets URL
    sheet_url = "https://docs.google.com/spreadsheets/d/1UbBCtcUscJ65lCBkjLmZod3L7lR5e85ztgyPcetwGWs/edit?usp=sharing"
    sheet_id = extract_sheet_id(sheet_url)
    ws_name = "termbase_master"
    
    print(f"📊 Sheet ID: {sheet_id}")
    print(f"📋 工作表名稱: {ws_name}")
    
    try:
        # 設定認證
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
        print("✅ 認證設定成功")
        
        # 連線到Google Sheets
        gc = gspread.authorize(creds)
        print("✅ gspread授權成功")
        
        # 開啟試算表
        sh = gc.open_by_key(sheet_id)
        print(f"✅ 試算表開啟成功: {sh.title}")
        
        # 檢查工作表是否存在
        try:
            ws = sh.worksheet(ws_name)
            print(f"✅ 工作表 '{ws_name}' 存在")
        except gspread.exceptions.WorksheetNotFound:
            print(f"⚠️  工作表 '{ws_name}' 不存在，將創建新工作表")
            ws = sh.add_worksheet(title=ws_name, rows=1000, cols=10)
            print(f"✅ 已創建工作表 '{ws_name}'")
        
        # 測試讀取資料
        values = ws.get_all_values()
        print(f"✅ 成功讀取資料，共 {len(values)} 行")
        
        if values:
            print("📋 前5行資料:")
            for i, row in enumerate(values[:5]):
                print(f"  第{i+1}行: {row}")
        else:
            print("📋 工作表為空")
        
        # 測試寫入資料
        test_data = [["en_canonical", "zh_canonical", "abbr", "first_mention_style", "variant (錯誤用法)", "status", "added_date"]]
        ws.update('A1:G1', test_data)
        print("✅ 測試寫入成功")
        
        return True
        
    except Exception as e:
        print(f"❌ 連線失敗: {e}")
        print(f"🔧 錯誤類型: {type(e).__name__}")
        import traceback
        print(f"🔍 詳細錯誤信息:")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_gsheets_connection()
    if success:
        print("\n🎉 Google Sheets連線測試成功！")
    else:
        print("\n💡 故障排除建議:")
        print("1. 確認Google Cloud Project已啟用Google Sheets API")
        print("2. 確認Service Account有適當權限")
        print("3. 確認Service Account email已加入試算表編輯者清單")
        print("4. 檢查網路連線")
