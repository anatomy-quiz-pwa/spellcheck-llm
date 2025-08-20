# 🔍 錯字校正LLM

智能PDF錯字檢測和術語一致性檢查工具

## 🚀 功能特色

- **PDF文字提取**: 自動提取PDF中的文字內容
- **智能錯字檢測**: 基於詞庫進行術語一致性檢查
- **密碼保護**: 安全的訪問控制
- **雲端部署**: 支援Render等雲端平台
- **結果匯出**: 支援CSV格式結果下載

## 📋 系統需求

- Python 3.11+
- Streamlit
- PyPDF2
- pandas
- pytesseract (OCR功能)

## 🔧 本地安裝

1. **克隆專案**
```bash
git clone <your-repo-url>
cd 錯字校正LLM
```

2. **安裝依賴**
```bash
pip install -r requirements.txt
```

3. **啟動應用**
```bash
streamlit run app_streamlit_cloud.py
```

## ☁️ 雲端部署 (Render)

1. **推送到GitHub**
```bash
git add .
git commit -m "Initial commit"
git push origin main
```

2. **在Render創建服務**
   - 登入 [Render](https://render.com)
   - 點擊 "New +" → "Web Service"
   - 連接GitHub倉庫
   - 選擇 `app_streamlit_cloud.py` 作為啟動文件
   - 設置環境變數

3. **設置環境變數**
   - 在Render控制台添加環境變數：
   - `PASSWORD`: 設置訪問密碼

## 🔐 密碼設置

在Render的環境變數中設置：
- `PASSWORD`: 您的訪問密碼

## 📁 文件結構

```
錯字校正LLM/
├── app_streamlit_cloud.py    # 雲端版本主程式
├── app_streamlit_auto_extract.py  # 本地完整版本
├── requirements.txt          # Python依賴
├── render.yaml              # Render部署配置
├── .streamlit/              # Streamlit配置
│   └── config.toml
└── README.md               # 說明文件
```

## 🎯 使用說明

1. **上傳PDF文件**: 支援標準PDF格式
2. **上傳詞庫**: CSV格式的中英文對照詞庫
3. **選擇處理選項**: 圖片文字提取、表格處理等
4. **查看分析結果**: 錯字檢測和術語一致性報告
5. **下載結果**: 匯出分析結果為CSV

## 🔒 安全注意事項

- 密碼保護功能確保只有授權用戶可訪問
- 文件處理在記憶體中進行，不會永久儲存
- 建議定期更新密碼

## �� 支援

如有問題，請聯繫開發團隊。
