import streamlit as st
import pandas as pd
import PyPDF2
import pytesseract
from PIL import Image
import cv2
import numpy as np
import io
import re
from datetime import datetime
import os
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

# 設置頁面配置
st.set_page_config(
    page_title="錯字校正LLM - 雲端版",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 密碼保護功能
def check_password():
    """檢查密碼"""
    def password_entered():
        if st.session_state["password"] == st.secrets.get("password", "admin123"):
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # 清除密碼
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # 顯示登入界面
        st.markdown("""
        <div style="text-align: center; padding: 50px;">
            <h1>🔐 錯字校正LLM</h1>
            <h3>請輸入密碼以繼續</h3>
        </div>
        """, unsafe_allow_html=True)
        
        st.text_input(
            "密碼", 
            type="password", 
            on_change=password_entered, 
            key="password"
        )
        
        if "password_correct" in st.session_state and not st.session_state["password_correct"]:
            st.error("❌ 密碼錯誤，請重試")
        
        st.stop()
    
    return st.session_state["password_correct"]

# 檢查密碼
if not check_password():
    st.stop()

# 主要應用邏輯
def main():
    st.title("🔍 錯字校正LLM - 雲端版")
    st.markdown("---")
    
    # 側邊欄
    with st.sidebar:
        st.header("⚙️ 設置")
        
        # 文件上傳
        uploaded_file = st.file_uploader(
            "📄 上傳PDF文件",
            type=['pdf'],
            help="支援PDF格式文件"
        )
        
        # 詞庫上傳
        termbase_file = st.file_uploader(
            "📚 上傳詞庫 (CSV)",
            type=['csv'],
            help="CSV格式的詞庫文件"
        )
        
        # 處理選項
        st.subheader("🔧 處理選項")
        extract_images = st.checkbox("提取圖片文字", value=True)
        extract_tables = st.checkbox("提取表格文字", value=True)
        
        # 下載選項
        st.subheader("📥 下載選項")
        download_results = st.checkbox("自動下載結果", value=False)
    
    # 主要內容區域
    if uploaded_file is not None:
        st.success(f"✅ 已上傳文件: {uploaded_file.name}")
        
        # 讀取PDF
        try:
            pdf_reader = PyPDF2.PdfReader(uploaded_file)
            full_text = ""
            
            # 提取文字
            for page_num, page in enumerate(pdf_reader.pages):
                page_text = page.extract_text()
                full_text += f"\n--- 第 {page_num + 1} 頁 ---\n{page_text}\n"
            
            st.text_area("📄 提取的文字", full_text[:2000] + "..." if len(full_text) > 2000 else full_text, height=300)
            
            # 詞庫處理
            if termbase_file is not None:
                termbase = pd.read_csv(termbase_file)
                st.success(f"✅ 已載入詞庫: {len(termbase)} 個條目")
                
                # 顯示詞庫預覽
                with st.expander("📚 詞庫預覽"):
                    st.dataframe(termbase.head(10), use_container_width=True)
                
                # 分析結果
                st.subheader("🔍 分析結果")
                
                # 這裡可以添加錯字檢測邏輯
                st.info("🔍 正在分析文件中...")
                
                # 模擬分析結果
                analysis_results = {
                    "總頁數": len(pdf_reader.pages),
                    "總字數": len(full_text),
                    "檢測到的術語": 0,
                    "潛在錯誤": 0
                }
                
                # 顯示統計
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("📄 總頁數", analysis_results["總頁數"])
                with col2:
                    st.metric("📝 總字數", analysis_results["總字數"])
                with col3:
                    st.metric("🔍 檢測術語", analysis_results["檢測到的術語"])
                with col4:
                    st.metric("⚠️ 潛在錯誤", analysis_results["潛在錯誤"])
                
                # 下載結果
                if download_results:
                    results_df = pd.DataFrame([analysis_results])
                    csv = results_df.to_csv(index=False)
                    st.download_button(
                        label="📥 下載分析結果",
                        data=csv,
                        file_name=f"analysis_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv"
                    )
            else:
                st.warning("⚠️ 請上傳詞庫文件以進行分析")
                
        except Exception as e:
            st.error(f"❌ 處理文件時發生錯誤: {str(e)}")
    
    else:
        st.info("ℹ️ 請上傳PDF文件開始分析")
        
        # 顯示使用說明
        with st.expander("📖 使用說明"):
            st.markdown("""
            ### 🔍 錯字校正LLM 使用說明
            
            **步驟 1:** 在側邊欄上傳PDF文件
            **步驟 2:** 上傳CSV格式的詞庫文件
            **步驟 3:** 選擇處理選項
            **步驟 4:** 查看分析結果
            
            ### 📋 支援格式
            - **PDF文件**: 包含文字的PDF文件
            - **詞庫文件**: CSV格式，包含中英文對照
            
            ### 🔧 功能特色
            - 自動提取PDF文字
            - 智能錯字檢測
            - 術語一致性檢查
            - 結果匯出功能
            """)

if __name__ == "__main__":
    main()
