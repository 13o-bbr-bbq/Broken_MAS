from __future__ import annotations

import logging
import os
import copy
import re
import tempfile
import types
from datetime import datetime, timezone

import networkx as nx

from visualization.visualize_traces import render_html

logger = logging.getLogger(__name__)


def _safe_id(raw: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", (raw or "unknown").strip())
    return cleaned.strip("_").lower() or "unknown"


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


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def _add_edge_counted(
    edge_counts: dict[tuple[str, str], int],
    edge_labels: dict[tuple[str, str], str],
    src: str,
    dst: str,
    label: str,
) -> None:
    edge_counts[(src, dst)] = edge_counts.get((src, dst), 0) + 1
    if (src, dst) not in edge_labels and label:
        edge_labels[(src, dst)] = label


def _merge_list_of_dict_by_name(base_list: list, overlay_list: list) -> list:
    merged: dict[str, dict] = {}
    order: list[str] = []

    for item in _as_list(base_list):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "")
        key = _norm_name(name) or f"unknown_{len(order)}"
        merged[key] = copy.deepcopy(item)
        order.append(key)

    for item in _as_list(overlay_list):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "")
        key = _norm_name(name) or f"unknown_{len(order)}"
        existing = merged.get(key)
        if existing is None:
            merged[key] = copy.deepcopy(item)
            order.append(key)
            continue

        updated = copy.deepcopy(existing)
        for k, v in item.items():
            if k == "tools":
                existing_tools = set(_as_list(updated.get("tools")))
                overlay_tools = set(_as_list(v))
                updated["tools"] = sorted(t for t in existing_tools | overlay_tools if t)
            elif updated.get(k) in (None, "", [], {}):
                updated[k] = copy.deepcopy(v)
            elif k not in updated:
                updated[k] = copy.deepcopy(v)
        merged[key] = updated

    return [merged[k] for k in order]


def merge_agentcore_and_langfuse_schema(
    agentcore_schema: dict,
    langfuse_schema: dict | None,
    *,
    trace_count: int = 0,
) -> dict:
    """AgentCore 静的スキーマをベースに Langfuse 動的スキーマを補完した統合スキーマを返す。"""
    logger.debug(
        "merge_agentcore_and_langfuse_schema: agentcore=%s langfuse=%s trace_count=%d",
        bool(agentcore_schema), bool(langfuse_schema), trace_count,
    )
    merged = copy.deepcopy(agentcore_schema or {})
    merged.setdefault("system_type", "multiagent")

    ac_components = merged.setdefault("components", {})
    lf_components = ((langfuse_schema or {}).get("components") or {})

    # AgentCore components を優先しつつ Langfuse の欠損要素を補完
    ac_components["orchestrators"] = _merge_list_of_dict_by_name(
        _as_list(ac_components.get("orchestrators")),
        [
            ag for ag in _as_list(lf_components.get("agents"))
            if (
                "orchestrator" in str((ag or {}).get("role") or "").lower()
                or "オーケストレーター" in str((ag or {}).get("role") or "")
            )
        ],
    )
    ac_components["a2a_agents"] = _merge_list_of_dict_by_name(
        _as_list(ac_components.get("a2a_agents")),
        [
            ag for ag in _as_list(lf_components.get("agents"))
            if (
                "orchestrator" not in str((ag or {}).get("role") or "").lower()
                and "オーケストレーター" not in str((ag or {}).get("role") or "")
            )
        ],
    )
    ac_components["mcp_servers"] = _merge_list_of_dict_by_name(
        _as_list(ac_components.get("mcp_servers")),
        _as_list(lf_components.get("mcp_servers")),
    )
    # Threat Modeling 側互換のため agents も保持
    ac_components["agents"] = _merge_list_of_dict_by_name(
        _as_list(ac_components.get("agents")),
        _as_list(lf_components.get("agents")),
    )

    # communication の統合
    merged_comm = merged.setdefault("communication", {})
    lf_comm = ((langfuse_schema or {}).get("communication") or {})

    protocols = set(_as_list(merged_comm.get("protocols"))) | set(_as_list(lf_comm.get("protocols")))
    flows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    key_to_index: dict[tuple[str, str, str], int] = {}

    def _append_flows(values, source: str) -> None:
        for fl in _as_list(values):
            if not isinstance(fl, dict):
                continue
            f = str(fl.get("from") or "").strip()
            t = str(fl.get("to") or "").strip()
            p = str(fl.get("protocol") or "Flow").strip()
            if not f or not t:
                continue
            key = (_norm_name(f), _norm_name(t), p.upper())
            if key in seen:
                idx = key_to_index.get(key)
                if idx is not None:
                    prev_source = flows[idx].get("source")
                    if prev_source != source:
                        flows[idx]["source"] = "both"
                continue
            seen.add(key)
            item = copy.deepcopy(fl)
            item.setdefault("source", source)
            key_to_index[key] = len(flows)
            flows.append(item)

    _append_flows(merged_comm.get("flows"), "agentcore")
    _append_flows(lf_comm.get("flows"), "langfuse")

    if protocols:
        merged_comm["protocols"] = sorted(str(p) for p in protocols if p)
    if flows:
        merged_comm["flows"] = flows
    if merged_comm.get("encryption") is None and lf_comm.get("encryption") is not None:
        merged_comm["encryption"] = lf_comm.get("encryption")

    # セクション単位で欠損補完（AgentCore 優先）
    for section in ["authentication", "memory", "tools", "human_interaction", "multi_agent"]:
        ac_sec = merged.setdefault(section, {})
        lf_sec = ((langfuse_schema or {}).get(section) or {})
        for key, value in lf_sec.items():
            if ac_sec.get(key) in (None, "", [], {}):
                ac_sec[key] = copy.deepcopy(value)
            elif key not in ac_sec:
                ac_sec[key] = copy.deepcopy(value)

    merged["schema_merge"] = {
        "mode": "agentcore_plus_langfuse",
        "sources": {
            "agentcore": bool(agentcore_schema),
            "langfuse": bool(langfuse_schema),
            "langfuse_trace_count": trace_count,
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    logger.info(
        "merge 完了: orchestrators=%d a2a_agents=%d mcp_servers=%d flows=%d",
        len(merged.get("components", {}).get("orchestrators", [])),
        len(merged.get("components", {}).get("a2a_agents", [])),
        len(merged.get("components", {}).get("mcp_servers", [])),
        len((merged.get("communication") or {}).get("flows", [])),
    )
    return merged


def build_graph_from_schema(schema: dict) -> nx.DiGraph:
    """AgentCore/Langfuse/Hybrid の system_schema から可視化用グラフを構築する。"""
    G = nx.DiGraph()
    comps = schema.get("components") or {}

    orchestrators = _as_list(comps.get("orchestrators"))
    a2a_agents = _as_list(comps.get("a2a_agents"))
    mcp_servers = _as_list(comps.get("mcp_servers"))
    gateways = _as_list(comps.get("gateways"))
    agents = _as_list(comps.get("agents"))

    name_to_node: dict[str, str] = {}       # 完全一致用
    norm_to_node: dict[str, str] = {}       # 正規化名フォールバック用
    edge_counts: dict[tuple[str, str], int] = {}
    edge_labels: dict[tuple[str, str], str] = {}

    def _register(name: str, nid: str) -> None:
        name_to_node[name] = nid
        norm_to_node[_norm_name(name)] = nid

    def _resolve(raw_name: str) -> str | None:
        return name_to_node.get(raw_name) or norm_to_node.get(_norm_name(raw_name))

    for orch in orchestrators:
        name = orch.get("name") or orch.get("id") or "Unknown Orchestrator"
        nid = f"orch_{_safe_id(name)}"
        G.add_node(nid, node_type="orchestrator", node_label=name)
        _register(name, nid)

    for ag in a2a_agents:
        name = ag.get("name") or ag.get("id") or "Unknown A2A Agent"
        nid = f"a2a_{_safe_id(name)}"
        G.add_node(nid, node_type="a2a_agent", node_label=name)
        _register(name, nid)

    for ag in agents:
        name = ag.get("name") or ag.get("id") or "Unknown Agent"
        role = (ag.get("role") or "").lower()
        is_orch = ("orchestrator" in role) or ("オーケストレーター" in role)
        prefix = "orch" if is_orch else "a2a"
        node_type = "orchestrator" if is_orch else "a2a_agent"
        nid = f"{prefix}_{_safe_id(name)}"
        G.add_node(nid, node_type=node_type, node_label=name)
        _register(name, nid)

    for srv in mcp_servers:
        name = srv.get("name") or srv.get("id") or "Unknown MCP"
        nid = f"mcp_{_safe_id(name)}"
        G.add_node(nid, node_type="mcp_server", node_label=name)
        _register(name, nid)

    for gw in gateways:
        name = gw.get("name") or gw.get("id") or "Unknown Gateway"
        nid = f"gw_{_safe_id(name)}"
        G.add_node(nid, node_type="unknown", node_label=f"Gateway\n({name})")
        _register(name, nid)

    rt_gw = comps.get("runtime_gateway_connections") or {}
    if isinstance(rt_gw, dict):
        for rt_name, gw_names in rt_gw.items():
            src = _resolve(rt_name)
            if not src:
                src = f"runtime_{_safe_id(str(rt_name))}"
                G.add_node(src, node_type="unknown", node_label=str(rt_name))
                _register(str(rt_name), src)
            for gw_name in _as_list(gw_names):
                gw_name = str(gw_name)
                dst = _resolve(gw_name)
                if not dst:
                    dst = f"gw_{_safe_id(gw_name)}"
                    G.add_node(dst, node_type="unknown", node_label=f"Gateway\n({gw_name})")
                    _register(gw_name, dst)
                _add_edge_counted(edge_counts, edge_labels, src, dst, "Gateway")

    for gw in gateways:
        gw_name = gw.get("name") or gw.get("id") or "Unknown Gateway"
        src = _resolve(gw_name)
        if not src:
            continue
        for target in _as_list(gw.get("targets")):
            target_name = str(target)
            dst = _resolve(target_name)
            if not dst:
                dst = f"mcp_{_safe_id(target_name)}"
                G.add_node(dst, node_type="mcp_server", node_label=target_name)
                _register(target_name, dst)
            _add_edge_counted(edge_counts, edge_labels, src, dst, "MCP")

    for fl in _as_list((schema.get("communication") or {}).get("flows")):
        if not isinstance(fl, dict):
            continue
        from_name = str(fl.get("from") or "")
        to_name = str(fl.get("to") or "")
        if not from_name or not to_name:
            continue

        src = _resolve(from_name)
        if not src:
            src = f"node_{_safe_id(from_name)}"
            G.add_node(src, node_type="unknown", node_label=from_name)
            _register(from_name, src)

        dst = _resolve(to_name)
        if not dst:
            dst = f"node_{_safe_id(to_name)}"
            G.add_node(dst, node_type="unknown", node_label=to_name)
            _register(to_name, dst)

        proto = fl.get("protocol") or "Flow"
        _add_edge_counted(edge_counts, edge_labels, src, dst, str(proto))

    for (src, dst), count in edge_counts.items():
        if G.has_node(src) and G.has_node(dst):
            G.add_edge(src, dst, weight=count, edge_label=edge_labels.get((src, dst), ""))

    logger.debug("build_graph_from_schema 完了: %d ノード, %d エッジ", G.number_of_nodes(), G.number_of_edges())
    return G


# ---------------------------------------------------------------------------
# Docker Compose ローカルトポロジー
# ---------------------------------------------------------------------------

#: MCP サーバーディレクトリ名 → 表示名のマッピング（このプロジェクト固有）
_MCP_SERVER_META: dict[str, str] = {
    "broken_mcp_server_1": "MCP Server 1 (Hotel Search)",
    "broken_mcp_server_2": "MCP Server 2 (Hotel Details)",
    "broken_mcp_server_3": "MCP Server 3 (Availability)",
    "broken_mcp_server_4": "MCP Server 4 (Reservation)",
    "rogue_mcp_server_1":  "MCP Server [Rogue] (Partner Deals)",
}


def _classify_compose_service(name: str) -> str | None:
    """サービス名からコンポーネント種別を判定する。None はトポロジーから除外。"""
    n = name.lower()
    if "orchestrator" in n:
        return "orchestrator"
    if "rogue" in n and ("a2a" in n or "agent" in n):
        return "rogue_a2a"
    if "a2a" in n:
        return "a2a_agent"
    if "mcp" in n and ("gateway" in n or "gw" in n):
        return "mcp_gateway"
    # dashboard / nginx などインフラ系は除外
    return None


def _display_name_from_service(svc_name: str) -> str:
    """サービス名（例: rogue-a2a-agent-1）を人間可読な表示名に変換する。"""
    n = svc_name.lower()
    is_rogue = "rogue" in n
    prefix = "[Rogue] " if is_rogue else ""
    suffix_m = re.search(r"(\d+)$", svc_name)
    suffix = f" {suffix_m.group(1)}" if suffix_m else ""
    if "orchestrator" in n:
        return "Orchestrator"
    if "a2a" in n and "agent" in n:
        return f"{prefix}A2A Agent{suffix}"
    return prefix + " ".join(p.capitalize() for p in re.split(r"[-_]", svc_name))


def _scan_dockerfile_mcp_dirs(df_path: str) -> list[str]:
    """Dockerfile から COPY <broken/rogue_mcp_server_N>/ 行のディレクトリ名を返す。"""
    pattern = re.compile(r"^COPY\s+((?:broken|rogue)_mcp_server_\d+)/", re.IGNORECASE)
    dirs: list[str] = []
    try:
        with open(df_path, "r", encoding="utf-8") as f:
            for line in f:
                m = pattern.match(line.strip())
                if m:
                    d = m.group(1)
                    if d not in dirs:
                        dirs.append(d)
    except OSError:
        pass
    return dirs


def _scan_mcp_tool_names(server_dir_path: str) -> list[str]:
    """MCP サーバーディレクトリ内の .py ファイルから @mcp.tool(name="...") を収集する。"""
    import glob as _glob

    tools: list[str] = []
    for py_path in _glob.glob(os.path.join(server_dir_path, "*.py")):
        try:
            with open(py_path, "r", encoding="utf-8") as f:
                src = f.read()
        except OSError:
            continue
        # @mcp.tool(...) ブロックを区切りとして分割し、name= を検索
        for block in src.split("@mcp.tool")[1:]:
            m = re.search(r'\bname\s*=\s*["\']([^"\']+)', block)
            if m and m.group(1) not in tools:
                tools.append(m.group(1))
    return tools


def _extract_url_edges(
    services: dict,
    svc_types: dict[str, str],
) -> list[tuple[str, str, str]]:
    """
    docker-compose.yml の environment セクションの URL 値から接続エッジを抽出する。
    Returns: [(src_service, dst_service, protocol), ...]
    """
    url_re = re.compile(r"^https?://([a-zA-Z0-9_-]+)(?::\d+)?(?:/.*)?$")
    edges: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for svc_name, svc_cfg in services.items():
        if svc_name not in svc_types:
            continue
        env = svc_cfg.get("environment") or {}
        # リスト形式 ["KEY=value", ...] を dict に変換
        if isinstance(env, list):
            tmp: dict[str, str] = {}
            for item in env:
                if isinstance(item, str) and "=" in item:
                    k, v = item.split("=", 1)
                    tmp[k] = v
            env = tmp

        for env_key, env_val in env.items():
            if not isinstance(env_val, str):
                continue
            m = url_re.match(env_val.strip())
            if not m:
                continue
            hostname = m.group(1)
            # Docker Compose ではサービス名がそのままホスト名になる
            if hostname not in services or hostname == svc_name:
                continue
            if hostname not in svc_types:
                continue
            k = env_key.upper()
            proto = "A2A" if "A2A" in k else ("MCP" if ("GW" in k or "MCP" in k) else "HTTP")
            key = (svc_name, hostname, proto)
            if key not in seen:
                seen.add(key)
                edges.append(key)

    return edges


def build_graph_from_compose(
    compose_path: str,
    repo_root: str | None = None,
) -> tuple[nx.DiGraph, dict]:
    """
    docker-compose.yml とローカルソースコードから MAS トポロジーを構築する。

    処理内容:
      1. docker-compose.yml をパースしてサービスを分類
      2. Dockerfile.mcp-gateway-* を解析して gateway → MCP サーバーのマッピングを取得
      3. MCP サーバー Python ファイルを走査してツール名を収集
      4. 環境変数の URL から接続エッジを抽出（gateway を経由するエッジは個別 MCP サーバーに展開）
      5. NetworkX グラフと system_schema 互換 dict を生成して返す

    Parameters
    ----------
    compose_path : str
        docker-compose.yml のパス
    repo_root : str | None
        リポジトリルート（Dockerfile や MCP サーバーソースの基点）。
        None の場合は compose_path の親ディレクトリを使用。

    Returns
    -------
    (G, schema) : tuple[nx.DiGraph, dict]
        G      : render_graph_to_html() に渡すグラフ
        schema : build_graph_from_schema / Threat Modeling 互換のスキーマ dict
    """
    import yaml
    from datetime import datetime, timezone
    from pathlib import Path

    compose_path_obj = Path(compose_path)
    root = Path(repo_root) if repo_root else compose_path_obj.parent

    with open(compose_path_obj, "r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    services: dict = compose.get("services") or {}

    # ── 1. サービス分類 ──────────────────────────────────────────────────
    svc_types: dict[str, str] = {}
    for svc_name in services:
        t = _classify_compose_service(svc_name)
        if t is not None:
            svc_types[svc_name] = t

    # ── 2. Dockerfile から gateway → sub-server ディレクトリマッピングを取得 ──
    gateway_to_server_dirs: dict[str, list[str]] = {}
    for svc_name, svc_type in svc_types.items():
        if svc_type != "mcp_gateway":
            continue
        svc_cfg = services[svc_name]
        build = svc_cfg.get("build") or {}
        if isinstance(build, str):
            df_path = str(root / build / "Dockerfile")
        else:
            df_name = build.get("dockerfile", "Dockerfile")
            df_path = str(root / df_name)
            if not os.path.exists(df_path):
                df_path = str(root / build.get("context", ".") / "Dockerfile")
        dirs = _scan_dockerfile_mcp_dirs(df_path)
        if dirs:
            gateway_to_server_dirs[svc_name] = dirs
            logger.debug("gateway '%s' → servers: %s", svc_name, dirs)

    # ── 3. 各 MCP サーバーのツール名をスキャン ─────────────────────────────
    all_server_dirs: list[str] = sorted(
        {d for dirs in gateway_to_server_dirs.values() for d in dirs}
    )
    server_tools: dict[str, list[str]] = {}
    for srv_dir in all_server_dirs:
        tools = _scan_mcp_tool_names(str(root / srv_dir))
        server_tools[srv_dir] = tools
        logger.debug("MCP server '%s' tools: %s", srv_dir, tools)

    # ── 4. 環境変数からエッジを抽出 → gateway 経由を個別サーバーに展開 ────────
    raw_edges = _extract_url_edges(services, svc_types)
    expanded_edges: list[tuple[str, str, str]] = []
    seen_exp: set[tuple[str, str, str]] = set()
    for src, dst, proto in raw_edges:
        if svc_types.get(dst) == "mcp_gateway":
            for srv_dir in gateway_to_server_dirs.get(dst, []):
                key = (src, srv_dir, "MCP")
                if key not in seen_exp:
                    seen_exp.add(key)
                    expanded_edges.append(key)
        else:
            key = (src, dst, proto)
            if key not in seen_exp:
                seen_exp.add(key)
                expanded_edges.append(key)

    # ── 5. NetworkX グラフを構築 ────────────────────────────────────────
    G = nx.DiGraph()
    name_to_nid: dict[str, str] = {}
    edge_counts_d: dict[tuple[str, str], int] = {}
    edge_labels_d: dict[tuple[str, str], str] = {}

    # エージェント系ノード（orchestrator / a2a_agent / rogue_a2a）
    for svc_name, svc_type in svc_types.items():
        if svc_type == "mcp_gateway":
            continue
        label = _display_name_from_service(svc_name)
        node_type = "orchestrator" if svc_type == "orchestrator" else "a2a_agent"
        prefix = "orch" if svc_type == "orchestrator" else "a2a"
        nid = f"{prefix}_{_safe_id(svc_name)}"
        G.add_node(nid, node_type=node_type, node_label=label)
        name_to_nid[svc_name] = nid

    # MCP サーバーノード（個別サーバー単位）
    for srv_dir in all_server_dirs:
        display = _MCP_SERVER_META.get(srv_dir, srv_dir)
        tools = server_tools.get(srv_dir, [])
        label = display + ("\n" + "\n".join(tools) if tools else "")
        nid = f"mcp_{_safe_id(srv_dir)}"
        G.add_node(nid, node_type="mcp_server", node_label=label)
        name_to_nid[srv_dir] = nid

    # エッジ追加
    for src_name, dst_name, proto in expanded_edges:
        src_nid = name_to_nid.get(src_name)
        dst_nid = name_to_nid.get(dst_name)
        if src_nid and dst_nid:
            _add_edge_counted(edge_counts_d, edge_labels_d, src_nid, dst_nid, proto)

    for (src_nid, dst_nid), count in edge_counts_d.items():
        G.add_edge(
            src_nid, dst_nid,
            weight=count,
            edge_label=edge_labels_d.get((src_nid, dst_nid), ""),
        )

    # ── 6. system_schema dict 生成 ─────────────────────────────────────
    orchestrators_s: list[dict] = []
    a2a_agents_s: list[dict] = []
    for svc_name, svc_type in svc_types.items():
        if svc_type == "mcp_gateway":
            continue
        display = _display_name_from_service(svc_name)
        if svc_type == "orchestrator":
            orchestrators_s.append({"name": display, "role": "Orchestrator"})
        elif svc_type == "rogue_a2a":
            a2a_agents_s.append({"name": display, "role": "Rogue A2A Agent"})
        else:
            a2a_agents_s.append({"name": display, "role": "A2A Agent"})

    mcp_servers_s: list[dict] = [
        {
            "name": _MCP_SERVER_META.get(srv_dir, srv_dir),
            "tools": server_tools.get(srv_dir, []),
        }
        for srv_dir in all_server_dirs
    ]

    # 表示名 → スキーマ名の逆引きマップ（フロー生成用）
    name_to_display: dict[str, str] = {}
    for svc_name, svc_type in svc_types.items():
        if svc_type != "mcp_gateway":
            name_to_display[svc_name] = _display_name_from_service(svc_name)
    for srv_dir in all_server_dirs:
        name_to_display[srv_dir] = _MCP_SERVER_META.get(srv_dir, srv_dir)

    flows: list[dict] = []
    seen_flows: set[tuple[str, str, str]] = set()
    for src, dst, proto in expanded_edges:
        f = name_to_display.get(src, src)
        t = name_to_display.get(dst, dst)
        key = (_norm_name(f), _norm_name(t), proto.upper())
        if key not in seen_flows:
            seen_flows.add(key)
            flows.append({"from": f, "to": t, "protocol": proto})

    all_tools = [tool for tools in server_tools.values() for tool in tools]
    schema = {
        "name": "Broken MAS (Docker Compose)",
        "overview": "Multi-Agent System topology parsed from docker-compose.yml and source code.",
        "components": {
            "orchestrators": orchestrators_s,
            "a2a_agents": a2a_agents_s,
            "mcp_servers": mcp_servers_s,
            "agents": orchestrators_s + a2a_agents_s,
            "storage": [],
            "external_apis": [],
        },
        "communication": {
            "protocols": sorted({e[2] for e in expanded_edges}),
            "flows": flows,
            "encryption": None,
        },
        "authentication": {},
        "memory": {},
        "tools": {"tool_list": all_tools},
        "multi_agent": {
            "enabled": True,
            "agent_count": len(orchestrators_s) + len(a2a_agents_s),
            "architecture": "Orchestrator + A2A Agents",
            "delegation_mechanism": "A2A protocol",
        },
        "schema_source": "docker_compose",
        "notes": (
            f"Generated from {compose_path_obj.name} at: "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        ),
    }

    logger.info(
        "build_graph_from_compose 完了: %d ノード, %d エッジ, %d mcp_servers",
        G.number_of_nodes(), G.number_of_edges(), len(mcp_servers_s),
    )
    return G, schema


def render_graph_to_html(
    G: nx.DiGraph,
    *,
    no_physics: bool,
    heading_trace_count: int,
    heading_override: str | None,
) -> str:
    args_mock = types.SimpleNamespace(no_physics=no_physics)
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, dir="/tmp") as tmp:
        html_path = tmp.name

    logger.debug("render_graph_to_html: tmp=%s nodes=%d", html_path, G.number_of_nodes())
    render_html(G, html_path, args_mock, trace_count=heading_trace_count)

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    os.unlink(html_path)
    logger.debug("render_graph_to_html 完了: html_len=%d", len(html_content))

    # pyvis は同じ heading を <h1> タグで2箇所出力するため、1つ目を削除する
    html_content = re.sub(r"<h1>.*?</h1>", "", html_content, count=1)

    if heading_override:
        html_content = re.sub(
            r"MAS Topology — .*? — ",
            f"{heading_override} — ",
            html_content,
        )
    return html_content
