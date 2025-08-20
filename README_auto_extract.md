# PDF自動提取中英文翻譯對比系統

## 📋 功能概述

這個系統專門用於：
1. **自動提取**PDF投影片中的中英文翻譯對照
2. **智能對比**Google Sheets詞庫中的現有資料
3. **檢測差異**並提醒人工檢查潛在錯誤
4. **自動新增**表格中沒有的內容，並標記為"新增待確認"

## 🚀 主要特色

### ✨ 智能提取
- 支援多種中英文對照格式：
  - `中文(英文;縮寫)`
  - `中文(英文)`
  - `英文(中文)`
  - `英文 - 中文`
  - `中文 - 英文`
- OCR後備功能，處理圖片型PDF
- 自動文字正規化處理

### 🔍 智能對比
- 自動檢測新增內容
- 識別潛在的翻譯錯誤
- 提供詳細的差異報告

### 📊 自動化管理
- 自動新增到Google Sheets
- 狀態管理（已確認/新增待確認）
- 時間戳記錄

## 🛠️ 安裝與設定

### 1. 安裝依賴
```bash
pip install -r requirements.txt
```

### 2. 安裝Tesseract OCR（可選）
```bash
# macOS
brew install tesseract tesseract-lang

# Ubuntu
sudo apt-get install tesseract-ocr tesseract-ocr-chi-tra

# Windows
# 下載並安裝 https://github.com/UB-Mannheim/tesseract/wiki
```

### 3. Google Sheets設定
1. 創建Google Cloud Project
2. 啟用Google Sheets API
3. 創建Service Account
4. 下載JSON憑證文件
5. 將Service Account email加入Google Sheets編輯者清單

## 🎯 使用方法

### 1. 啟動應用
```bash
streamlit run app_streamlit_auto_extract.py
```

### 2. 設定Google Sheets
- 在側邊欄輸入Google Sheets URL或ID
- 上傳Service Account JSON文件
- 設定工作表名稱（預設：termbase_master）

### 3. 上傳PDF
- 點擊"上傳投影片PDF"
- 系統會自動處理並提取中英文對照

### 4. 查看結果
系統會顯示四個分頁：
- **📋 提取的對照**：從PDF中提取的所有中英文對照
- **🆕 新增內容**：詞庫中沒有的新內容
- **⚠️ 潛在錯誤**：需要人工檢查的差異
- **📄 原始文字**：PDF的原始文字內容

### 5. 自動新增
- 點擊"🚀 自動新增到Google Sheets"
- 新內容會自動添加到Google Sheets並標記為"新增待確認"

## 📊 詞庫格式

Google Sheets應包含以下欄位：
- `en_canonical`：標準英文
- `zh_canonical`：標準中文
- `abbr`：縮寫
- `first_mention_style`：首次顯示規則
- `variant (錯誤用法)`：錯誤用法
- `status`：狀態（已確認/新增待確認）
- `added_date`：新增日期

## ⚙️ 設定選項

### OCR設定
- **啟用OCR後備**：當PDF文字提取失敗時使用OCR
- **OCR觸發門檻**：文字少於多少字符時觸發OCR
- **OCR語言**：支援的語言（預設：chi_tra+eng）

### 自動化設定
- **自動新增新內容**：是否自動新增到Google Sheets
- **自動標記為待確認**：新內容是否標記為待確認狀態

## 🔧 進階功能

### 1. 批量處理
可以連續上傳多個PDF文件，系統會累積處理結果。

### 2. 狀態管理
- 查看已確認和待確認的條目
- 手動修改狀態
- 按狀態篩選顯示

### 3. 錯誤檢測
系統會檢測以下類型的潛在錯誤：
- 英文相同但中文不同
- 中文相同但英文不同

## 🐛 故障排除

### 常見問題

1. **Google Sheets連線失敗**
   - 檢查Service Account JSON文件
   - 確認API已啟用
   - 確認email已加入編輯者清單

2. **OCR無法工作**
   - 確認已安裝Tesseract
   - 檢查語言包是否正確安裝

3. **提取結果為空**
   - 檢查PDF是否包含中英文對照
   - 嘗試啟用OCR功能
   - 檢查文字格式是否符合支援的模式

### 日誌查看
```bash
streamlit run app_streamlit_auto_extract.py --logger.level debug
```

## 📈 效能優化

- 對於大型PDF，建議分批處理
- 啟用OCR會增加處理時間
- 定期清理Google Sheets中的重複條目

## 🤝 貢獻

歡迎提交Issue和Pull Request來改善這個系統！

## 📄 授權

MIT License

