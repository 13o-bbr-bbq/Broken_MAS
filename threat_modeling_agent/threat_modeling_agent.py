"""
OWASP Agentic AI 机上脅威モデリングエージェント

使用方法:
    # システム記述を JSON ファイルで指定（visualize_traces.py --export-schema で自動生成）
    python threat_modeling_agent.py --system-file system_schema.json

    # システム記述をテキストで指定
    python threat_modeling_agent.py --system-file system.md
    python threat_modeling_agent.py --system-description "オーケストレーター + A2Aエージェント構成..."

    # 参考ドキュメントを追加（複数指定可）
    python threat_modeling_agent.py --system-file system_schema.json \\
        --reference-doc company_security_policy.md \\
        --reference-doc additional_threats.pdf

    # カスタム知識ベースを指定
    python threat_modeling_agent.py --system-file system_schema.json \\
        --knowledge-base custom_knowledge_base.json

    # Markdown レポートをファイルに出力（省略時は threat_model_report.md を生成）
    python threat_modeling_agent.py --system-file system_schema.json \\
        --output-file report.md

    # JSON 形式で出力（省略時は threat_model_report.json を生成）
    python threat_modeling_agent.py --system-file system_schema.json \\
        --output-format json --output-file report.json
"""

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from strands import Agent, tool
from strands.models import BedrockModel

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# --- 固定パス ---

_BASE_DIR = Path(__file__).parent
_DEFAULT_KB_PATH = _BASE_DIR / "knowledge" / "owasp_knowledge_base.json"
_PHASE_PROMPTS_DIR = _BASE_DIR / "phase_agents" / "phase_prompts"
_SOP_PATH = _BASE_DIR / "sops" / "threat_modeling.sop.md"

# --- 実行時に差し替え可能な知識ベース（可変コンテナ） ---
# @tool デコレーターは定義時に関数をラップするため、
# 辞書の「参照先」は変えず「中身」を差し替える方式をとる。
_knowledge_base: dict = {}


# --- ドキュメント読み込みユーティリティ ---

def _load_text_document(path: Path) -> str:
    """テキスト・Markdown ファイルを読み込む"""
    return path.read_text(encoding="utf-8")


def _load_pdf_document(path: Path) -> str:
    """PDF ファイルをテキストとして読み込む（pdfminer.six が必要）"""
    try:
        from pdfminer.high_level import extract_text
        return extract_text(str(path))
    except ImportError:
        raise SystemExit(
            "Error: PDF の読み込みには pdfminer.six が必要です。\n"
            "  pip install pdfminer.six"
        )


def _normalize_list(value: Any) -> list:
    """値を list に正規化する。dict は values、単一値は 1 要素 list として扱う。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        if isinstance(value.get("items"), list):
            return value["items"]
        return list(value.values())
    return [value]


def _normalize_entity_list(value: Any) -> list[dict]:
    """components の各セクションを list[dict] に正規化する。"""
    items = _normalize_list(value)
    normalized: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(item)
        else:
            normalized.append({"name": str(item)})
    return normalized


def _resolve_components(data: dict) -> dict:
    """components セクションの位置ゆれに対応して解決する。"""
    comps = data.get("components")
    if not isinstance(comps, dict):
        comps = {}
    resolved = dict(comps)
    for key in (
        "agents",
        "orchestrators",
        "a2a_agents",
        "mcp_servers",
        "gateways",
        "runtime_gateway_connections",
        "storage",
        "external_apis",
    ):
        if key not in resolved and key in data:
            resolved[key] = data.get(key)
    return resolved


def _system_dict_to_markdown(data: dict) -> str:
    """
    system_schema 形式の Python dict を LLM が解釈しやすい Markdown テキストに変換する。

    YAML / JSON どちらからパースされた dict でも受け取れる共通変換関数。
    null / None のフィールドは出力から省略する。
    """
    lines: list[str] = []

    # --- 基本情報 ---
    name = data.get("name") or "（名称未設定）"
    overview = data.get("overview") or "（概要なし）"
    lines += [f"# システム名: {name}", "", "## 概要", "", str(overview).strip(), ""]

    # --- コンポーネント ---
    # Langfuse 形式 (components.agents) と AgentCore 形式
    # (components.orchestrators / a2a_agents / gateways / runtime_gateway_connections)
    # の両方に対応する。
    schema_components = _resolve_components(data)

    # エージェント一覧を統合
    agents: list[dict] = _normalize_entity_list(schema_components.get("agents"))
    for orch in _normalize_entity_list(schema_components.get("orchestrators")):
        agents.append({
            "name": orch.get("name") or orch.get("id") or "?",
            "role": "オーケストレーター（HTTP エントリポイント）",
            "status": orch.get("status"),
        })
    for a2a in _normalize_entity_list(schema_components.get("a2a_agents")):
        agents.append({
            "name": a2a.get("name") or a2a.get("id") or "?",
            "role": "A2A エージェント（サブエージェント）",
            "status": a2a.get("status"),
        })
    lines += ["## コンポーネント一覧", ""]
    lines += ["### エージェント", ""]
    if agents:
        for ag in agents:
            lines.append(f"- **{ag.get('name') or '?'}**")
            if ag.get("role"):
                lines.append(f"  - 役割: {ag['role']}")
            if ag.get("llm"):
                lines.append(f"  - LLM: {ag['llm']}")
            if ag.get("framework"):
                lines.append(f"  - フレームワーク: {ag['framework']}")
            if ag.get("reasoning_pattern"):
                lines.append(f"  - 推論パターン: {ag['reasoning_pattern']}")
            if ag.get("status"):
                lines.append(f"  - ステータス: {ag['status']}")
    else:
        lines.append("- 情報不足（エージェント情報がスキーマに含まれていません）")
    lines.append("")

    mcp_servers = _normalize_entity_list(schema_components.get("mcp_servers"))
    lines += ["### MCP サーバー", ""]
    if mcp_servers:
        for srv in mcp_servers:
            lines.append(f"- **{srv.get('name') or srv.get('id') or '?'}**")
            if srv.get("hosted_by"):
                lines.append(f"  - ホスト環境: {srv['hosted_by']}")
            tools = _normalize_list(srv.get("tools"))
            if tools:
                lines.append(f"  - 提供ツール: {', '.join(map(str, tools))}")
            if srv.get("status"):
                lines.append(f"  - ステータス: {srv['status']}")
    else:
        lines.append("- 情報不足")
    lines.append("")

    storage = _normalize_entity_list(schema_components.get("storage"))
    lines += ["### データストレージ", ""]
    if storage:
        for st in storage:
            lines.append(f"- **{st.get('name') or '?'}** ({st.get('type') or '?'})")
            if st.get("contains"):
                lines.append(f"  - 格納データ: {st['contains']}")
            if st.get("access_control"):
                lines.append(f"  - アクセス制御: {st['access_control']}")
    else:
        lines.append("- 情報不足")
    lines.append("")

    external_apis = _normalize_entity_list(schema_components.get("external_apis"))
    lines += ["### 外部 API・サービス", ""]
    if external_apis:
        for api in external_apis:
            trusted_label = " (trusted)" if api.get("trusted") else ""
            lines.append(f"- **{api.get('name') or '?'}**{trusted_label}")
            if api.get("type"):
                lines.append(f"  - タイプ: {api['type']}")
            if api.get("auth"):
                lines.append(f"  - 認証: {api['auth']}")
    else:
        lines.append("- 情報不足")
    lines.append("")

    # AgentCore 形式: Gateway 情報
    gateways = _normalize_entity_list(schema_components.get("gateways"))
    if gateways:
        lines += ["### AgentCore Gateway", ""]
        for gw in gateways:
            targets = _normalize_list(gw.get("targets"))
            lines.append(f"- **{gw.get('name') or gw.get('id') or '?'}**")
            if targets:
                lines.append(f"  - 接続 MCP サーバー: {', '.join(map(str, targets))}")
            if gw.get("status"):
                lines.append(f"  - ステータス: {gw['status']}")
        lines.append("")

    # AgentCore 形式: エージェント-Gateway 接続
    connections = schema_components.get("runtime_gateway_connections") or {}
    if connections:
        lines += ["## エージェント-Gateway 接続", ""]
        for rt_name, gw_names in connections.items():
            if gw_names:
                lines.append(f"- {rt_name} → {', '.join(map(str, _normalize_list(gw_names)))}")
            else:
                lines.append(f"- {rt_name} → (接続情報なし)")
        lines.append("")

    # --- 通信フロー ---
    comm = data.get("communication") or {}
    if comm:
        lines += ["## 通信フロー", ""]
        protocols = comm.get("protocols") or []
        if protocols:
            lines.append(f"- 使用プロトコル: {', '.join(protocols)}")
        encryption = comm.get("encryption")
        if encryption is not None:
            lines.append(f"- 通信暗号化: {'あり' if encryption else 'なし'}")
        flows = comm.get("flows") or []
        if flows:
            lines.append("- 通信フロー:")
            for fl in flows:
                auth_label = f" (認証: {fl['auth']})" if fl.get("auth") else ""
                lines.append(
                    f"  - {fl.get('from') or '?'} → {fl.get('to') or '?'}"
                    f" [{fl.get('protocol') or '?'}]{auth_label}"
                )
        lines.append("")

    # --- 記憶機構 ---
    memory = data.get("memory") or {}
    mem_lines: list[str] = []
    flags = {
        "short_term": "短期記憶（セッション内）",
        "long_term": "長期記憶（永続化）",
        "vector_db": "ベクトル DB / RAG",
        "shared_memory": "共有メモリ（マルチエージェント・ユーザー間）",
    }
    for key, label in flags.items():
        val = memory.get(key)
        if val is not None:
            mem_lines.append(f"- {label}: {'あり' if val else 'なし'}")
    if memory.get("details"):
        mem_lines.append(f"- 詳細: {str(memory['details']).strip()}")
    if mem_lines:
        lines += ["## 記憶機構", ""] + mem_lines + [""]

    # --- ツール・実行能力 ---
    tools_sec = data.get("tools") or {}
    tool_lines: list[str] = []
    capability_flags = {
        "code_execution": "コード生成・実行",
        "file_access": "ファイルシステムアクセス",
        "external_api_calls": "外部 API 呼び出し",
        "email_or_messaging": "メール・メッセージ送信",
        "database_write": "DB 書き込み",
    }
    for key, label in capability_flags.items():
        val = tools_sec.get(key)
        if val is not None:
            tool_lines.append(f"- {label}: {'あり' if val else 'なし'}")
    tool_list = tools_sec.get("tool_list") or []
    if tool_list:
        tool_lines.append(f"- ツール一覧: {', '.join(tool_list)}")
    if tool_lines:
        lines += ["## ツール・実行能力", ""] + tool_lines + [""]

    # --- 認証・認可 ---
    auth = data.get("authentication") or {}
    auth_lines: list[str] = []
    if auth.get("enabled") is not None:
        auth_lines.append(f"- 認証: {'有効' if auth['enabled'] else '無効'}")
    if auth.get("method"):
        auth_lines.append(f"- 認証方式: {auth['method']}")
    if auth.get("rbac") is not None:
        auth_lines.append(f"- RBAC: {'あり' if auth['rbac'] else 'なし'}")
    if auth.get("nhi") is not None:
        auth_lines.append(f"- 非人間 ID (NHI): {'あり' if auth['nhi'] else 'なし'}")
    if auth.get("nhi_details"):
        auth_lines.append(f"- NHI 詳細: {str(auth['nhi_details']).strip()}")
    if auth.get("least_privilege") is not None:
        auth_lines.append(f"- 最小権限原則: {'適用あり' if auth['least_privilege'] else '適用なし'}")
    if auth.get("token_rotation") is not None:
        auth_lines.append(f"- トークンローテーション: {'あり' if auth['token_rotation'] else 'なし'}")
    if auth_lines:
        lines += ["## 認証・認可", ""] + auth_lines + [""]

    # --- 人間の関与 ---
    human = data.get("human_interaction") or {}
    human_lines: list[str] = []
    if human.get("hitl") is not None:
        human_lines.append(f"- Human-in-the-Loop (HITL): {'あり' if human['hitl'] else 'なし'}")
    if human.get("hitl_details"):
        human_lines.append(f"- HITL 詳細: {human['hitl_details']}")
    if human.get("user_interaction") is not None:
        human_lines.append(f"- ユーザー直接インタラクション: {'あり' if human['user_interaction'] else 'なし'}")
    if human.get("interaction_type"):
        human_lines.append(f"- インタラクション形式: {human['interaction_type']}")
    if human.get("user_trust_level"):
        human_lines.append(f"- ユーザー信頼レベル: {human['user_trust_level']}")
    if human_lines:
        lines += ["## 人間の関与", ""] + human_lines + [""]

    # --- マルチエージェント構成 ---
    multi = data.get("multi_agent") or {}
    multi_lines: list[str] = []
    if multi.get("enabled") is not None:
        multi_lines.append(f"- マルチエージェント: {'有効' if multi['enabled'] else '無効'}")
    if multi.get("agent_count") is not None:
        multi_lines.append(f"- エージェント数: {multi['agent_count']}")
    if multi.get("architecture"):
        multi_lines.append(f"- アーキテクチャ: {multi['architecture']}")
    if multi.get("delegation_mechanism"):
        multi_lines.append(f"- タスク委譲機構: {multi['delegation_mechanism']}")
    if multi.get("trust_boundaries"):
        multi_lines.append(f"- 信頼境界: {multi['trust_boundaries']}")
    shared = multi.get("shared_resources") or []
    if shared:
        multi_lines.append(f"- 共有リソース: {', '.join(shared)}")
    if multi_lines:
        lines += ["## マルチエージェント構成", ""] + multi_lines + [""]

    # --- 追加情報 ---
    notes = data.get("notes") or ""
    if str(notes).strip():
        lines += ["## 追加情報・備考", "", str(notes).strip(), ""]

    return "\n".join(lines)


def _load_json_system(path: Path) -> str:
    """
    JSON 形式のシステム記述ファイルを Markdown テキストに変換する。

    visualize_traces.py の --export-schema で自動生成されたファイルを
    そのまま --system-file に渡すことができる。
    """
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return _system_dict_to_markdown(data)


def load_document(path: Path) -> str:
    """拡張子に応じてドキュメントを読み込む"""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf_document(path)
    if suffix == ".json":
        return _load_json_system(path)
    return _load_text_document(path)


def load_knowledge_base(kb_path: Path) -> dict:
    """脅威知識ベース JSON を読み込む"""
    return json.loads(kb_path.read_text(encoding="utf-8"))


# --- SOP ローダー（Agent SOP の _with_input パターン） ---

def _make_system_prompt(
    system_description: str,
    output_format: str,
    session_id: str,
    reference_docs: list[tuple[str, str]],  # [(filename, content), ...]
) -> str:
    """
    SOP コンテンツとユーザー入力を <agent-sop> XML でラップする。
    参考ドキュメントは <reference-documents> セクションに含める。
    """
    sop_content = _SOP_PATH.read_text(encoding="utf-8")

    ref_section = ""
    if reference_docs:
        doc_blocks = []
        for name, content in reference_docs:
            doc_blocks.append(f"### {name}\n\n{content}")
        ref_section = (
            "\n\n## 参考ドキュメント（追加コンテキスト）\n\n"
            + "\n\n---\n\n".join(doc_blocks)
        )

    return f"""<agent-sop name="threat-modeling">
<content>
{sop_content}
</content>
<user-input>
system_description: {system_description}
output_format: {output_format}
session_id: {session_id}{ref_section}
</user-input>
</agent-sop>"""


# --- セッション状態管理（フェーズ結果・生成レポートの外部保存） ---

_session_findings: dict[str, list[dict]] = {}

# generate_threat_report() が生成した純粋なレポート本文を保存する。
# response.message はLLMが前置き・後置きを付加することがあるため、
# ツール内で直接保存してから run() で取り出すことで純粋なレポートを保証する。
_generated_reports: dict[str, str] = {}


def _get_findings(session_id: str) -> list[dict]:
    return _session_findings.get(session_id, [])


def _clear_session(session_id: str) -> None:
    _session_findings.pop(session_id, None)


# --- フェーズサブエージェント用ツール ---

@tool
def get_threat_detail(threat_id: str) -> str:
    """
    指定された脅威 ID に対応する脅威フレームワークの詳細を取得する。

    このツールは各フェーズのサブエージェントが脅威評価の前に必ず呼び出すことで、
    LLM の訓練データへの依存を排除し、指定されたガイドライン原文に基づく
    評価を保証する。

    Args:
        threat_id: 脅威 ID（例: "T1", "T6", "T16"）

    Returns:
        脅威の定義・シナリオ・軽減策を含む JSON 文字列
    """
    key = threat_id.strip().upper()
    threat = _knowledge_base.get(key)
    if not threat:
        valid_ids = ", ".join(sorted(_knowledge_base.keys()))
        return f"Error: '{key}' は知識ベースに存在しません。有効な ID: {valid_ids}"
    return json.dumps(threat, ensure_ascii=False, indent=2)


# --- オーケストレーター用ツール ---

@tool
def run_phase(phase_num: int, architecture: str, previous_findings: str) -> str:
    """
    指定されたフェーズの脅威評価を、独立したコンテキストのサブエージェントで実行する。

    各フェーズは完全に独立したコンテキストで実行されるため、
    前フェーズの会話履歴によるコンテキスト腐敗が発生しない。

    Args:
        phase_num: フェーズ番号（0〜6）
        architecture: 評価対象システムのアーキテクチャ記述
        previous_findings: 前フェーズまでの分析結果サマリー（参照用）

    Returns:
        フェーズの評価結果テキスト
    """
    phase_files = sorted(_PHASE_PROMPTS_DIR.glob(f"phase_{phase_num}_*.md"))
    if not phase_files:
        return f"Error: Phase {phase_num} のプロンプトファイルが見つかりません。"

    phase_prompt = phase_files[0].read_text(encoding="utf-8")

    # フェーズサブエージェントは get_threat_detail のみを持つ（最小権限）
    phase_agent = Agent(
        model=BedrockModel(model_id=os.environ.get("AWS_BEDROCK_MODEL_ID")),
        system_prompt=phase_prompt,
        tools=[get_threat_detail],
    )

    user_input = f"""以下のシステムアーキテクチャを評価してください。

## 対象システムアーキテクチャ

{architecture}

## 前フェーズまでの分析結果（参照用）

{previous_findings if previous_findings else "（なし：最初のフェーズ）"}
"""
    result = phase_agent(user_input)
    return result.message


@tool
def record_phase_finding(
    phase_num: int,
    phase_title: str,
    findings: str,
    session_id: str = "default",
) -> str:
    """
    フェーズの評価結果を外部状態として記録する。

    LLM のコンテキストに依存せず外部辞書に保存することで、
    全フェーズ完了後のレポート生成が記憶による幻覚なしに行える。

    Args:
        phase_num: フェーズ番号（0〜6）
        phase_title: フェーズのタイトル
        findings: 評価結果テキスト
        session_id: セッション識別子

    Returns:
        記録完了メッセージ
    """
    if session_id not in _session_findings:
        _session_findings[session_id] = []

    _session_findings[session_id].append({
        "phase_num": phase_num,
        "phase_title": phase_title,
        "findings": findings,
    })

    total = len(_session_findings[session_id])
    return f"Phase {phase_num} ({phase_title}) の評価結果を記録しました。累計 {total} フェーズ完了。"


@tool
def generate_threat_report(
    session_id: str = "default",
    output_format: str = "markdown",
) -> str:
    """
    記録された全フェーズの評価結果を集約して最終脅威モデルレポートを生成する。

    record_phase_finding で保存されたデータのみを使用し、
    LLM の記憶には一切依存しない。

    Args:
        session_id: セッション識別子
        output_format: 出力形式（"markdown" または "json"）

    Returns:
        最終脅威モデルレポート
    """
    findings = _get_findings(session_id)

    if not findings:
        return (
            "Error: 評価済みフェーズが見つかりません。"
            "先に run_phase と record_phase_finding を実行してください。"
        )

    sorted_findings = sorted(findings, key=lambda x: x["phase_num"])

    if output_format == "json":
        report = {
            "report_title": "脅威モデリングレポート",
            "guideline": _knowledge_base.get("_meta", {}).get(
                "source", "Custom Threat Knowledge Base"
            ),
            "phases": sorted_findings,
        }
        _clear_session(session_id)
        report_text = json.dumps(report, ensure_ascii=False, indent=2)
        _generated_reports[session_id] = report_text
        return report_text

    # Markdown 形式
    source_label = _knowledge_base.get("_meta", {}).get(
        "source", "Custom Threat Knowledge Base"
    )
    lines = [
        "# 脅威モデリングレポート",
        "",
        f"**参照フレームワーク**: {source_label}",
        "",
        "---",
        "",
    ]

    for phase in sorted_findings:
        lines.append(f"## Phase {phase['phase_num']}: {phase['phase_title']}")
        lines.append("")
        lines.append(phase["findings"])
        lines.append("")
        lines.append("---")
        lines.append("")

    _clear_session(session_id)
    report_text = "\n".join(lines)
    _generated_reports[session_id] = report_text
    return report_text


# --- CLI エントリポイント ---

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OWASP Agentic AI ガイドラインに基づく机上脅威モデリングエージェント",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # システム記述（ファイルまたは文字列、どちらか必須）
    system_group = parser.add_mutually_exclusive_group(required=True)
    system_group.add_argument(
        "--system-file", type=Path, metavar="PATH",
        help=(
            "評価対象システムのアーキテクチャ記述ファイル。\n"
            "  .json    : system_schema.json 形式（visualize_traces.py --export-schema で自動生成）\n"
            "  .txt/.md : テキスト・Markdown\n"
            "  .pdf     : PDF（pdfminer.six が必要）"
        ),
    )
    system_group.add_argument(
        "--system-description", type=str, metavar="TEXT",
        help="評価対象システムのアーキテクチャ記述（文字列で直接指定）",
    )

    # 知識ベース（省略時はデフォルトの OWASP JSON を使用）
    parser.add_argument(
        "--knowledge-base", type=Path, metavar="PATH",
        default=_DEFAULT_KB_PATH,
        help=(
            "脅威知識ベース JSON ファイル "
            f"（デフォルト: {_DEFAULT_KB_PATH.name}）"
        ),
    )

    # 参考ドキュメント（複数指定可）
    parser.add_argument(
        "--reference-doc", type=Path, metavar="PATH",
        action="append", dest="reference_docs", default=[],
        help=(
            "参考ドキュメント（.txt / .md / .pdf）。"
            "オーケストレーターの追加コンテキストとして渡される。複数指定可。"
        ),
    )

    # 出力形式
    parser.add_argument(
        "--output-format", choices=["markdown", "json"], default="markdown",
        help="レポートの出力形式（デフォルト: markdown）",
    )

    # 出力先ファイル
    parser.add_argument(
        "--output-file", type=Path, metavar="PATH",
        help=(
            "レポートの出力先ファイルパス。"
            "省略時は --output-format に応じて "
            "threat_model_report.md または threat_model_report.json を生成する。"
        ),
    )

    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    # --- 知識ベースを読み込んでモジュール変数に反映 ---
    if not args.knowledge_base.exists():
        raise SystemExit(f"Error: 知識ベースファイルが見つかりません: {args.knowledge_base}")

    kb = load_knowledge_base(args.knowledge_base)
    _knowledge_base.clear()
    _knowledge_base.update(kb)
    logging.info("知識ベース読み込み完了: %s (%d 脅威)", args.knowledge_base.name, len(kb))

    # --- システム記述を取得 ---
    if args.system_file:
        if not args.system_file.exists():
            raise SystemExit(f"Error: システム記述ファイルが見つかりません: {args.system_file}")
        system_description = load_document(args.system_file)
        logging.info("システム記述ファイル読み込み完了: %s", args.system_file.name)
    else:
        system_description = args.system_description

    if not system_description.strip():
        raise SystemExit("Error: システム記述が空です。")

    # --- 参考ドキュメントを読み込む ---
    reference_docs: list[tuple[str, str]] = []
    for doc_path in args.reference_docs:
        if not doc_path.exists():
            raise SystemExit(f"Error: 参考ドキュメントが見つかりません: {doc_path}")
        content = load_document(doc_path)
        reference_docs.append((doc_path.name, content))
        logging.info("参考ドキュメント読み込み完了: %s", doc_path.name)

    # --- オーケストレーターを構築・実行 ---
    session_id = str(uuid.uuid4())

    system_prompt = _make_system_prompt(
        system_description=system_description,
        output_format=args.output_format,
        session_id=session_id,
        reference_docs=reference_docs,
    )

    orchestrator = Agent(
        model=BedrockModel(model_id=os.environ.get("AWS_BEDROCK_MODEL_ID")),
        system_prompt=system_prompt,
        tools=[run_phase, record_phase_finding, generate_threat_report],
    )

    logging.info("脅威モデリング開始 (session_id=%s)", session_id)
    orchestrator("Start threat-modeling sop")

    # generate_threat_report() が _generated_reports に保存した純粋なレポートを取り出す。
    # response.message はLLMが前置き/後置きを付加する場合があるため使用しない。
    #
    # NOTE: SOP の tool 呼び出しに session_id が明示されない場合、LLM はデフォルト値
    # "default" を使用する。UUID と "default" の両方を確認する。
    report = _generated_reports.pop(session_id, None) or _generated_reports.pop("default", None)
    if report is None:
        raise SystemExit(
            "Error: レポートが生成されませんでした。"
            "generate_threat_report ツールが呼ばれなかった可能性があります。"
        )

    # --- 出力先を決定 ---
    output_path = args.output_file
    if output_path is None:
        # --output-file 未指定時はデフォルトファイル名を生成
        ext = ".json" if args.output_format == "json" else ".md"
        output_path = Path(f"threat_model_report{ext}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    logging.info("レポートを保存しました: %s", output_path.resolve())


if __name__ == "__main__":
    run(parse_args())
