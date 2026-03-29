"""
Threat Modeling ページ

OWASP Agentic AI ガイドラインに基づく机上脅威モデリングをダッシュボードから実行する。

フロー:
  1. スキーマソース選択（Visualization 結果 / JSON アップロード / テキスト直接入力）
  2. ログから取得できなかった null フィールドをユーザーが補足入力
  3. バックグラウンドスレッドで脅威モデリング実行（フェーズ進捗をリアルタイム表示）
  4. 完成したレポートを表示・ダウンロード
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import threading
import time
import uuid

import streamlit as st

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, _REPO_ROOT)

import threat_modeling_agent.threat_modeling_agent as tma
from translations import get_translations

logger = logging.getLogger(__name__)

T = get_translations(st.session_state.get("lang", "日本語"))

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# 各フェーズの名称（進捗表示用）
_PHASE_LABELS: dict[int, str] = {
    0: "アーキテクチャ解析",
    1: "計画・推論 (T6, T7, T8)",
    2: "記憶 (T1, T5)",
    3: "ツール実行 (T2, T4, T11, T17)",
    4: "認証・ID (T3, T9)",
    5: "人間インタラクション (T10, T15)",
    6: "マルチエージェント (T12, T13, T14, T16)",
}

# ログから取得できない補足フィールドの定義
# field_type: "bool" / "text" / "select:opt1,opt2,..."
_SUPPLEMENTAL_SECTIONS: list[dict] = [
    {
        "label": T["tm_section_memory"],
        "section": "memory",
        "fields": [
            ("short_term",    T["tm_field_stm"],           "bool"),
            ("long_term",     T["tm_field_ltm"],           "bool"),
            ("vector_db",     T["tm_field_vector_db"],     "bool"),
            ("shared_memory", T["tm_field_shared_memory"], "bool"),
        ],
    },
    {
        "label": T["tm_section_tools"],
        "section": "tools",
        "fields": [
            ("code_execution",    T["tm_field_code_exec"],  "bool"),
            ("file_access",       T["tm_field_file_access"],"bool"),
            ("email_or_messaging",T["tm_field_messaging"],  "bool"),
            ("database_write",    T["tm_field_db_write"],   "bool"),
        ],
    },
    {
        "label": T["tm_section_auth"],
        "section": "authentication",
        "fields": [
            ("enabled",         T["tm_field_auth_enabled"],  "bool"),
            ("method",          T["tm_field_auth_method"],   "text"),
            ("rbac",            T["tm_field_rbac"],          "bool"),
            ("nhi",             T["tm_field_nhi"],           "bool"),
            ("least_privilege", T["tm_field_least_priv"],   "bool"),
            ("token_rotation",  T["tm_field_token_rotation"],"bool"),
        ],
    },
    {
        "label": T["tm_section_human"],
        "section": "human_interaction",
        "fields": [
            ("hitl",             T["tm_field_hitl"],              "bool"),
            ("user_interaction", T["tm_field_user_interaction"],  "bool"),
            ("interaction_type", T["tm_field_interaction_type"],  "text"),
            ("user_trust_level", T["tm_field_trust_level"], "select:,low,medium,high"),
        ],
    },
    {
        "label": T["tm_section_comms"],
        "section": "communication",
        "fields": [
            ("encryption", T["tm_field_tls"], "bool"),
        ],
    },
    {
        "label": T["tm_section_multi_agent"],
        "section": "multi_agent",
        "fields": [
            ("trust_boundaries", T["tm_field_trust_boundary"], "text"),
        ],
    },
]

_BOOL_OPTIONS = [T["tm_bool_none"], T["tm_bool_yes"], T["tm_bool_no"]]

# ---------------------------------------------------------------------------
# ページ
# ---------------------------------------------------------------------------

st.title(T["tm_title"])
st.caption(T["tm_caption"])

# ---------------------------------------------------------------------------
# セッションステート初期化
# ---------------------------------------------------------------------------

_SS_DEFAULTS = {
    "tm_state":      "idle",    # "idle" | "running" | "completed"
    "tm_session_id": None,
    "tm_thread":     None,
    "tm_report":     None,
    "tm_error":      None,
}
for k, v in _SS_DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------------------------------------------------------------------------
# ヘルパー: スキーマ dict → 補足入力フォーム
# ---------------------------------------------------------------------------

def _render_supplemental_form(schema: dict) -> tuple[dict, bool]:
    """
    スキーマ dict を解析し、null フィールドに対する補足入力ウィジェットを表示する。

    Returns:
        (updated_schema, any_null_rendered)
        - updated_schema: ユーザー入力で null フィールドを補完したコピー
        - any_null_rendered: 補足入力が 1 つ以上あった場合 True
    """
    updated = copy.deepcopy(schema)
    any_null_rendered = False

    for sec_conf in _SUPPLEMENTAL_SECTIONS:
        sec_label = sec_conf["label"]
        sec_key   = sec_conf["section"]
        sec_data  = schema.get(sec_key) or {}

        # このセクションで null のフィールドだけを抽出
        null_fields = [
            (fk, fl, ft)
            for fk, fl, ft in sec_conf["fields"]
            if sec_data.get(fk) is None
        ]
        if not null_fields:
            continue

        any_null_rendered = True
        st.markdown(f"**{sec_label}**")

        # 2 カラムで並べる
        for row_start in range(0, len(null_fields), 2):
            row = null_fields[row_start : row_start + 2]
            cols = st.columns(len(row))
            for col, (fkey, flabel, ftype) in zip(cols, row):
                widget_key = f"tm_field_{sec_key}_{fkey}"
                with col:
                    if ftype == "bool":
                        chosen = st.selectbox(
                            flabel,
                            options=_BOOL_OPTIONS,
                            index=0,
                            key=widget_key,
                        )
                        if chosen == T["tm_bool_yes"]:
                            updated.setdefault(sec_key, {})[fkey] = True
                        elif chosen == T["tm_bool_no"]:
                            updated.setdefault(sec_key, {})[fkey] = False
                        # tm_bool_none → null のまま（LLM が「情報なし」として扱う）

                    elif ftype == "text":
                        val = st.text_input(flabel, value="", key=widget_key)
                        if val.strip():
                            updated.setdefault(sec_key, {})[fkey] = val.strip()

                    elif ftype.startswith("select:"):
                        opts = [o.strip() for o in ftype[7:].split(",")]
                        chosen = st.selectbox(
                            flabel,
                            options=opts,
                            index=0,
                            key=widget_key,
                        )
                        if chosen:
                            updated.setdefault(sec_key, {})[fkey] = chosen

        st.write("")  # セクション間スペース

    return updated, any_null_rendered


def _component_counts(schema: dict) -> tuple[int, int]:
    """スキーマからエージェント数 / MCP サーバー数を数える。"""
    components = schema.get("components") if isinstance(schema.get("components"), dict) else {}

    def _as_list(v):
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, tuple):
            return list(v)
        if isinstance(v, dict):
            return list(v.values())
        return [v]

    agent_count = (
        len(_as_list(components.get("orchestrators")))
        + len(_as_list(components.get("a2a_agents")))
    )
    mcp_count = len(_as_list(components.get("mcp_servers")))
    return agent_count, mcp_count


# ---------------------------------------------------------------------------
# ヘルパー: バックグラウンドスレッド実行関数
# ---------------------------------------------------------------------------

def _run_thread(
    session_id: str,
    system_description: str,
    output_format: str,
    result_box: dict,
) -> None:
    """脅威モデリングをバックグラウンドスレッドで実行する。

    st.session_state への直接書き込みは ScriptRunContext がないため不可。
    代わりに result_box（通常の Python dict）に結果を書き込み、
    メインスレッドのポーリング時に st.session_state へ転記する。
    """
    logger.info("_run_thread 開始: session_id=%s output_format=%s", session_id, output_format)
    try:
        from strands import Agent
        from strands.models import BedrockModel

        # 知識ベースをモジュール変数に反映
        kb = tma.load_knowledge_base(tma._DEFAULT_KB_PATH)
        tma._knowledge_base.clear()
        tma._knowledge_base.update(kb)

        # SOP ラップシステムプロンプトを構築
        system_prompt = tma._make_system_prompt(
            system_description=system_description,
            output_format=output_format,
            session_id=session_id,
            reference_docs=[],
        )

        # オーケストレーターを起動
        orchestrator = Agent(
            model=BedrockModel(model_id=os.environ.get("AWS_BEDROCK_MODEL_ID")),
            system_prompt=system_prompt,
            tools=[tma.run_phase, tma.record_phase_finding, tma.generate_threat_report],
        )
        orchestrator("Start threat-modeling sop")

        # レポートを取り出す（session_id または "default" フォールバック）
        report = (
            tma._generated_reports.pop(session_id, None)
            or tma._generated_reports.pop("default", None)
        )
        result_box["report"] = report or "（レポートが生成されませんでした）"
        result_box["error"]  = None
        logger.info("_run_thread 完了: session_id=%s report_len=%d",
                    session_id, len(result_box["report"]))

    except Exception as exc:
        logger.error("_run_thread 失敗: session_id=%s error=%s", session_id, exc, exc_info=True)
        result_box["report"] = None
        result_box["error"]  = str(exc)

    finally:
        # done フラグを最後に立てる（メインスレッドが完了を検知する）
        result_box["done"] = True


# ===========================================================================
# メインレンダリング — 状態ごとに分岐
# ===========================================================================

current_state = st.session_state.tm_state

# ---------------------------------------------------------------------------
# ── RUNNING: 進捗表示
# ---------------------------------------------------------------------------

if current_state == "running":
    session_id  = st.session_state.tm_session_id
    result_box  = st.session_state.get("tm_result_box", {})
    findings    = tma._session_findings.get(session_id, [])
    completed   = len(findings)
    total       = len(_PHASE_LABELS)  # 7

    st.subheader(T["tm_subheader_running"])
    st.progress(completed / total, text=T["tm_progress_text"].format(completed=completed, total=total))

    for f in findings:
        phase_num   = f.get("phase_num", "?")
        phase_title = f.get("phase_title") or _PHASE_LABELS.get(phase_num, f"Phase {phase_num}")
        st.write(f"✅ Phase {phase_num}: {phase_title}")

    # 未完了フェーズをグレー表示
    completed_nums = {f.get("phase_num") for f in findings}
    for num, label in _PHASE_LABELS.items():
        if num not in completed_nums:
            st.write(f"⏳ Phase {num}: {label}")

    st.caption(T["tm_caption_running"])

    # result_box["done"] が立っていれば完了 → メインスレッドから st.session_state に転記
    if result_box.get("done"):
        st.session_state.tm_report = result_box.get("report")
        st.session_state.tm_error  = result_box.get("error")
        st.session_state.tm_state  = "completed"
        st.rerun()
    else:
        time.sleep(2)
        st.rerun()

# ---------------------------------------------------------------------------
# ── COMPLETED: レポート表示
# ---------------------------------------------------------------------------

elif current_state == "completed":
    if st.session_state.tm_error:
        st.error(T["tm_error_run"].format(e=st.session_state.tm_error))

    if st.session_state.tm_report:
        st.subheader(T["tm_subheader_report"])

        report = st.session_state.tm_report
        output_fmt = st.session_state.get("tm_output_format", "markdown")

        if output_fmt == "json":
            try:
                st.json(json.loads(report))
            except json.JSONDecodeError:
                st.code(report, language="json")
        else:
            st.markdown(report)

        file_ext = ".json" if output_fmt == "json" else ".md"
        st.download_button(
            label=T["tm_btn_download_report"],
            data=report,
            file_name=f"threat_model_report{file_ext}",
            mime="application/json" if output_fmt == "json" else "text/markdown",
        )

    st.divider()
    if st.button(T["tm_btn_rerun"], type="secondary"):
        st.session_state.tm_state  = "idle"
        st.session_state.tm_report = None
        st.session_state.tm_error  = None
        st.rerun()

# ---------------------------------------------------------------------------
# ── IDLE: 入力フォーム
# ---------------------------------------------------------------------------

else:  # idle

    # ── ① スキーマソース選択 ──────────────────────────────────────────────

    st.subheader(T["tm_subheader_schema_source"])

    has_viz_schema = bool(st.session_state.get("viz_schema"))

    source_options = [
        T["tm_option_viz"],
        T["tm_option_upload"],
        T["tm_option_text"],
    ]
    default_idx = 0 if has_viz_schema else 1

    schema_source = st.radio(
        T["tm_subheader_schema_source"],
        options=source_options,
        index=default_idx,
        horizontal=True,
        label_visibility="collapsed",
    )

    schema_dict: dict | None = None
    system_description_text: str | None = None

    if schema_source == T["tm_option_viz"]:
        if not has_viz_schema:
            st.warning(T["tm_warning_no_viz"])
            st.stop()
        schema_dict = copy.deepcopy(st.session_state.viz_schema)
        st.success(T["tm_success_viz_loaded"])

    elif schema_source == T["tm_option_upload"]:
        uploaded = st.file_uploader(
            T["tm_label_upload"],
            type=["json"],
            help=T["tm_help_upload"],
        )
        if not uploaded:
            st.info(T["tm_info_upload_prompt"])
            st.stop()
        try:
            schema_dict = json.load(uploaded)
            st.success(T["tm_success_upload"])
        except json.JSONDecodeError as e:
            st.error(T["tm_error_json_parse"].format(e=e))
            st.stop()

    else:  # テキストで直接記述
        system_description_text = st.text_area(
            T["tm_label_text_input"],
            height=220,
            placeholder=(
                "例:\n"
                "オーケストレーターエージェント（Strands Agents）が A2A プロトコルで\n"
                "レストラン検索エージェントとピザ注文エージェントを呼び出す構成。\n"
                "各エージェントは MCP サーバーを通じてツールを利用する。\n"
                "認証: Cognito JWT / HTTPS 通信 / HITL なし"
            ),
        )
        if not (system_description_text or "").strip():
            st.info(T["tm_info_text_prompt"])
            st.stop()

    # ── ② 補足情報の入力（スキーマ dict がある場合のみ） ─────────────────

    if schema_dict is not None:
        st.divider()
        st.subheader(T["tm_subheader_supplement"])
        st.caption(T["tm_caption_supplement"])

        # 検出済み情報をサマリー表示
        detected_items: list[str] = []
        for sec_conf in _SUPPLEMENTAL_SECTIONS:
            sec_data = schema_dict.get(sec_conf["section"]) or {}
            for fkey, flabel, _ in sec_conf["fields"]:
                val = sec_data.get(fkey)
                if val is not None:
                    display = (
                        T["tm_bool_yes"] if val is True
                        else (T["tm_bool_no"] if val is False else str(val))
                    )
                    detected_items.append(f"- **{flabel}**: {display}")

        if detected_items:
            with st.expander(T["tm_expander_detected"], expanded=False):
                st.markdown("\n".join(detected_items))

        updated_schema, any_null = _render_supplemental_form(schema_dict)

        if not any_null:
            st.success(T["tm_success_all_detected"])

        agent_count, mcp_count = _component_counts(updated_schema)
        c1, c2 = st.columns(2)
        c1.metric(T["tm_metric_agents"], agent_count)
        c2.metric(T["tm_metric_mcp_servers"], mcp_count)
        if agent_count == 0:
            st.warning(T["tm_warning_no_agents"])

        # 最終的なシステム記述を生成
        final_description = tma._system_dict_to_markdown(updated_schema)

    else:
        # テキスト入力の場合はそのまま使用
        final_description = (system_description_text or "").strip()

    # ── ③ 出力形式 ────────────────────────────────────────────────────────

    st.divider()
    st.subheader(T["tm_subheader_output"])
    output_format = st.radio(
        T["tm_label_report_format"],
        options=["markdown", "json"],
        horizontal=True,
        label_visibility="collapsed",
    )

    # ── ④ 実行 ───────────────────────────────────────────────────────────

    st.divider()

    if not os.environ.get("AWS_BEDROCK_MODEL_ID"):
        st.error(T["tm_error_no_model_id"])
        st.stop()

    if not final_description.strip():
        st.warning(T["tm_warning_empty_schema"])
        st.stop()

    if st.button(T["tm_btn_run"], type="primary", use_container_width=False):
        session_id = str(uuid.uuid4())

        # result_box: スレッドが結果を書き込む通常の Python dict
        # バックグラウンドスレッドは ScriptRunContext を持たないため
        # st.session_state への直接書き込みはできない。
        # スレッドは result_box に書き込み、メインスレッドが転記する。
        result_box = {"done": False, "report": None, "error": None}

        st.session_state.tm_state         = "running"
        st.session_state.tm_session_id    = session_id
        st.session_state.tm_report        = None
        st.session_state.tm_error         = None
        st.session_state.tm_output_format = output_format
        st.session_state.tm_result_box    = result_box

        thread = threading.Thread(
            target=_run_thread,
            args=(session_id, final_description, output_format, result_box),
            daemon=True,
        )
        thread.start()
        st.session_state.tm_thread = thread

        st.rerun()
