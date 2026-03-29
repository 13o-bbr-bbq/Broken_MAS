"""
Visualization ページ

docker-compose.yml（ローカル静的）または Langfuse OTEL トレース（動的）から
MAS トポロジーをインタラクティブグラフで表示する。
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
    build_graph_from_compose,
    render_graph_to_html,
)
from translations import get_translations

T = get_translations(st.session_state.get("lang", "日本語"))


# ---------------------------------------------------------------------------
# ページ設定
# ---------------------------------------------------------------------------

st.title(T["viz_title"])
st.caption(T["viz_caption"])


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


@st.cache_data(ttl=3600, show_spinner="docker-compose.yml からトポロジーを生成中...")
def _generate_compose_topology(
    compose_path: str,
    repo_root: str,
    no_physics: bool,
) -> dict:
    """docker-compose.yml とソースコードから静的トポロジーを生成する。"""
    G, schema = build_graph_from_compose(compose_path, repo_root)

    if G.number_of_nodes() == 0:
        return {
            "source": "compose",
            "html_content": "",
            "node_count": 0,
            "edge_count": 0,
            "summary_value": 0,
            "trace_count": 0,
            "trace_summaries": [],
            "schema_json": None,
        }

    html_content = render_graph_to_html(
        G,
        no_physics=no_physics,
        heading_trace_count=G.number_of_nodes(),
        heading_override="MAS Topology (Docker Compose Local)",
    )

    comps = schema.get("components") or {}
    service_count = (
        len(_as_list(comps.get("orchestrators")))
        + len(_as_list(comps.get("a2a_agents")))
        + len(_as_list(comps.get("mcp_servers")))
    )

    return {
        "source": "compose",
        "html_content": html_content,
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "summary_value": service_count,
        "trace_count": 0,
        "trace_summaries": [],
        "schema_json": schema,
    }


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
        "summary_value": len(traces),
        "trace_count": len(traces),
        "trace_summaries": trace_summaries,
        "schema_json": schema_json,
    }


# ---------------------------------------------------------------------------
# サイドバー — 設定
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header(T["viz_header_settings"])

    data_source = st.radio(
        T["viz_label_source"],
        options=[T["viz_option_compose"], T["viz_option_langfuse"]],
        index=0,
        help=T["viz_help_source"],
    )

    no_physics = st.checkbox(
        T["viz_label_no_physics"],
        value=False,
        help=T["viz_help_no_physics"],
    )

    limit = 100
    hours = 24
    host = os.environ.get("LANGFUSE_HOST", "https://us.cloud.langfuse.com")

    if data_source == T["viz_option_compose"]:
        st.divider()
        compose_file = os.path.join(_REPO_ROOT, "docker-compose.yml")
        st.caption(T["viz_caption_compose_file"])
        st.code(compose_file, language=None)
        st.caption(T["viz_caption_compose_desc"])
    else:  # Langfuse
        st.divider()
        st.subheader(T["viz_subheader_langfuse"])

        limit = st.slider(T["viz_label_trace_limit"], min_value=10, max_value=500, value=100, step=10)
        hours = st.number_input(
            T["viz_label_hours"],
            min_value=0,
            max_value=720,
            value=24,
            step=1,
            help=T["viz_help_hours"],
        )
        host = st.text_input(
            T["viz_label_langfuse_host"],
            value=os.environ.get("LANGFUSE_HOST", "https://us.cloud.langfuse.com"),
        )

    generate = st.button(T["viz_btn_refresh"], type="primary", use_container_width=True)


# ---------------------------------------------------------------------------
# セッションステート管理
# ---------------------------------------------------------------------------

if "viz_result" not in st.session_state:
    st.session_state.viz_result = None
if "viz_result_source" not in st.session_state:
    st.session_state.viz_result_source = None

source_map = {
    T["viz_option_compose"]: "compose",
    T["viz_option_langfuse"]: "langfuse",
}
source_key = source_map[data_source]
source_changed = st.session_state.viz_result_source != source_key

if generate or st.session_state.viz_result is None or source_changed:
    if source_key == "compose":
        compose_path = os.path.join(_REPO_ROOT, "docker-compose.yml")
        if not os.path.exists(compose_path):
            st.error(T["viz_error_no_compose"].format(path=compose_path))
            st.stop()
        try:
            st.session_state.viz_result = _generate_compose_topology(
                compose_path=compose_path,
                repo_root=_REPO_ROOT,
                no_physics=no_physics,
            )
            st.session_state.viz_result_source = source_key
        except Exception as e:
            st.error(T["viz_error_compose"].format(e=e))
            st.stop()
    else:  # langfuse
        try:
            st.session_state.viz_result = _generate_langfuse_topology(
                limit=limit,
                hours=hours,
                no_physics=no_physics,
                host=host,
            )
            st.session_state.viz_result_source = source_key
        except EnvironmentError as e:
            st.error(T["viz_error_env"].format(e=e))
            st.info(T["viz_info_env_hint"])
            st.stop()
        except RuntimeError as e:
            st.error(T["viz_error_graph"].format(e=e))
            st.stop()

result = st.session_state.viz_result
html_content = result.get("html_content", "")
node_count = result.get("node_count", 0)
edge_count = result.get("edge_count", 0)
summary_label = T["viz_summary_label_compose"] if source_key == "compose" else T["viz_summary_label_langfuse"]
summary_value = result.get("summary_value", 0)
trace_summaries = result.get("trace_summaries", [])
trace_count = int(result.get("trace_count", 0))
schema_json = result.get("schema_json")

if schema_json:
    st.session_state.viz_schema = schema_json


# ---------------------------------------------------------------------------
# データなし
# ---------------------------------------------------------------------------

if not html_content:
    if source_key == "compose":
        st.warning(T["viz_warning_no_compose"])
    else:
        st.warning(T["viz_warning_no_langfuse"])
    st.stop()


# ---------------------------------------------------------------------------
# サマリー指標
# ---------------------------------------------------------------------------

col1, col2, col3, col4 = st.columns(4)
col1.metric(summary_label, summary_value)
col2.metric(T["viz_metric_components"], node_count)
col3.metric(T["viz_metric_edges"], edge_count)
with col4:
    if schema_json:
        schema_file = "compose_schema.json" if source_key == "compose" else "system_schema.json"
        st.download_button(
            label=T["viz_btn_download_schema"],
            data=json.dumps(schema_json, ensure_ascii=False, indent=2),
            file_name=schema_file,
            mime="application/json",
            help=T["viz_help_download_schema"],
        )

if source_key == "compose":
    st.caption(T["viz_caption_current_compose"])
else:
    st.caption(T["viz_caption_current_langfuse"])

st.divider()


# ---------------------------------------------------------------------------
# トポロジーグラフ（pyvis HTML）
# ---------------------------------------------------------------------------

st.subheader(T["viz_subheader_topology"])
st.caption(T["viz_caption_topology"])
components.html(html_content, height=820, scrolling=False)


# ---------------------------------------------------------------------------
# トレース一覧（Langfuse 時のみ）
# ---------------------------------------------------------------------------

if source_key == "langfuse":
    st.divider()
    with st.expander(T["viz_expander_traces"].format(count=trace_count), expanded=False):
        if trace_summaries:
            import pandas as pd

            df_traces = pd.DataFrame(trace_summaries)
            df_traces.columns = [T["viz_col_timestamp"], T["viz_col_trace_id"], T["viz_col_trace_name"]]
            st.dataframe(df_traces, use_container_width=True, hide_index=True)
        else:
            st.info(T["viz_info_no_traces"])
