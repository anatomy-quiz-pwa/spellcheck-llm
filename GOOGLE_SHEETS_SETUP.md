# 🔗 Google Sheets 設置說明

## 📋 前置需求

1. **Google Cloud Project**
2. **Google Sheets API 啟用**
3. **服務帳戶憑證**

## 🚀 設置步驟

### 步驟 1: 創建 Google Cloud Project

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)
2. 創建新專案或選擇現有專案
3. 記下專案 ID

### 步驟 2: 啟用 Google Sheets API

1. 在 Google Cloud Console 中
2. 前往 "API 和服務" → "程式庫"
3. 搜尋 "Google Sheets API"
4. 點擊啟用

### 步驟 3: 創建服務帳戶

1. 前往 "API 和服務" → "憑證"
2. 點擊 "建立憑證" → "服務帳戶"
3. 填寫服務帳戶資訊：
   - 名稱：`spellcheck-llm`
   - 描述：`錯字校正LLM服務帳戶`
4. 點擊 "建立並繼續"
5. 跳過權限設置，點擊 "完成"

### 步驟 4: 下載憑證

1. 在服務帳戶列表中，點擊剛創建的服務帳戶
2. 前往 "金鑰" 標籤
3. 點擊 "新增金鑰" → "建立新金鑰"
4. 選擇 "JSON" 格式
5. 下載 JSON 文件

### 步驟 5: 重命名憑證文件

將下載的 JSON 文件重命名為：
```
service_account_key.json
```

並放置在專案根目錄中。

### 步驟 6: 設置 Google Sheets 權限

1. 創建新的 Google Sheets 文件
2. 點擊 "共用" 按鈕
3. 添加服務帳戶的電子郵件地址（在 JSON 文件中的 `client_email`）
4. 給予 "編輯者" 權限

### 步驟 7: 更新應用設定

在應用中更新以下設定：
- **Sheet URL**: 您的 Google Sheets 文件 URL
- **工作表名稱**: 預設為 `termbase_master`

## 🔒 安全注意事項

- **不要** 將 `service_account_key.json` 上傳到 Git
- 該文件已加入 `.gitignore`
- 在雲端部署時，使用環境變數

## 🐛 常見問題

### Q: 找不到預設 JSON 文件
**A**: 確保 `service_account_key.json` 在專案根目錄中

### Q: 權限錯誤
**A**: 檢查服務帳戶是否有 Google Sheets 的編輯權限

### Q: API 配額限制
**A**: Google Sheets API 有使用限制，免費版每天 300 次請求

## 📞 支援

如有問題，請檢查：
1. JSON 文件格式是否正確
2. Google Sheets API 是否已啟用
3. 服務帳戶權限是否正確
