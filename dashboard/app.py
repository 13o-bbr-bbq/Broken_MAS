"""
Dashboard — エントリポイント

Streamlit のマルチページ機能 (st.navigation / st.Page) でナビゲーションを構成。
将来のページ追加は pages/ にファイルを追加して st.navigation のリストに加えるだけ。
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

pg = st.navigation(
    [
        st.Page("pages/chat.py",              title="Agent Chat",         icon="💬"),
        st.Page("pages/evaluation.py",        title="Evaluation Logs",    icon="📊"),
        st.Page("pages/visualization.py",      title="Visualization",      icon="🕸️"),
        st.Page("pages/threat_modeling.py",    title="Threat Modeling",    icon="🛡️"),
    ]
)
pg.run()
