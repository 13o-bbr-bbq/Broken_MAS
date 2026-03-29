#!/usr/bin/env python3
"""
MAS Topology Visualizer

Langfuse に蓄積された OTEL トレースを取得し、マルチエージェントシステムの
コンポーネントトポロジーをインタラクティブ HTML グラフとして可視化する。

スパン名パターン（Strands Agents tracer.py より）:
  invoke_agent {name}   : エージェント起動境界
  invoke_{name}         : A2A アウトバウンド呼び出し（SpanKind.CLIENT）
  execute_tool {tool}   : ツール呼び出し（MCP または A2A ツール）
  chat                  : LLM 呼び出し
  execute_event_loop_cycle : 内部処理（スキップ）

使用方法:
  export LANGFUSE_PUBLIC_KEY="pk-lf-..."
  export LANGFUSE_SECRET_KEY="sk-lf-..."
  python visualization/visualize_traces.py [options]

オプション:
  --limit N      最新 N 件のトレースを取得（デフォルト: 100）
  --hours N      過去 N 時間分のトレースを取得（--limit と併用可能）
  --output PATH  出力 HTML ファイルパス
  --host URL     Langfuse ホスト URL
  --no-physics   vis.js の物理シミュレーションを無効化
  --verbose      各 observation のデバッグ出力
"""

import argparse
import base64
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# python-dotenv は任意
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from langfuse import Langfuse
except ImportError:
    sys.exit("ERROR: langfuse がインストールされていません。\n  pip install langfuse")

try:
    import networkx as nx
except ImportError:
    sys.exit("ERROR: networkx がインストールされていません。\n  pip install networkx")

try:
    from pyvis.network import Network
except ImportError:
    sys.exit("ERROR: pyvis がインストールされていません。\n  pip install pyvis")


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _label_to_display_name(label: str) -> str:
    """ノードラベルから表示名を抽出する。

    ラベル形式 "Short Label\\n(Full Name)" の場合は括弧内の Full Name を返す。
    括弧がない場合は改行前の先頭部分を返す。

    例:
        "A2A Agent 1\\n(Finding Restaurants Agent)" → "Finding Restaurants Agent"
        "MCP Server 1\\n(Restaurant Search)"        → "Restaurant Search"
        "Orchestrator Agent"                        → "Orchestrator Agent"
    """
    if "\n(" in label and label.endswith(")"):
        return label.split("\n(", 1)[1].rstrip(")")
    return label.split("\n")[0]


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# MCP サーバー名（スパン名の {server}___{tool} 形式の server 部分） → (ノード ID, ノードラベル)
# Strands Agents は type=TOOL のスパンを "{server_name}___{tool_name}" という名前で記録する
SERVER_NAME_TO_MCP_NODE: dict[str, tuple[str, str]] = {
    "broken-mcp-server-1": ("mcp_server_1", "MCP Server 1\n(Restaurant Search)"),
    "broken-mcp-server-2": ("mcp_server_2", "MCP Server 2\n(Restaurant Details)"),
    "broken-mcp-server-3": ("mcp_server_3", "MCP Server 3\n(Pizza Menu)"),
    "broken-mcp-server-4": ("mcp_server_4", "MCP Server 4\n(Pizza Orders)"),
}

# ツール名 → (MCP ノード ID, MCP ノードラベル)（execute_tool 形式のスパン用フォールバック）
TOOL_TO_MCP_NODE: dict[str, tuple[str, str]] = {
    "finding_restaurants":          ("mcp_server_1", "MCP Server 1\n(Restaurant Search)"),
    "finding_michelin_restaurants": ("mcp_server_1", "MCP Server 1\n(Restaurant Search)"),
    "retrieve_restaurant_details":  ("mcp_server_2", "MCP Server 2\n(Restaurant Details)"),
    "retrieve_restaurant_menu":     ("mcp_server_2", "MCP Server 2\n(Restaurant Details)"),
    "getting_pizza_menu":           ("mcp_server_3", "MCP Server 3\n(Pizza Menu)"),
    "ordering_pizza":               ("mcp_server_4", "MCP Server 4\n(Pizza Orders)"),
}

# ---------------------------------------------------------------------------
# SVG アイコン
# ---------------------------------------------------------------------------

def _svg_to_data_url(svg: str) -> str:
    """SVG 文字列を base64 データ URL に変換する。"""
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def _load_asset_svg(filename: str) -> str:
    """assets/ ディレクトリから SVG ファイルを読み込む。"""
    assets_dir = Path(__file__).parent / "assets"
    return (assets_dir / filename).read_text(encoding="utf-8")


def _wrap_in_circle(
    inner_svg: str,
    canvas: int,
    bg_color: str,
    border_color: str,
    border_width: int,
) -> str:
    """アセット SVG を円形背景 + ボーダーで包んだ合成 SVG を返す。

    Args:
        inner_svg: 埋め込むアセット SVG の文字列。
        canvas: 出力 SVG の幅・高さ（px）。
        bg_color: 円の背景色。
        border_color: 円の縁色。
        border_width: 縁の太さ（px）。
    """
    r = canvas / 2
    pad = canvas * 0.15
    icon_size = canvas - 2 * pad
    inner_b64 = base64.b64encode(inner_svg.encode("utf-8")).decode()
    return (
        f'<svg width="{canvas}" height="{canvas}" viewBox="0 0 {canvas} {canvas}" '
        f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        f'<circle cx="{r}" cy="{r}" r="{r}" fill="{bg_color}"/>'
        f'<circle cx="{r}" cy="{r}" r="{r - border_width / 2}" fill="none" '
        f'stroke="{border_color}" stroke-width="{border_width}"/>'
        f'<image href="data:image/svg+xml;base64,{inner_b64}" '
        f'x="{pad}" y="{pad}" width="{icon_size}" height="{icon_size}"/>'
        f'</svg>'
    )


# オーケストレーターアイコン：濃紺の円 + 赤ボーダー + ロボット（orchestrator.svg）
_ORCHESTRATOR_SVG = _wrap_in_circle(
    _load_asset_svg("orchestrator.svg"),
    canvas=80,
    bg_color="#E8EAF6",
    border_color="#FF6B6B",
    border_width=6,
)

# A2A エージェントアイコン：緑の円 + ティールボーダー + ロボット（agent.svg）
_AGENT_SVG = _wrap_in_circle(
    _load_asset_svg("agent.svg"),
    canvas=80,
    bg_color="#E8F5E9",
    border_color="#4ECDC4",
    border_width=4,
)

# MCP サーバーアイコン：オレンジの円 + 黄ボーダー + サーバー（mcp_server.svg）
_MCP_SVG = _wrap_in_circle(
    _load_asset_svg("mcp_server.svg"),
    canvas=80,
    bg_color="#FBE9E7",
    border_color="#FFDD00",
    border_width=4,
)

# LLM / カスタムツールアイコン：紫の破線枠 + レンチ（custom_tool.svg）
_TOOL_SVG = (
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<rect x="0.5" y="0.5" width="23" height="23" rx="3.5" fill="#F5DDFF"/>'
    '<rect x="0.5" y="0.5" width="23" height="23" rx="3.5" stroke="#A738D6" '
    'stroke-dasharray="2 2"/>'
    '<g clip-path="url(#ct)">'
    '<path d="M9.5 10.9999H11V9.49993L9.25 7.74993'
    'C9.80982 7.48256 10.4388 7.39533 11.0502 7.50024'
    'C11.6617 7.60515 12.2255 7.89704 12.6642 8.33571'
    'C13.1029 8.77439 13.3948 9.33828 13.4997 9.94973'
    'C13.6046 10.5612 13.5174 11.1901 13.25 11.7499'
    'L16.25 14.7499C16.4489 14.9488 16.5607 15.2186 16.5607 15.4999'
    'C16.5607 15.7812 16.4489 16.051 16.25 16.2499'
    'C16.0511 16.4488 15.7813 16.5606 15.5 16.5606'
    'C15.2187 16.5606 14.9489 16.4488 14.75 16.2499'
    'L11.75 13.2499C11.1902 13.5173 10.5613 13.6045 9.9498 13.4996'
    'C9.33835 13.3947 8.77447 13.1028 8.33579 12.6641'
    'C7.89711 12.2255 7.60522 11.6616 7.50031 11.0501'
    'C7.39541 10.4387 7.48264 9.80974 7.75 9.24993L9.5 10.9999Z" '
    'stroke="black" stroke-linecap="round" stroke-linejoin="round"/>'
    '</g>'
    '<defs><clipPath id="ct"><rect width="12" height="12" fill="white" '
    'transform="translate(6 6)"/></clipPath></defs>'
    '</svg>'
)

# 不明ノードアイコン：ティール色のひし形 + 歯車（basic.svg）
_BASIC_SVG = (
    '<svg width="29" height="29" viewBox="0 0 29 29" fill="none" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<g clip-path="url(#bs)">'
    '<rect x="14.1421" width="20" height="20" rx="4" '
    'transform="rotate(45 14.1421 0)" fill="#009C92"/>'
    '<g clip-path="url(#bs2)">'
    '<path d="M16.2635 12.0207L17.3241 10.9601M17.1473 14.142L18.6676 14.142'
    'M16.2635 16.2634L17.3241 17.324M14.1421 17.1472V18.6675'
    'M12.0208 16.2634L10.9602 17.324M11.1369 14.142L9.61665 14.142'
    'M12.0208 12.0207L10.9602 10.9601M14.1421 11.1368V9.61655" '
    'stroke="white" stroke-linecap="round" stroke-linejoin="round"/>'
    '</g>'
    '</g>'
    '<defs>'
    '<clipPath id="bs"><rect x="14.1421" width="20" height="20" rx="4" '
    'transform="rotate(45 14.1421 0)" fill="white"/></clipPath>'
    '<clipPath id="bs2"><rect width="12" height="12" fill="white" '
    'transform="translate(14.1421 5.65674) rotate(45)"/></clipPath>'
    '</defs>'
    '</svg>'
)

# ノード種別ごとの pyvis スタイル
# shape="circularImage" でアイコン画像を円形クロップ、
# shape="image" で元のアイコン形状をそのまま表示する
NODE_STYLES: dict[str, dict] = {
    "orchestrator": {
        "shape": "circularImage",
        "image": _svg_to_data_url(_ORCHESTRATOR_SVG),
        "size": 40,
        "border_color": "#FF6B6B",   # 赤いボーダーでオーケストレーターを強調
        "border_width": 4,
    },
    "a2a_agent": {
        "shape": "circularImage",
        "image": _svg_to_data_url(_AGENT_SVG),
        "size": 28,
        "border_color": "#4ECDC4",   # ティール
        "border_width": 2,
    },
    "mcp_server": {
        "shape": "circularImage",
        "image": _svg_to_data_url(_MCP_SVG),
        "size": 24,
        "border_color": "#FFDD00",   # 黄色
        "border_width": 2,
    },
    "llm": {
        "shape": "image",
        "image": _svg_to_data_url(_TOOL_SVG),
        "size": 26,
        "border_color": "#A738D6",   # 紫
        "border_width": 2,
    },
    "unknown": {
        "shape": "image",
        "image": _svg_to_data_url(_BASIC_SVG),
        "size": 16,
        "border_color": "#009C92",   # ティール
        "border_width": 1,
    },
}

# A2A エージェント名 → (ノード ID, ノードラベル)
AGENT_NAME_TO_NODE: dict[str, tuple[str, str]] = {
    "Orchestrator Agent":        ("orchestrator",  "Orchestrator Agent"),
    "Finding Restaurants Agent": ("a2a_agent_1",   "A2A Agent 1\n(Finding Restaurants Agent)"),
    "Ordering Pizza Agent":      ("a2a_agent_2",   "A2A Agent 2\n(Ordering Pizza Agent)"),
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Langfuse OTEL トレースから MAS トポロジーを可視化する"
    )
    parser.add_argument(
        "--limit", type=int, default=100,
        help="取得するトレースの最大件数（デフォルト: 100）"
    )
    parser.add_argument(
        "--hours", type=float, default=None,
        help="過去 N 時間分のトレースを取得（省略時は --limit 件）"
    )
    parser.add_argument(
        "--output",
        default="visualization/output/mas_topology.html",
        help="出力 HTML ファイルパス"
    )
    parser.add_argument(
        "--host",
        default="https://us.cloud.langfuse.com",
        help="Langfuse ホスト URL"
    )
    parser.add_argument(
        "--no-physics", action="store_true",
        help="vis.js の物理シミュレーションを無効化（静的レイアウト）"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="各 observation のデバッグ出力を表示"
    )
    parser.add_argument(
        "--export-schema",
        default=None,
        metavar="PATH",
        help=(
            "システムスキーマ JSON を指定パスに出力する。"
            " threat_modeling_agent の --system-file に直接渡せる形式。"
            " （例: visualization/output/system_schema.json）"
        ),
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Langfuse 接続
# ---------------------------------------------------------------------------

def create_langfuse_client(args: argparse.Namespace) -> "Langfuse":
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sec = os.environ.get("LANGFUSE_SECRET_KEY")
    if not pub:
        sys.exit("ERROR: 環境変数 LANGFUSE_PUBLIC_KEY が設定されていません。")
    if not sec:
        sys.exit("ERROR: 環境変数 LANGFUSE_SECRET_KEY が設定されていません。")
    return Langfuse(public_key=pub, secret_key=sec, host=args.host)


# ---------------------------------------------------------------------------
# トレース取得
# ---------------------------------------------------------------------------

def fetch_traces(lf: "Langfuse", args: argparse.Namespace) -> list:
    """Langfuse からトレース一覧を取得する。ページネーション対応。

    Langfuse v3 では lf.api.trace.list() を使用する。
    ページネーションは page=1, 2, ... で行い meta.total_pages で終端を判定する。
    """
    kwargs: dict = {}
    if args.hours is not None:
        kwargs["from_timestamp"] = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    logger.info("fetch_traces 開始: limit=%d hours=%s", args.limit, args.hours)
    print(f"トレースを取得中 (limit={args.limit}, hours={args.hours}) ...")

    traces = []
    page = 1
    per_page = min(args.limit, 100)

    while len(traces) < args.limit:
        try:
            response = lf.api.trace.list(page=page, limit=per_page, **kwargs)
        except Exception as e:
            logger.error("Langfuse への接続に失敗: %s", e, exc_info=True)
            sys.exit(f"ERROR: Langfuse への接続に失敗しました: {e}")

        batch = list(response.data)
        logger.debug("fetch_traces page=%d: %d 件", page, len(batch))
        if not batch:
            break

        traces.extend(batch)

        # meta.total_pages に達したら終了
        if page >= response.meta.total_pages:
            break
        page += 1

    traces = traces[: args.limit]
    logger.info("fetch_traces 完了: %d 件", len(traces))
    print(f"  取得完了: {len(traces)} 件")
    return traces


# ---------------------------------------------------------------------------
# Observation 取得
# ---------------------------------------------------------------------------

def fetch_observations_for_traces(
    lf: "Langfuse",
    traces: list,
    verbose: bool,
) -> dict[str, dict]:
    """
    各トレースの observation を取得する。
    戻り値: {trace_id: {obs_id: obs_object}}
    """
    _MAX_RETRIES = 3
    _RETRY_BASE_WAIT = 2.0  # 秒（指数バックオフの初期値）

    obs_by_trace: dict[str, dict] = {}
    logger.info("fetch_observations_for_traces 開始: %d 件のトレース", len(traces))
    print(f"{len(traces)} 件のトレースから observation を取得中 ...")

    for i, trace in enumerate(traces):
        logger.debug("[%d/%d] trace_id=%s", i + 1, len(traces), trace.id)
        if verbose:
            print(f"  [{i + 1}/{len(traces)}] trace_id={trace.id}")
        obs_map: dict = {}
        page = 1
        try:
            # observations.get_many() の limit 上限は 100 のためページネーションで全件取得
            while True:
                # 429 レートリミット対策: 指数バックオフでリトライ
                for attempt in range(_MAX_RETRIES):
                    try:
                        response = lf.api.observations.get_many(
                            trace_id=trace.id, limit=100, page=page
                        )
                        break
                    except Exception as e:
                        is_rate_limit = "429" in str(e)
                        if is_rate_limit and attempt < _MAX_RETRIES - 1:
                            wait = _RETRY_BASE_WAIT * (2 ** attempt)
                            logger.warning("429 レートリミット: trace=%s page=%d, %.0f秒後にリトライ [%d/%d]",
                                           trace.id, page, wait, attempt + 1, _MAX_RETRIES - 1)
                            print(
                                f"  429 レートリミット (trace {trace.id}, page {page})。"
                                f"{wait:.0f} 秒後にリトライ [{attempt + 1}/{_MAX_RETRIES - 1}]..."
                            )
                            time.sleep(wait)
                        else:
                            raise
                for o in response.data:
                    obs_map[o.id] = o
                if page >= response.meta.total_pages:
                    break
                page += 1
        except Exception as e:
            logger.warning("trace %s の observation 取得に失敗: %s", trace.id, e)
            print(f"  WARNING: trace {trace.id} の observation 取得に失敗: {e}")
        obs_by_trace[trace.id] = obs_map

    total = sum(len(m) for m in obs_by_trace.values())
    logger.info("fetch_observations_for_traces 完了: 合計 %d observation", total)
    print(f"  合計 observation 数: {total}")
    return obs_by_trace


# ---------------------------------------------------------------------------
# スパン分類ロジック
# ---------------------------------------------------------------------------

def _get_safe_metadata(obs) -> tuple[dict, dict]:
    """observation.metadata から resourceAttributes と attributes を安全に取得する。"""
    meta = obs.metadata or {}
    resource_attrs = meta.get("resourceAttributes", {}) if isinstance(meta, dict) else {}
    attrs = meta.get("attributes", {}) if isinstance(meta, dict) else {}
    return resource_attrs, attrs


def resolve_parent_agent(obs, obs_map: dict) -> tuple[str, str]:
    """
    parentObservationId チェーンを辿り、最も近い invoke_agent スパンを探す。
    戻り値: (node_id, node_label)
    """
    current = obs
    seen: set[str] = set()

    while current.parent_observation_id:
        pid = current.parent_observation_id
        if pid in seen:
            break  # 循環ガード
        seen.add(pid)

        parent = obs_map.get(pid)
        if parent is None:
            break

        pname = parent.name or ""
        if pname.startswith("invoke_agent "):
            agent_name = pname[len("invoke_agent "):]
            node_id, node_label = AGENT_NAME_TO_NODE.get(
                agent_name, (f"agent_{agent_name[:20]}", agent_name)
            )
            return node_id, node_label

        current = parent

    return "unknown_agent", "Unknown Agent"


def classify_and_extract_edges(
    observations: list,
    obs_map: dict,
    verbose: bool,
) -> list[dict]:
    """
    トレース内の全 observation を分類し、グラフノード・エッジ情報を返す。

    戻り値の各要素:
      {
        "node_id": str,
        "node_type": str,       # orchestrator / a2a_agent / mcp_server / llm / unknown
        "node_label": str,
        "edge": (src, dst) | None,
        "edge_label": str | None,
      }
    """
    results: list[dict] = []

    for obs in observations:
        name = obs.name or ""
        _, attrs = _get_safe_metadata(obs)

        if verbose:
            print(f"    obs: name={name!r}  type={obs.type}  parent={obs.parent_observation_id}")

        # ── エージェント起動境界スパン ─────────────────────────────────────
        if name.startswith("invoke_agent "):
            agent_name = name[len("invoke_agent "):]
            node_id, node_label = AGENT_NAME_TO_NODE.get(
                agent_name, (f"agent_{agent_name[:20]}", agent_name)
            )
            node_type = "orchestrator" if "Orchestrator" in agent_name else "a2a_agent"
            results.append({
                "node_id": node_id,
                "node_type": node_type,
                "node_label": node_label,
                "edge": None,
                "edge_label": None,
            })

        # ── A2A アウトバウンド呼び出しスパン（SpanKind.CLIENT） ───────────
        # tracer.py start_multiagent_span(): span_name = f"invoke_{instance}"
        elif name.startswith("invoke_") and not name.startswith("invoke_agent"):
            agent_instance = name[len("invoke_"):]
            # A2A 呼び出し元はオーケストレーター
            results.append({
                "node_id": "orchestrator",
                "node_type": "orchestrator",
                "node_label": "Orchestrator Agent",
                "edge": None,
                "edge_label": None,
            })
            # 呼び出し先エージェントのノードを追加し、エッジを張る
            node_id, node_label = AGENT_NAME_TO_NODE.get(
                agent_instance, (f"agent_{agent_instance[:20]}", agent_instance)
            )
            results.append({
                "node_id": node_id,
                "node_type": "a2a_agent",
                "node_label": node_label,
                "edge": ("orchestrator", node_id),
                "edge_label": "A2A",
            })

        # ── MCP ツール呼び出しスパン（Strands type=TOOL, "{server}___{tool}" 形式） ──
        # Strands Agents は MCPClient 経由のツール呼び出しを
        # type=TOOL, name="{server_name}___{tool_name}" として記録する
        elif "___" in name:
            server_name, tool_name = name.split("___", 1)
            parent_id, _ = resolve_parent_agent(obs, obs_map)

            if server_name in SERVER_NAME_TO_MCP_NODE:
                mcp_id, mcp_label = SERVER_NAME_TO_MCP_NODE[server_name]
            elif tool_name in TOOL_TO_MCP_NODE:
                # server_name が不明でもツール名から推定
                mcp_id, mcp_label = TOOL_TO_MCP_NODE[tool_name]
            else:
                # 完全に未知のサーバー → サーバー名からノードを動的生成
                safe = server_name.replace("-", "_")[:25]
                mcp_id = f"mcp_{safe}"
                mcp_label = f"MCP Server\n({server_name})"

            results.append({
                "node_id": mcp_id,
                "node_type": "mcp_server",
                "node_label": mcp_label,
                "edge": (parent_id, mcp_id),
                "edge_label": f"MCP\n({tool_name})",
            })

        # ── ツール呼び出しスパン（execute_tool 形式、旧 SDK との互換） ────────
        elif name.startswith("execute_tool "):
            tool_name = name[len("execute_tool "):]

            if tool_name in TOOL_TO_MCP_NODE:
                mcp_id, mcp_label = TOOL_TO_MCP_NODE[tool_name]
                parent_id, _ = resolve_parent_agent(obs, obs_map)
                results.append({
                    "node_id": mcp_id,
                    "node_type": "mcp_server",
                    "node_label": mcp_label,
                    "edge": (parent_id, mcp_id),
                    "edge_label": f"MCP\n({tool_name})",
                })

            else:
                unknown_id = f"tool_{tool_name[:25]}"
                parent_id, _ = resolve_parent_agent(obs, obs_map)
                results.append({
                    "node_id": unknown_id,
                    "node_type": "unknown",
                    "node_label": f"Tool: {tool_name}",
                    "edge": (parent_id, unknown_id),
                    "edge_label": tool_name,
                })

        # ── LLM 呼び出しスパン ────────────────────────────────────────────
        elif name == "chat":
            model_id = attrs.get("gen_ai.request.model", "bedrock")
            parent_id, _ = resolve_parent_agent(obs, obs_map)
            safe_model = model_id.replace(".", "_").replace("-", "_").replace(":", "_")
            llm_node_id = f"llm_{safe_model[:30]}"
            results.append({
                "node_id": llm_node_id,
                "node_type": "llm",
                "node_label": f"Amazon Bedrock\n({model_id})",
                "edge": (parent_id, llm_node_id),
                "edge_label": "LLM",
            })

        # ── 内部処理スパン（スキップ） ────────────────────────────────────
        elif name == "execute_event_loop_cycle":
            pass

    return results


# ---------------------------------------------------------------------------
# グラフ構築
# ---------------------------------------------------------------------------

def build_graph(obs_by_trace: dict[str, dict], verbose: bool) -> nx.DiGraph:
    """observation データから NetworkX 有向グラフを構築する。"""
    G = nx.DiGraph()
    node_registry: dict[str, dict] = {}
    edge_counts: dict[tuple, int] = defaultdict(int)
    edge_labels: dict[tuple, str] = {}

    for trace_id, obs_map in obs_by_trace.items():
        observations = list(obs_map.values())
        items = classify_and_extract_edges(observations, obs_map, verbose)

        for item in items:
            nid = item["node_id"]
            if nid and nid not in node_registry:
                node_registry[nid] = {
                    "node_type":  item["node_type"],
                    "node_label": item["node_label"],
                }

            if item["edge"]:
                src, dst = item["edge"]
                edge_counts[(src, dst)] += 1
                if item.get("edge_label") and (src, dst) not in edge_labels:
                    edge_labels[(src, dst)] = item["edge_label"]

    # ノードをグラフに追加
    for nid, attrs in node_registry.items():
        G.add_node(nid, **attrs)

    # エッジをグラフに追加（重み = 累計呼び出し回数）
    for (src, dst), count in edge_counts.items():
        if G.has_node(src) and G.has_node(dst):
            label = edge_labels.get((src, dst), "")
            G.add_edge(src, dst, weight=count, edge_label=label)

    logger.info("build_graph 完了: %d ノード, %d エッジ", G.number_of_nodes(), G.number_of_edges())
    print(f"グラフ: {G.number_of_nodes()} ノード, {G.number_of_edges()} エッジ")
    return G


# ---------------------------------------------------------------------------
# システムスキーマ自動生成
# ---------------------------------------------------------------------------

def export_system_schema(
    G: "nx.DiGraph",
    obs_by_trace: dict[str, dict],
    output_path: str,
) -> None:
    """
    NetworkX グラフと observation データから system_schema.json を生成する。

    グラフノード（エージェント・MCP サーバー・LLM）とエッジ（A2A / MCP 通信）を解析し、
    threat_modeling_agent の --system-file に渡せる JSON スキーマを出力する。

    トレースから読み取れない項目（memory, authentication, human_interaction 等）は
    null で出力されるため、手動で補完すること。
    """
    # --- ノード情報とLLMモデルを収集 ---
    agents: list[dict] = []
    mcp_servers_dict: dict[str, dict] = {}   # mcp_node_id -> {"name": str, "tools": set[str]}
    llm_models: set[str] = set()

    for node_id, attrs in G.nodes(data=True):
        node_type = attrs.get("node_type", "unknown")
        label = attrs.get("node_label", node_id)
        display_name = _label_to_display_name(label)

        if node_type == "orchestrator":
            agents.append({
                "name": display_name,
                "role": "全体を調整するオーケストレーター",
                "llm": None,
                "framework": "Strands Agents",
                "reasoning_pattern": "ReAct",
            })
        elif node_type == "a2a_agent":
            agents.append({
                "name": display_name,
                "role": "A2A サブエージェント",
                "llm": None,
                "framework": "Strands Agents",
                "reasoning_pattern": None,
            })
        elif node_type == "mcp_server":
            mcp_servers_dict[node_id] = {"name": display_name, "tools": set()}
        elif node_type == "llm":
            # ラベル形式: "Amazon Bedrock\n({model_id})"
            if "\n(" in label:
                model_id = label.split("\n(", 1)[1].rstrip(")")
                llm_models.add(model_id)

    # --- 全 observation を再スキャンしてMCPツール名を完全収集 ---
    # build_graph() のエッジは (src, dst) ペアごとに最初のツール名しか保持しないため、
    # 同一サーバーへの複数ツール呼び出しを正確に把握するには observation を直接見る。
    for _trace_id, obs_map in obs_by_trace.items():
        observations = list(obs_map.values())
        items = classify_and_extract_edges(observations, obs_map, verbose=False)
        for item in items:
            if item["node_type"] == "mcp_server":
                mcp_id = item["node_id"]
                edge_label = item.get("edge_label") or ""
                # エッジラベル形式: "MCP\n({tool_name})"
                if mcp_id in mcp_servers_dict and "MCP\n(" in edge_label:
                    tool_name = edge_label.split("MCP\n(", 1)[1].rstrip(")")
                    mcp_servers_dict[mcp_id]["tools"].add(tool_name)

    # --- LLM モデルをエージェントに付与 ---
    primary_llm = next(iter(llm_models), None)
    if primary_llm:
        for ag in agents:
            ag["llm"] = primary_llm

    # --- 通信フロー・プロトコル・ツール一覧を収集 ---
    protocols: set[str] = set()
    flows: list[dict] = []
    seen_flow_keys: set[tuple] = set()
    tool_list: list[str] = []

    for src, dst, data in G.edges(data=True):
        edge_label = data.get("edge_label", "")
        src_name = _label_to_display_name(G.nodes[src].get("node_label", src)) if G.has_node(src) else src
        dst_name = _label_to_display_name(G.nodes[dst].get("node_label", dst)) if G.has_node(dst) else dst

        if edge_label == "A2A":
            protocols.add("A2A")
            key = (src_name, dst_name, "A2A")
            if key not in seen_flow_keys:
                seen_flow_keys.add(key)
                flows.append({"from": src_name, "to": dst_name, "protocol": "A2A", "auth": None})

        elif edge_label.startswith("MCP"):
            protocols.add("MCP")
            key = (src_name, dst_name, "MCP")
            if key not in seen_flow_keys:
                seen_flow_keys.add(key)
                flows.append({"from": src_name, "to": dst_name, "protocol": "MCP", "auth": None})

        elif edge_label == "LLM":
            protocols.add("Bedrock")

    # MCP サーバーのツールをフラットなリストに変換
    for mcp_data in mcp_servers_dict.values():
        for tool in sorted(mcp_data["tools"]):
            if tool not in tool_list:
                tool_list.append(tool)

    mcp_servers_list = [
        {"name": v["name"], "tools": sorted(v["tools"]), "hosted_by": None}
        for v in mcp_servers_dict.values()
    ]

    # --- マルチエージェント判定 ---
    a2a_count = sum(1 for ag in agents if ag.get("role") == "A2A サブエージェント")
    is_multi = a2a_count > 0

    # --- スキーマ構築 ---
    schema = {
        "name": "（トレースから自動生成 — 手動で修正してください）",
        "overview": (
            "Langfuse OTEL トレースから自動生成されたシステムスキーマです。\n"
            "null の項目は実際のシステム設定を確認して補完してください。"
        ),
        "components": {
            "agents": agents,
            "mcp_servers": mcp_servers_list,
            "storage": [],
            "external_apis": [],
        },
        "communication": {
            "protocols": sorted(protocols),
            "flows": flows,
            "encryption": None,
        },
        "memory": {
            "short_term": None,
            "long_term": None,
            "vector_db": None,
            "shared_memory": None,
        },
        "tools": {
            "code_execution": None,
            "file_access": None,
            "external_api_calls": len(tool_list) > 0 or None,
            "email_or_messaging": None,
            "database_write": None,
            "tool_list": tool_list,
        },
        "authentication": {
            "enabled": None,
            "method": None,
            "rbac": None,
            "nhi": None,
            "least_privilege": None,
            "token_rotation": None,
        },
        "human_interaction": {
            "hitl": None,
            "user_interaction": None,
            "interaction_type": None,
            "user_trust_level": None,
        },
        "multi_agent": {
            "enabled": is_multi,
            "agent_count": len(agents),
            "architecture": (
                f"オーケストレーター 1 台 + サブエージェント {a2a_count} 台"
                if is_multi else None
            ),
            "delegation_mechanism": "A2A プロトコル" if "A2A" in protocols else None,
            "trust_boundaries": None,
            "shared_resources": [],
        },
        "notes": (
            "このスキーマは Langfuse OTEL トレースから自動生成されました。\n"
            "null の項目は実際のシステム設定を確認して補完してください。\n"
            f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        ),
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)

    abs_path = os.path.abspath(output_path)
    print(f"システムスキーマを出力しました: {abs_path}")
    print(f"脅威モデリング実行例:")
    print(f"  python threat_modeling_agent/threat_modeling_agent.py --system-file {abs_path}")


# ---------------------------------------------------------------------------
# HTML レンダリング
# ---------------------------------------------------------------------------

def render_html(
    G: nx.DiGraph,
    output_path: str,
    args: argparse.Namespace,
    trace_count: int,
) -> None:
    """pyvis で NetworkX グラフをインタラクティブ HTML に変換する。"""
    heading = (
        f"MAS Topology — {trace_count} traces"
        f" — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    net = Network(
        height="850px",
        width="100%",
        directed=True,
        bgcolor="#1a1a2e",
        font_color="#ffffff",
        notebook=False,
        heading=heading,
    )

    for node_id, attrs in G.nodes(data=True):
        node_type = attrs.get("node_type", "unknown")
        style = NODE_STYLES.get(node_type, NODE_STYLES["unknown"])
        label = attrs.get("node_label", node_id)

        out_calls = sum(G[node_id][n]["weight"] for n in G.successors(node_id))
        in_calls  = sum(G[n][node_id]["weight"] for n in G.predecessors(node_id))

        net.add_node(
            node_id,
            label=label,
            shape=style["shape"],
            image=style["image"],
            size=style["size"],
            color={
                "border":     style["border_color"],
                "background": style["border_color"],
                "highlight":  {"border": "#ffffff", "background": style["border_color"]},
                "hover":      {"border": "#ffffff", "background": style["border_color"]},
            },
            borderWidth=style["border_width"],
            borderWidthSelected=style["border_width"] + 2,
            title=(
                f"<b>{label.replace(chr(10), '<br>')}</b><br>"
                f"種別: {node_type}<br>"
                f"送信呼び出し数: {out_calls}<br>"
                f"受信呼び出し数: {in_calls}"
            ),
            font={"size": 13, "color": "#ffffff", "bold": True},
        )

    for src, dst, data in G.edges(data=True):
        weight = data.get("weight", 1)
        edge_label = data.get("edge_label", "")
        # エッジ幅: 1〜8 px でスケール
        width = 1 + min(7, weight // 3)

        net.add_edge(
            src,
            dst,
            width=width,
            title=f"{edge_label}<br>呼び出し数: {weight}",
            label=f"{weight}x",
            color={"color": "#88aacc", "highlight": "#ffffff"},
            arrows={"to": {"enabled": True, "scaleFactor": 1.2}},
            font={"size": 10, "color": "#ccddee", "align": "top"},
        )

    physics_enabled = "false" if args.no_physics else "true"
    net.set_options(f"""
    {{
      "physics": {{
        "enabled": {physics_enabled},
        "solver": "barnesHut",
        "barnesHut": {{
          "gravitationalConstant": -12000,
          "centralGravity": 0.25,
          "springLength": 250,
          "springConstant": 0.035,
          "damping": 0.12,
          "avoidOverlap": 0.8
        }},
        "stabilization": {{"iterations": 300}}
      }},
      "interaction": {{
        "hover": true,
        "tooltipDelay": 150,
        "navigationButtons": true,
        "keyboard": true
      }},
      "edges": {{
        "smooth": {{"type": "curvedCW", "roundness": 0.2}}
      }}
    }}
    """)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    net.write_html(output_path)

    abs_path = os.path.abspath(output_path)
    print(f"\n可視化ファイルを出力しました: {abs_path}")
    print(f"ブラウザで開く: file://{abs_path}")


# ---------------------------------------------------------------------------
# メインエントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    # 1. Langfuse クライアントを作成
    lf = create_langfuse_client(args)

    # 2. トレースを取得
    traces = fetch_traces(lf, args)
    if not traces:
        sys.exit(
            "トレースが見つかりませんでした。\n"
            "  --hours や --limit を調整するか、Langfuse 認証情報を確認してください。"
        )

    # 3. 各トレースの observation を取得
    obs_by_trace = fetch_observations_for_traces(lf, traces, args.verbose)
    total_obs = sum(len(m) for m in obs_by_trace.values())
    if total_obs == 0:
        sys.exit(
            "observation が見つかりませんでした。\n"
            "  OTEL エクスポーターが正しく設定されているか確認してください。"
        )

    # 4. NetworkX グラフを構築
    G = build_graph(obs_by_trace, args.verbose)
    if G.number_of_nodes() == 0:
        sys.exit(
            "グラフにノードが存在しません。\n"
            "  --verbose オプションで observation 名を確認し、"
            "  AGENT_NAME_TO_NODE や TOOL_TO_MCP_NODE を調整してください。"
        )

    # 5. HTML グラフをレンダリング
    render_html(G, args.output, args, trace_count=len(traces))

    # 6. （オプション）システムスキーマ JSON を出力
    if args.export_schema:
        export_system_schema(G, obs_by_trace, args.export_schema)


if __name__ == "__main__":
    main()
