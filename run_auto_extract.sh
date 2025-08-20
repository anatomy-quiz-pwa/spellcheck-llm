#!/bin/bash

# PDF自動提取中英文翻譯對比系統 - 快速啟動腳本

echo "🚀 啟動PDF自動提取中英文翻譯對比系統..."
echo ""

# 檢查Python是否安裝
if ! command -v python3 &> /dev/null; then
    echo "❌ 錯誤：未找到Python3，請先安裝Python"
    exit 1
fi

# 檢查是否安裝了依賴
echo "📦 檢查依賴..."
if ! python3 -c "import streamlit, pandas, pypdf" 2>/dev/null; then
    echo "⚠️  檢測到缺少依賴，正在安裝..."
    pip3 install -r requirements.txt
fi

# 啟動應用
echo "🌐 啟動Streamlit應用..."
echo "📱 應用將在瀏覽器中打開：http://localhost:8501"
echo "🔄 按 Ctrl+C 停止應用"
echo ""

streamlit run app_streamlit_auto_extract.py

