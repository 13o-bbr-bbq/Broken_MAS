"""
Evaluation Logs ページ

Langfuse に格納済みの評価スコアと会話ログを表示する。
- 時系列スコアチャート（評価観点ごとの推移）
- 会話ログテーブル（各行を展開して入力/出力全文を確認可能）
"""

from __future__ import annotations

import logging
import sys
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# evaluation_client を import パスに追加
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from evaluation_client.langfuse_eval_client import LangfuseEvalClient
from translations import get_translations

logger = logging.getLogger(__name__)

T = get_translations(st.session_state.get("lang", "日本語"))

# ---------------------------------------------------------------------------
# ページ設定
# ---------------------------------------------------------------------------

st.title(T["eval_title"])
st.caption(T["eval_caption"])

# ---------------------------------------------------------------------------
# サイドバー — フィルタ設定
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header(T["eval_header_filter"])

    today = datetime.now(timezone.utc).date()
    from_date = st.date_input(T["eval_label_start_date"], value=today - timedelta(days=1))
    to_date = st.date_input(T["eval_label_end_date"], value=today)

    limit = st.slider(T["eval_label_limit"], min_value=100, max_value=2000, value=500, step=100)

    host = st.text_input(
        T["eval_label_langfuse_host"],
        value=os.environ.get("LANGFUSE_HOST", "https://us.cloud.langfuse.com"),
        help=T["eval_help_langfuse_host"],
    )

    refresh = st.button(T["eval_btn_refresh"], type="primary", width="stretch")

# ---------------------------------------------------------------------------
# データ取得（キャッシュ付き）
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner="Langfuse からデータを取得中...")
def load_data(
    from_iso: str,
    to_iso: str,
    limit: int,
    host: str,
) -> pd.DataFrame:
    """評価 DataFrame を取得してキャッシュする。

    引数をすべてシリアライズ可能な型にしてキャッシュキーとして使う。
    """
    client = LangfuseEvalClient(host=host)
    from_dt = datetime.fromisoformat(from_iso)
    to_dt = datetime.fromisoformat(to_iso)
    return client.get_evaluation_dataframe(from_dt=from_dt, to_dt=to_dt, limit=limit)


# セッションステートで最後に取得したデータを保持
if "df" not in st.session_state:
    st.session_state.df = None

if refresh or st.session_state.df is None:
    try:
        from_iso = datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc).isoformat()
        to_iso = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, tzinfo=timezone.utc).isoformat()
        st.session_state.df = load_data(from_iso, to_iso, limit, host)
    except EnvironmentError as e:
        logger.error("環境変数エラー: %s", e, exc_info=True)
        st.error(T["eval_error_env"].format(e=e))
        st.info(T["eval_info_env_hint"])
        st.stop()
    except RuntimeError as e:
        logger.error("データ取得エラー: %s", e, exc_info=True)
        st.error(T["eval_error_fetch"].format(e=e))
        st.stop()

df: pd.DataFrame = st.session_state.df

if df.empty:
    st.warning(T["eval_warning_no_data"])
    st.stop()

# ---------------------------------------------------------------------------
# スコア種別フィルタ（データ取得後に選択肢を確定）
# ---------------------------------------------------------------------------

all_score_names = sorted(df["score_name"].unique().tolist())

with st.sidebar:
    st.divider()
    selected_scores = st.multiselect(
        T["eval_label_filter"],
        options=all_score_names,
        default=all_score_names,
        help=T["eval_help_filter"],
    )

if selected_scores:
    df_filtered = df[df["score_name"].isin(selected_scores)].copy()
else:
    df_filtered = df.copy()

# ---------------------------------------------------------------------------
# サマリー指標
# ---------------------------------------------------------------------------

col1, col2, col3 = st.columns(3)
col1.metric(T["eval_metric_total_scores"], len(df_filtered))
col2.metric(T["eval_metric_total_traces"], df_filtered["trace_id"].nunique())
col3.metric(T["eval_metric_criteria"], df_filtered["score_name"].nunique())

st.divider()

# ---------------------------------------------------------------------------
# 時系列スコアチャート
# ---------------------------------------------------------------------------

st.subheader(T["eval_subheader_timeseries"])

numeric_df = df_filtered[df_filtered["data_type"] == "NUMERIC"].copy()

if numeric_df.empty:
    st.info(T["eval_info_no_numeric"])
else:
    fig = go.Figure()

    for score_name in numeric_df["score_name"].unique():
        sub = numeric_df[numeric_df["score_name"] == score_name].sort_values("timestamp")
        fig.add_trace(
            go.Scatter(
                x=sub["timestamp"],
                y=sub["score_value"],
                mode="lines+markers",
                name=score_name,
                customdata=sub[["trace_id", "input_preview"]].values,
                hovertemplate=(
                    "<b>%{fullData.name}</b><br>"
                    "時刻: %{x}<br>"
                    "スコア: %{y:.3f}<br>"
                    "Trace ID: %{customdata[0]}<br>"
                    "入力: %{customdata[1]}<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        xaxis_title="時刻",
        yaxis_title="スコア",
        legend_title="評価観点",
        hovermode="closest",
        height=400,
        margin=dict(l=0, r=0, t=30, b=0),
    )
    st.plotly_chart(fig, width="stretch")

st.divider()

# ---------------------------------------------------------------------------
# 会話ログテーブル + 展開ビュー
# ---------------------------------------------------------------------------

st.subheader(T["eval_subheader_logs"])

# trace 単位でまとめる（同一 trace に複数スコアがある場合はまとめて表示）
trace_groups = df_filtered.groupby("trace_id", sort=False)

# タイムスタンプ降順で trace_id を並べる
trace_order = (
    df_filtered[["trace_id", "timestamp"]]
    .drop_duplicates("trace_id")
    .sort_values("timestamp", ascending=False)
    ["trace_id"]
    .tolist()
)

for trace_id in trace_order:
    group = trace_groups.get_group(trace_id)
    first = group.iloc[0]

    ts = first["timestamp"]
    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S UTC") if pd.notna(ts) else "—"

    # スコアサマリ文字列を構築
    score_parts = []
    for _, row in group.iterrows():
        if row["data_type"] == "NUMERIC" and row["score_value"] is not None:
            score_parts.append(f"{row['score_name']}: **{row['score_value']:.3f}**")
        elif row["string_value"]:
            score_parts.append(f"{row['score_name']}: **{row['string_value']}**")
        else:
            score_parts.append(f"{row['score_name']}: —")

    score_summary = " / ".join(score_parts)

    label = f"{ts_str}  |  {score_summary}  |  {first['input_preview']}"

    with st.expander(label):
        # スコア詳細テーブル
        score_rows = []
        for _, row in group.iterrows():
            score_rows.append(
                {
                    T["eval_col_criteria"]: row["score_name"],
                    T["eval_col_score"]: row["score_value"] if row["score_value"] is not None else row["string_value"],
                    T["eval_col_type"]: row["data_type"],
                    T["eval_col_comment"]: row["comment"],
                }
            )
        st.table(pd.DataFrame(score_rows))

        # 会話内容
        col_in, col_out = st.columns(2)
        with col_in:
            st.markdown(T["eval_label_input"])
            st.text_area(
                label="input_detail",
                value=first["full_input"],
                height=300,
                key=f"input_{trace_id}",
                disabled=True,
                label_visibility="collapsed",
            )
        with col_out:
            st.markdown(T["eval_label_output"])
            st.text_area(
                label="output_detail",
                value=first["full_output"],
                height=300,
                key=f"output_{trace_id}",
                disabled=True,
                label_visibility="collapsed",
            )

        st.caption(f"Trace ID: `{trace_id}`")
