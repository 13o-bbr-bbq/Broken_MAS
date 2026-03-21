"""
Visualization ページ

Langfuse に蓄積された OTEL トレース（動的）または
AgentCore から取得したトポロジー（静的）を
インタラクティブグラフで表示する。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import streamlit as st
import streamlit.components.v1 as components

# visualization/ モジュールと evaluation_client/ を import パスに追加
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, _REPO_ROOT)

logger = logging.getLogger(__name__)

from visualization.visualize_traces import (
    export_system_schema,
    fetch_observations_for_traces,
    build_graph,
)
from dashboard.topology_utils import (
    build_graph_from_schema,
    merge_agentcore_and_langfuse_schema,
    render_graph_to_html,
)


# ---------------------------------------------------------------------------
# ページ設定
# ---------------------------------------------------------------------------

st.title("🕸️ MAS Topology Visualization")
st.caption("Langfuse（動的）/ AgentCore（静的）/ Hybrid（統合）のトポロジーを可視化します。")


# ---------------------------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------------------------

def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return list(value.values())
    return [value]


# ---------------------------------------------------------------------------
# Langfuse クライアントとトレース取得（ページ固有の薄いラッパー）
# ---------------------------------------------------------------------------

def _make_langfuse_client(host: str):
    from langfuse import Langfuse

    pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sec = os.environ.get("LANGFUSE_SECRET_KEY")
    if not pub:
        raise EnvironmentError("環境変数 LANGFUSE_PUBLIC_KEY が設定されていません。")
    if not sec:
        raise EnvironmentError("環境変数 LANGFUSE_SECRET_KEY が設定されていません。")
    return Langfuse(public_key=pub, secret_key=sec, host=host)


def _fetch_traces(lf, limit: int, from_timestamp=None) -> list:
    """visualize_traces.py の fetch_traces と同等のロジック（args 依存なし）。"""
    kwargs: dict = {}
    if from_timestamp is not None:
        kwargs["from_timestamp"] = from_timestamp

    traces = []
    page = 1
    per_page = min(limit, 100)

    while len(traces) < limit:
        response = lf.api.trace.list(page=page, limit=per_page, **kwargs)
        batch = list(response.data)
        if not batch:
            break
        traces.extend(batch)
        if page >= response.meta.total_pages:
            break
        page += 1

    return traces[:limit]


@st.cache_data(ttl=300, show_spinner="Langfuse からトレースを取得してグラフを生成中...")
def _generate_langfuse_topology(
    limit: int,
    hours: int,
    no_physics: bool,
    host: str,
) -> dict:
    lf = _make_langfuse_client(host)

    from_ts = datetime.now(timezone.utc) - timedelta(hours=hours) if hours > 0 else None
    traces = _fetch_traces(lf, limit, from_ts)
    if not traces:
        return {
            "source": "langfuse",
            "html_content": "",
            "node_count": 0,
            "edge_count": 0,
            "summary_label": "取得トレース数",
            "summary_value": 0,
            "trace_count": 0,
            "trace_summaries": [],
            "schema_json": None,
        }

    obs_by_trace = fetch_observations_for_traces(lf, traces, verbose=False)
    G = build_graph(obs_by_trace, verbose=False)
    html_content = render_graph_to_html(
        G,
        no_physics=no_physics,
        heading_trace_count=len(traces),
        heading_override="MAS Topology (Langfuse Dynamic)",
    )

    schema_json: dict | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, dir="/tmp") as tmp:
            schema_path = tmp.name
        export_system_schema(G, obs_by_trace, schema_path)
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_json = json.load(f)
        os.unlink(schema_path)
        logger.info("export_system_schema 成功: nodes=%d", G.number_of_nodes())
    except Exception as e:
        logger.error("export_system_schema 失敗: %s", e, exc_info=True)
        schema_json = None

    trace_summaries = [
        {
            "timestamp": t.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC") if t.timestamp else "—",
            "trace_id": t.id,
            "name": t.name or "—",
        }
        for t in traces
    ]

    return {
        "source": "langfuse",
        "html_content": html_content,
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "summary_label": "取得トレース数",
        "summary_value": len(traces),
        "trace_count": len(traces),
        "trace_summaries": trace_summaries,
        "schema_json": schema_json,
    }


@st.cache_data(ttl=120, show_spinner="AgentCore スキーマからグラフを生成中...")
def _generate_agentcore_topology(
    schema_json_text: str,
    no_physics: bool,
) -> dict:
    schema = json.loads(schema_json_text)
    G = build_graph_from_schema(schema)
    html_content = render_graph_to_html(
        G,
        no_physics=no_physics,
        heading_trace_count=max(1, G.number_of_nodes()),
        heading_override="MAS Topology (AgentCore Static)",
    )

    comps = schema.get("components") or {}
    runtime_count = (
        len(_as_list(comps.get("orchestrators")))
        + len(_as_list(comps.get("a2a_agents")))
        + len(_as_list(comps.get("mcp_servers")))
    )

    return {
        "source": "agentcore",
        "html_content": html_content,
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "summary_label": "Runtime 数 (静的)",
        "summary_value": runtime_count,
        "trace_count": 0,
        "trace_summaries": [],
        "schema_json": schema,
    }


@st.cache_data(ttl=180, show_spinner="AgentCore + Langfuse の統合トポロジーを生成中...")
def _generate_hybrid_topology(
    agentcore_schema_json_text: str,
    limit: int,
    hours: int,
    no_physics: bool,
    host: str,
) -> dict:
    agentcore_schema = json.loads(agentcore_schema_json_text)
    lf_result = _generate_langfuse_topology(
        limit=limit,
        hours=hours,
        no_physics=no_physics,
        host=host,
    )
    merged_schema = merge_agentcore_and_langfuse_schema(
        agentcore_schema=agentcore_schema,
        langfuse_schema=lf_result.get("schema_json"),
        trace_count=int(lf_result.get("summary_value") or 0),
    )

    G = build_graph_from_schema(merged_schema)
    html_content = render_graph_to_html(
        G,
        no_physics=no_physics,
        heading_trace_count=max(1, G.number_of_nodes()),
        heading_override="MAS Topology (Hybrid: AgentCore + Langfuse)",
    )

    comps = merged_schema.get("components") or {}
    runtime_count = (
        len(_as_list(comps.get("orchestrators")))
        + len(_as_list(comps.get("a2a_agents")))
        + len(_as_list(comps.get("mcp_servers")))
    )
    trace_count = int(lf_result.get("summary_value") or 0)

    return {
        "source": "hybrid",
        "html_content": html_content,
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "summary_label": "Runtime / Trace (統合)",
        "summary_value": f"{runtime_count} / {trace_count}",
        "trace_count": trace_count,
        "trace_summaries": lf_result.get("trace_summaries", []),
        "schema_json": merged_schema,
    }


# ---------------------------------------------------------------------------
# サイドバー — 設定
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("表示設定")

    data_source = st.radio(
        "トポロジーソース",
        options=["Langfuse (動的)", "AgentCore (静的)", "Hybrid (統合)"],
        index=0,
        help="Hybrid は AgentCore 構成をベースに Langfuse 実行実績を統合します。",
    )

    no_physics = st.checkbox(
        "物理シミュレーションを無効化",
        value=False,
        help="ON にすると静的レイアウトになり、大規模グラフで動作が安定します。",
    )

    limit = 100
    hours = 24
    host = os.environ.get("LANGFUSE_HOST", "https://us.cloud.langfuse.com")

    if data_source in ("Langfuse (動的)", "Hybrid (統合)"):
        st.divider()
        st.subheader("Langfuse 取得設定")

        limit = st.slider("取得トレース上限 (件)", min_value=10, max_value=500, value=100, step=10)
        hours = st.number_input(
            "過去 N 時間分を取得",
            min_value=0,
            max_value=720,
            value=24,
            step=1,
            help="0 にすると時間フィルタなし（最新 N 件のみ）",
        )
        host = st.text_input(
            "Langfuse ホスト",
            value=os.environ.get("LANGFUSE_HOST", "https://us.cloud.langfuse.com"),
        )
    elif data_source == "AgentCore (静的)":
        st.caption(
            "AgentCore Topology ページで取得済みの静的スキーマを表示します。"
        )
    else:
        st.caption(
            "AgentCore Topology の静的スキーマと Langfuse の動的フローを統合表示します。"
        )

    generate = st.button("表示を更新", type="primary", use_container_width=True)


# ---------------------------------------------------------------------------
# セッションステート管理
# ---------------------------------------------------------------------------

if "viz_result" not in st.session_state:
    st.session_state.viz_result = None
if "viz_result_source" not in st.session_state:
    st.session_state.viz_result_source = None

source_map = {
    "Langfuse (動的)": "langfuse",
    "AgentCore (静的)": "agentcore",
    "Hybrid (統合)": "hybrid",
}
source_key = source_map[data_source]
source_changed = st.session_state.viz_result_source != source_key

if generate or st.session_state.viz_result is None or source_changed:
    if source_key == "langfuse":
        try:
            st.session_state.viz_result = _generate_langfuse_topology(
                limit=limit,
                hours=hours,
                no_physics=no_physics,
                host=host,
            )
            st.session_state.viz_result_source = source_key
        except EnvironmentError as e:
            st.error(f"環境変数エラー: {e}")
            st.info("LANGFUSE_PUBLIC_KEY と LANGFUSE_SECRET_KEY を設定してから再試行してください。")
            st.stop()
        except RuntimeError as e:
            st.error(f"グラフ生成エラー: {e}")
            st.stop()
    elif source_key == "agentcore":
        agentcore_schema = st.session_state.get("agentcore_schema")
        if not agentcore_schema:
            st.warning("AgentCore Topology ページで先に構成情報を取得してください。")
            st.stop()

        st.session_state.viz_result = _generate_agentcore_topology(
            schema_json_text=json.dumps(agentcore_schema, ensure_ascii=False, sort_keys=True),
            no_physics=no_physics,
        )
        st.session_state.viz_result_source = source_key
    else:
        agentcore_schema = st.session_state.get("agentcore_schema")
        if not agentcore_schema:
            st.warning("Hybrid 表示には AgentCore Topology ページの構成情報が必要です。")
            st.stop()
        try:
            st.session_state.viz_result = _generate_hybrid_topology(
                agentcore_schema_json_text=json.dumps(agentcore_schema, ensure_ascii=False, sort_keys=True),
                limit=limit,
                hours=hours,
                no_physics=no_physics,
                host=host,
            )
            st.session_state.viz_result_source = source_key
        except EnvironmentError as e:
            st.error(f"環境変数エラー: {e}")
            st.info("LANGFUSE_PUBLIC_KEY と LANGFUSE_SECRET_KEY を設定してから再試行してください。")
            st.stop()
        except RuntimeError as e:
            st.error(f"統合グラフ生成エラー: {e}")
            st.stop()

result = st.session_state.viz_result
html_content = result.get("html_content", "")
node_count = result.get("node_count", 0)
edge_count = result.get("edge_count", 0)
summary_label = result.get("summary_label", "件数")
summary_value = result.get("summary_value", 0)
trace_summaries = result.get("trace_summaries", [])
trace_count = int(result.get("trace_count", 0))
schema_json = result.get("schema_json")

if result.get("source") == "langfuse" and schema_json:
    st.session_state.viz_schema = schema_json
if result.get("source") == "hybrid" and schema_json:
    st.session_state.hybrid_schema = schema_json


# ---------------------------------------------------------------------------
# データなし
# ---------------------------------------------------------------------------

if not html_content:
    if source_key == "langfuse":
        st.warning(
            "指定した条件でトレースが見つかりませんでした。\n"
            "取得件数・時間範囲・Langfuse ホストを確認してください。"
        )
    elif source_key == "agentcore":
        st.warning("AgentCore スキーマから描画できるコンポーネントが見つかりませんでした。")
    else:
        st.warning("Hybrid 統合結果から描画できるコンポーネントが見つかりませんでした。")
    st.stop()


# ---------------------------------------------------------------------------
# サマリー指標
# ---------------------------------------------------------------------------

col1, col2, col3, col4 = st.columns(4)
col1.metric(summary_label, summary_value)
col2.metric("コンポーネント数 (ノード)", node_count)
col3.metric("通信経路数 (エッジ)", edge_count)
with col4:
    if schema_json:
        schema_file = {
            "langfuse": "system_schema.json",
            "agentcore": "agentcore_schema.json",
            "hybrid": "hybrid_schema.json",
        }[source_key]
        st.download_button(
            label="スキーマ JSON をダウンロード",
            data=json.dumps(schema_json, ensure_ascii=False, indent=2),
            file_name=schema_file,
            mime="application/json",
            help="Threat Modeling ページでも利用できます。",
        )

if source_key == "langfuse":
    st.caption("現在表示中: Langfuse の動的トポロジー")
elif source_key == "agentcore":
    st.caption("現在表示中: AgentCore の静的トポロジー")
else:
    st.caption("現在表示中: Hybrid（AgentCore + Langfuse）統合トポロジー")

st.divider()


# ---------------------------------------------------------------------------
# トポロジーグラフ（pyvis HTML）
# ---------------------------------------------------------------------------

st.subheader("コンポーネントトポロジー")
st.caption("ノードをドラッグして移動、スクロールでズーム、ホバーで詳細を確認できます。")
components.html(html_content, height=820, scrolling=False)


# ---------------------------------------------------------------------------
# トレース一覧（Langfuse 時のみ）
# ---------------------------------------------------------------------------

if source_key in ("langfuse", "hybrid"):
    st.divider()
    with st.expander(f"取得トレース一覧 ({trace_count} 件)", expanded=False):
        if trace_summaries:
            import pandas as pd

            df_traces = pd.DataFrame(trace_summaries)
            df_traces.columns = ["タイムスタンプ", "Trace ID", "トレース名"]
            st.dataframe(df_traces, use_container_width=True, hide_index=True)
        else:
            st.info("トレース情報がありません。")
