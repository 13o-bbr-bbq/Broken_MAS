"""
Dashboard — エントリポイント

Streamlit のマルチページ機能 (st.navigation / st.Page) でナビゲーションを構成。
将来のページ追加は _pages/ にファイルを追加して st.navigation のリストに加えるだけ。
"""

import sys
import os

# Streamlit がスクリプトディレクトリ(dashboard/)を sys.path に追加するが
# 名前空間パッケージとして "dashboard.log_config" を解決できないケースがあるため、
# ファイルの絶対パスを基点にしてモジュールを直接ロードする。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
from log_config import setup_logging

st.set_page_config(
    page_title="MAS AgentCore Dashboard",
    page_icon="🤖",
    layout="wide",
)
setup_logging()

with st.sidebar:
    st.selectbox("🌐 Language / 言語", ["日本語", "English"], key="lang")

pg = st.navigation(
    [
        st.Page("_pages/chat.py",              title="Agent Chat",         icon="💬"),
        st.Page("_pages/evaluation.py",        title="Evaluation Logs",    icon="📊"),
        st.Page("_pages/visualization.py",      title="Visualization",      icon="🕸️"),
        st.Page("_pages/threat_modeling.py",    title="Threat Modeling",    icon="🛡️"),
    ]
)
pg.run()
