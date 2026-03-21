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

    if heading_override:
        html_content = re.sub(
            r"MAS Topology — .*? — ",
            f"{heading_override} — ",
            html_content,
            count=1,
        )
    return html_content
