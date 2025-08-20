#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import gspread
from google.oauth2.service_account import Credentials

def quick_test():
    print("🚀 快速測試 Google Sheets 連線...")
    
    try:
        # 讀取金鑰
        with open('service_account_key.json', 'r') as f:
            creds_json = json.load(f)
        
        # 設定認證
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
        gc = gspread.authorize(creds)
        
        # 測試連線
        sheet_id = "1UbBCtcUscJ65lCBkjLmZod3L7lR5e85ztgyPcetwGWs"
        sh = gc.open_by_key(sheet_id)
        
        print(f"✅ 成功連線到試算表: {sh.title}")
        print(f"📊 試算表 ID: {sh.id}")
        print(f"🔗 試算表 URL: {sh.url}")
        
        # 列出所有工作表
        worksheets = sh.worksheets()
        print(f"📋 工作表列表:")
        for ws in worksheets:
            print(f"   - {ws.title} (ID: {ws.id})")
        
        return True
        
    except Exception as e:
        print(f"❌ 連線失敗: {e}")
        return False

if __name__ == "__main__":
    success = quick_test()
    if success:
        print("\n🎉 連線測試成功！")
    else:
        print("\n💡 請確認:")
        print("1. Google Sheets API 已啟用")
        print("2. Service Account 已加入試算表編輯者清單")
