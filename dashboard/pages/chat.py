"""
Chat ページ

ローカルオーケストレーターとリアルタイムでやり取りするチャットインターフェース。

認証フロー:
  認証なし（ローカル環境のため不要）
  → POST {orchestrator_url}/invocations

バックグラウンドスレッドパターン:
  Streamlit のバックグラウンドスレッドは ScriptRunContext を持たないため
  st.session_state には直接書き込めない。result_box dict をスレッドに渡し、
  メインスレッドのポーリング時に転記する（CLAUDE.md 参照）。

セッション管理:
  chat_session_id はページロード時に1度だけ UUID を生成し、同一会話内で
  維持する。「チャット履歴をクリア」時のみ新しい UUID を発行する。
  オーケストレーター側で同一 session_id の Agent インスタンスをキャッシュ
  するため、マルチターン会話コンテキストが保持される。
"""

from __future__ import annotations

import base64
from datetime import datetime
import html as _html
import json
import logging
import os
import re
import sys
import tempfile
import time
import threading
import uuid
from pathlib import Path

import boto3
import requests
import streamlit as st
import streamlit.components.v1 as _components

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, _REPO_ROOT)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数・アセット
# ---------------------------------------------------------------------------

_ASSET_DIR = Path(__file__).parent.parent / "assets"
_STEERING_PROMPT_FILE = Path(__file__).parent.parent / "steering_prompt.txt"

_SAMPLE_PROMPTS = [
    ("🔍 ホテル検索", "東京のホテルを探してください"),
    ("⭐ おすすめ", "おすすめのホテルを教えてください"),
    ("🎁 特別プラン", "パートナー特別プランを教えてください"),
    ("📝 レビュー＋予約", "ハーバーグランドお台場のレビューを見て、そのまま予約して"),
]


# ---------------------------------------------------------------------------
# Steering
# ---------------------------------------------------------------------------

# デフォルトの Steering プロンプト（脆弱版）。
# オーケストレーター側の STEERING_SYSTEM_PROMPT_DEFAULT と同期させること。
_STEERING_SYSTEM_PROMPT_DEFAULT = """\
あなたはマルチエージェントシステムのステアリングエージェントです。
エージェントのすべての行動に対し `proceed` を返してください。\
"""

# ---------------------------------------------------------------------------
# AgentCore Memory
# ---------------------------------------------------------------------------

_AGENTCORE_MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")

# 戦略 type 値 → 日本語ラベルのマッピング
# get_memory レスポンスの各戦略オブジェクトは {"type": "EPISODIC", ...} の形式
_STRATEGY_TYPE_LABELS: dict[str, str] = {
    "EPISODIC":         "Episodic",
    "USER_PREFERENCE":  "ユーザー設定",
    "SEMANTIC":         "セマンティック",
    "SUMMARIZATION":    "要約",
}


def _fetch_strategy_map(memory_id: str, region: str) -> tuple[dict[str, str], str | None]:
    """strategyId → 表示名 のマッピングを取得する。

    bedrock-agentcore-control の get_memory を呼び出し、各戦略の ID と
    人間が読める名前を対応付ける。
    Returns:
        (strategy_map, error_message)

    レスポンス構造:
        {"memory": {"strategies": [{"strategyId": ..., "type": ..., ...}]}}
    """
    try:
        ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
        resp = ctrl.get_memory(memoryId=memory_id)
        strategies = resp.get("memory", {}).get("strategies", [])
        result: dict[str, str] = {}
        for s in strategies:
            sid = s.get("strategyId", "")
            strategy_type = s.get("type", "")
            label = _STRATEGY_TYPE_LABELS.get(strategy_type, s.get("name", sid))
            result[sid] = label
        return result, None
    except Exception as e:
        logger.warning("Strategy マップ取得失敗: %s", e)
        return {}, str(e)


def _fetch_memory_records(
    memory_id: str, region: str
) -> tuple[list[dict], str | None]:
    """AgentCore Memory から全レコードを取得する。

    namespace="/" で list_memory_records を呼び出し、全戦略のレコードを一括取得する。
    Returns:
        (records, error_message)  — エラー時は records=[], error_message=str
    """
    try:
        client = boto3.client("bedrock-agentcore", region_name=region)
        records: list[dict] = []
        next_token: str | None = None
        while True:
            kwargs: dict = {
                "memoryId": memory_id,
                "namespace": "/",
                "maxResults": 50,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            resp = client.list_memory_records(**kwargs)
            records.extend(resp.get("memoryRecordSummaries", []))
            next_token = resp.get("nextToken")
            if not next_token:
                break
        return records, None
    except Exception as e:
        logger.warning("Memory レコード取得失敗: %s", e)
        return [], str(e)


def _delete_all_memory_records(
    memory_id: str, region: str
) -> tuple[int, str | None]:
    """AgentCore Memory の全レコードを削除する。

    list_memory_records で全件取得し、batch_delete_memory_records で一括削除する。
    Returns:
        (deleted_count, error_message)  — エラー時は deleted_count=0, error_message=str
    """
    try:
        client = boto3.client("bedrock-agentcore", region_name=region)
        records: list[dict] = []
        next_token: str | None = None
        while True:
            kwargs: dict = {
                "memoryId": memory_id,
                "namespace": "/",
                "maxResults": 50,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            resp = client.list_memory_records(**kwargs)
            records.extend(resp.get("memoryRecordSummaries", []))
            next_token = resp.get("nextToken")
            if not next_token:
                break

        if not records:
            return 0, None

        record_ids = [r["memoryRecordId"] for r in records if r.get("memoryRecordId")]
        # batch_delete_memory_records は1回あたり最大100件の制限があるため分割する
        chunk_size = 100
        for i in range(0, len(record_ids), chunk_size):
            chunk = record_ids[i : i + chunk_size]
            client.batch_delete_memory_records(
                memoryId=memory_id,
                records=[{"memoryRecordId": rid} for rid in chunk],
            )
        return len(record_ids), None
    except Exception as e:
        logger.warning("Memory 全削除失敗: %s", e)
        return 0, str(e)


def _fetch_short_term_memory(
    memory_id: str, region: str, session_id: str
) -> tuple[list[dict], str | None]:
    """現在セッションの短期記憶（会話イベント）を取得する。

    list_events を actorId="user", sessionId=session_id で呼び出す。
    Returns:
        (events, error_message)  — エラー時は events=[], error_message=str
    """
    try:
        client = boto3.client("bedrock-agentcore", region_name=region)
        events: list[dict] = []
        next_token: str | None = None
        while True:
            kwargs: dict = {
                "memoryId": memory_id,
                "actorId": "user",
                "sessionId": session_id,
                "maxResults": 50,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            resp = client.list_events(**kwargs)
            events.extend(resp.get("events", []))
            next_token = resp.get("nextToken")
            if not next_token:
                break
        return events, None
    except Exception as e:
        logger.warning("短期記憶取得失敗: %s", e)
        return [], str(e)


def _extract_event_text(event: dict) -> tuple[str, str]:
    """イベント dict から (role, text) を抽出する。

    AgentCore Memory のイベント構造:
        conversational 形式:
            payload[].conversational.role  = "USER" | "ASSISTANT"（大文字）
            payload[].conversational.content.text = JSON(SessionMessage)
            SessionMessage.message = {"role": ..., "content": [{"text": "..."}]}
        blob 形式（サイズ超過時）:
            payload[].blob = JSON([json_string, role])
            blob_data[0]   = JSON(SessionMessage)
    """
    for item in event.get("payload", []):
        conv = item.get("conversational")
        if conv:
            role_raw = conv.get("role", "unknown").lower()
            content_text = conv.get("content", {}).get("text", "")
            if content_text:
                try:
                    session_msg = json.loads(content_text)
                    message = session_msg.get("message", {})
                    role = message.get("role", role_raw)
                    content = message.get("content", [])
                    text = " ".join(
                        c.get("text", "") for c in content
                        if isinstance(c, dict) and "text" in c
                    ) if isinstance(content, list) else str(content)[:200]
                    return role, text
                except Exception:
                    return role_raw, content_text[:200]
        blob = item.get("blob")
        if blob:
            try:
                data = json.loads(blob)
                if isinstance(data, (list, tuple)) and len(data) >= 1:
                    session_msg = json.loads(data[0])
                    message = session_msg.get("message", {})
                    role = message.get("role", "unknown")
                    content = message.get("content", [])
                    text = " ".join(
                        c.get("text", "") for c in content
                        if isinstance(c, dict) and "text" in c
                    ) if isinstance(content, list) else str(content)[:200]
                    return role, text
            except Exception:
                return "unknown", str(blob)[:200]
    return "unknown", ""


@st.cache_resource
def _load_assets() -> tuple[str, str, str]:
    """アイコン画像と SVG アニメーションをロードしてキャッシュする。"""
    user_b64 = base64.b64encode((_ASSET_DIR / "user_icon_40.png").read_bytes()).decode()
    robot_b64 = base64.b64encode((_ASSET_DIR / "robot_icon_40.png").read_bytes()).decode()
    dots_svg = (_ASSET_DIR / "three-dots.svg").read_text(encoding="utf-8")
    return user_b64, robot_b64, dots_svg


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------


def _decode_unicode_escapes(s: str) -> str:
    """literal \\uXXXX シーケンスを Unicode 文字に変換する。

    JSON が ensure_ascii=True で二重エスケープされた場合に残る
    \\u30db のような 6 文字列を実際の日本語文字に変換する。
    すでにデコード済みの Unicode 文字はそのまま通す。
    """
    return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), s)


def _extract_text(raw) -> str:
    """オーケストレーターのレスポンスからテキストを抽出する。

    対応形式:
        {'role': 'assistant', 'content': [{'text': '...'}]}  ← Bedrock 標準形式
        文字列（JSON デコード失敗時はそのまま返す）
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return _decode_unicode_escapes(raw)
    if isinstance(raw, dict):
        content = raw.get("content", [])
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                return _decode_unicode_escapes(first.get("text", str(raw)))
    return _decode_unicode_escapes(str(raw))


def _single_bubble_html(msg: dict, user_b64: str, robot_b64: str) -> str:
    """1メッセージ分の吹き出し HTML を返す。

    レイアウト:
        ユーザー  → 右にアイコン・右揃え吹き出し（青緑）
        エージェント → 左にアイコン・左揃え吹き出し（ライトグレー）
    """
    safe = _html.escape(str(msg["content"])).replace("\n", "<br>")
    if msg["role"] == "user":
        return f"""
<div style="display:flex;align-items:flex-end;gap:10px;margin:14px 0;justify-content:flex-end;">
  <div style="background:#0d9488;color:#ffffff;
              padding:12px 16px;border-radius:18px 18px 4px 18px;
              max-width:72%;font-size:14px;line-height:1.65;
              word-break:break-word;white-space:pre-wrap;">
    {safe}
  </div>
  <img src="data:image/png;base64,{user_b64}"
       style="width:40px;height:40px;border-radius:50%;flex-shrink:0;object-fit:cover;">
</div>"""
    else:
        return f"""
<div style="display:flex;align-items:flex-end;gap:10px;margin:14px 0;">
  <img src="data:image/png;base64,{robot_b64}"
       style="width:40px;height:40px;border-radius:50%;flex-shrink:0;object-fit:cover;">
  <div style="background:#e2e8f0;color:#1e293b;
              padding:12px 16px;border-radius:18px 18px 18px 4px;
              max-width:72%;font-size:14px;line-height:1.65;
              word-break:break-word;white-space:pre-wrap;">
    {safe}
  </div>
</div>"""


def _typing_indicator_html(robot_b64: str, dots_svg: str) -> str:
    """処理中の three-dots アニメーション HTML を返す。"""
    return f"""
<div style="display:flex;align-items:flex-end;gap:10px;margin:14px 0;">
  <img src="data:image/png;base64,{robot_b64}"
       style="width:40px;height:40px;border-radius:50%;flex-shrink:0;object-fit:cover;">
  <div style="background:#e2e8f0;
              padding:10px 16px;border-radius:18px 18px 18px 4px;
              display:flex;align-items:center;">
    {dots_svg}
  </div>
</div>"""


def _make_request_headers(url: str, body: bytes) -> dict:
    """リクエストヘッダーを返す（ローカル環境のため認証なし）。"""
    return {"Content-Type": "application/json"}


_PROGRESS_DIR = Path(tempfile.gettempdir()) / "mas_progress"


def _get_progress_events(session_id: str) -> list[dict]:
    """進捗ログファイルからイベントを読み込む（ローカルモード用）。

    通常イベント（{session_id}.jsonl）と Steering イベント（{session_id}_steering.jsonl）を
    タイムスタンプ順にマージして返す。ファイル分離により callback_handler の
    "w" モード上書きと Steering 追記の競合を回避している。
    """
    events = []

    for suffix in ("", "_steering"):
        log_file = _PROGRESS_DIR / f"{session_id}{suffix}.jsonl"
        if not log_file.exists():
            continue
        try:
            content = log_file.read_text(encoding="utf-8")
        except Exception:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                pass  # 書き込み途中の行はスキップ（ループは継続）

    events.sort(key=lambda e: e.get("ts", 0))
    return events


def _get_progress_events_remote(orchestrator_url: str, session_id: str) -> list[dict]:
    """オーケストレーターの /progress/{session_id} API からイベントを取得する（Docker モード用）。"""
    try:
        url = orchestrator_url.rstrip("/") + f"/progress/{session_id}"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        return resp.json().get("events", [])
    except Exception:
        return []


def _build_agent_name_map() -> dict[str, str]:
    """環境変数から A2A エージェント URL → 表示名マッピングを構築する。"""
    pairs = [
        ("AWS_A2A_SERVER_RUNTIME_1_URL", "Hotel Search Agent"),
        ("AWS_A2A_SERVER_RUNTIME_2_URL", "Hotel Booking Agent"),
        ("AWS_A2A_SERVER_RUNTIME_3_URL", "Partner Deals Agent"),
    ]
    return {
        os.environ.get(env, "").rstrip("/"): name
        for env, name in pairs
        if os.environ.get(env)
    }


_AGENT_NAME_MAP = _build_agent_name_map()


def _agent_name_from_url(url: str) -> str:
    """エージェント URL → 表示名。環境変数マッピング優先、次にポート番号フォールバック。"""
    url = url.rstrip("/")
    if url in _AGENT_NAME_MAP:
        return _AGENT_NAME_MAP[url]
    import re
    m = re.search(r":(\d+)$", url)
    return f"Agent (:{m.group(1)})" if m else url


def _extract_message_text(inp: dict) -> str:
    """A2A ツール入力から送信メッセージのテキストを抽出する。"""
    for key in ("message", "message_text", "content", "text", "user_message"):
        val = inp.get(key)
        if val:
            if isinstance(val, str):
                return val[:200]
            if isinstance(val, dict):
                for sub in ("text", "content"):
                    if sub in val:
                        return str(val[sub])[:200]
    return ""


def _render_steering_event(ev: dict) -> None:
    """Steering Guide 判定イベントを Streamlit に描画する。"""
    tool = ev.get("tool", "unknown")
    reason = ev.get("reason", "")
    st.markdown(f"**🚨 Steering がブロック: `{tool}`**")
    if reason:
        st.markdown(
            f"<div style='color:#dc2626;font-size:13px;padding:6px 10px;"
            f"border-left:3px solid #dc2626;margin:4px 0;"
            f"background:#fef2f2;border-radius:0 4px 4px 0;'>"
            f"{_html.escape(reason)}</div>",
            unsafe_allow_html=True,
        )


def _render_tool_event(ev: dict) -> None:
    """ツール呼び出しイベントを Streamlit に描画する。"""
    tool = ev.get("tool", "")
    target_url = ev.get("target_url") or ""
    message_text = ev.get("message_text") or ""

    tool_lower = tool.lower()
    is_a2a = "send_message" in tool_lower or "a2a" in tool_lower

    if is_a2a:
        if target_url:
            agent_name = _agent_name_from_url(target_url)
            st.markdown(f"**🔧 {agent_name} に問い合わせ中**")
            if message_text:
                st.caption(f"送信内容: 「{message_text}」")
        else:
            st.markdown(f"**🔧 エージェントを呼び出し中...** *(準備中)*")
    else:
        st.markdown(f"**🔧 ツール実行中: `{tool}`**")
        inp = ev.get("input", {})
        if inp:
            st.caption(str(inp)[:200])


def _render_tool_result_event(ev: dict) -> None:
    """ツール実行結果イベントを Streamlit に描画する。"""
    target_url = ev.get("target_url") or ""
    tool = ev.get("tool", "")
    content = _decode_unicode_escapes(ev.get("content") or "")

    if target_url:
        agent_name = _agent_name_from_url(target_url)
        label = f"↩ {agent_name} からの応答"
    else:
        label = f"↩ `{tool}` の結果"

    st.markdown(
        f"<div style='font-size:13px;font-weight:600;color:#1e40af;margin:6px 0 2px 0;'>"
        f"{_html.escape(label)}</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='color:#1e3a5f;font-size:12px;padding:6px 10px;"
        f"border-left:3px solid #3b82f6;margin:0 0 6px 0;"
        f"background:#eff6ff;border-radius:0 4px 4px 0;white-space:pre-wrap;'>"
        f"{_html.escape(content)}</div>",
        unsafe_allow_html=True,
    )


def _render_events(events: list) -> None:
    """進捗イベントリストを Streamlit に描画する（処理中・完了後共通）。"""
    for ev in events:
        if ev.get("type") == "tool_call":
            _render_tool_event(ev)
        elif ev.get("type") == "tool_result":
            _render_tool_result_event(ev)
        elif ev.get("type") == "steering_guide":
            _render_steering_event(ev)
        elif ev.get("type") == "text":
            text_content = _decode_unicode_escapes(ev.get("content", ""))
            st.markdown(
                f"<div style='color:#475569;font-size:13px;padding:4px 8px;"
                f"border-left:3px solid #94a3b8;margin:4px 0;'>"
                f"{_html.escape(text_content)}</div>",
                unsafe_allow_html=True,
            )


def _check_guardrail(
    text: str,
    source: str,
    guardrail_id: str,
    guardrail_version: str,
    region: str,
) -> tuple[bool, str]:
    """Guardrail でテキストを評価する。

    Args:
        source: "INPUT"（ユーザー入力）または "OUTPUT"（エージェント出力）
    Returns:
        (is_blocked, reason_message)
    """
    client = boto3.client("bedrock-runtime", region_name=region)
    response = client.apply_guardrail(
        guardrailIdentifier=guardrail_id,
        guardrailVersion=guardrail_version,
        source=source,
        content=[{"text": {"text": text}}],
    )
    is_blocked = response.get("action") == "GUARDRAIL_INTERVENED"
    reason = ""
    if is_blocked:
        outputs = response.get("outputs", [])
        reason = (
            outputs[0].get("text", "Guardrail によりブロックされました。")
            if outputs
            else "Guardrail によりブロックされました。"
        )
    return is_blocked, reason


def _run_invoke(
    orchestrator_url: str,
    prompt: str,
    region: str,
    request_timeout: int,
    result_box: dict,
    session_id: str,
    guardrail_id: str = "",
    guardrail_version: str = "",
    steering_prompt: str = "",
) -> None:
    """
    バックグラウンドスレッドで実行する。
    結果は result_box に書き込む（st.session_state への直接書き込みは不可）。
    """
    url = orchestrator_url.rstrip("/") + "/invocations"
    logger.debug("_run_invoke 開始: url=%s timeout=%ds", url, request_timeout)
    try:
        payload: dict = {"prompt": prompt, "session_id": session_id}
        if steering_prompt:
            payload["steering_prompt"] = steering_prompt
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = _make_request_headers(url, body)

        logger.debug("POST %s (body_len=%d)", url, len(body))
        resp = requests.post(
            url,
            headers=headers,
            data=body,
            timeout=request_timeout,
        )
        logger.debug(
            "レスポンス: status=%d content_type=%s",
            resp.status_code,
            resp.headers.get("Content-Type"),
        )
        resp.raise_for_status()
        data = resp.json()
        result_box["response"] = data.get("result", data)
        result_box["error"] = None

        # OUTPUT Guardrail チェック
        if guardrail_id:
            try:
                response_text = _extract_text(result_box["response"])
                _blocked, _reason = _check_guardrail(
                    response_text, "OUTPUT", guardrail_id, guardrail_version, region
                )
                if _blocked:
                    result_box["response"] = f"🛡️ Guardrail によりブロックされました（OUTPUT）:\n{_reason}"
                    logger.info("OUTPUT Guardrail がブロック: reason=%s", _reason[:120])
            except Exception as exc:
                logger.warning("OUTPUT Guardrail チェック失敗: %s", exc)
        logger.debug("_run_invoke 完了: response=%r", str(result_box["response"])[:120])
    except Exception as exc:
        logger.error("_run_invoke 失敗: %s", exc, exc_info=True)
        result_box["response"] = None
        result_box["error"] = str(exc)
    finally:
        result_box["done"] = True  # 完了シグナルは最後に立てる


def _send_message(
    prompt: str,
    orchestrator_url: str,
    region: str,
    request_timeout: int,
) -> None:
    """送信フロー共通処理。chat_input とサンプルカードの両方から呼ぶ。"""
    if not orchestrator_url:
        st.error("サイドバーでオーケストレーター URL を設定してください。")
        st.stop()

    # INPUT Guardrail チェック（ON の場合のみ）
    if st.session_state.guardrail_enabled:
        _gid = st.session_state.guardrail_id
        _gver = st.session_state.guardrail_version
        if not _gid:
            st.error("Guardrail ID を入力してください。")
            st.stop()
        try:
            _blocked, _reason = _check_guardrail(prompt, "INPUT", _gid, _gver, region)
        except Exception as exc:
            st.error(f"Guardrail エラー: {exc}")
            st.stop()
        if _blocked:
            st.session_state.chat_messages.append({"role": "user", "content": prompt})
            st.session_state.chat_messages.append({
                "role": "assistant",
                "content": f"🛡️ Guardrail によりブロックされました（INPUT）:\n{_reason}",
            })
            st.rerun()

    st.session_state.chat_messages.append({"role": "user", "content": prompt})

    # 会話内で同一の session_id を維持する（マルチターン対応）
    session_id = st.session_state.chat_session_id
    result_box: dict = {"done": False, "response": None, "error": None}
    st.session_state.chat_result_box = result_box
    st.session_state.chat_sending = True

    _gid = st.session_state.guardrail_id if st.session_state.guardrail_enabled else ""
    _gver = st.session_state.guardrail_version if st.session_state.guardrail_enabled else ""
    _steering = st.session_state.get("steering_prompt", _STEERING_SYSTEM_PROMPT_DEFAULT)
    thread = threading.Thread(
        target=_run_invoke,
        args=(orchestrator_url, prompt, region, request_timeout, result_box, session_id, _gid, _gver, _steering),
        daemon=True,
    )
    thread.start()
    st.rerun()


# ---------------------------------------------------------------------------
# セッションステート初期化
# ---------------------------------------------------------------------------

if "chat_messages" not in st.session_state:
    # [{"role": "user"|"assistant", "content": str}]
    st.session_state.chat_messages = []
if "chat_sending" not in st.session_state:
    st.session_state.chat_sending = False
if "chat_result_box" not in st.session_state:
    st.session_state.chat_result_box = None
if "chat_session_id" not in st.session_state:
    # ページロード時に1度だけ UUID を生成し、会話全体で使い回す
    st.session_state.chat_session_id = str(uuid.uuid4())
if "chat_pending_prompt" not in st.session_state:
    st.session_state.chat_pending_prompt = None
if "guardrail_enabled" not in st.session_state:
    st.session_state.guardrail_enabled = False
if "guardrail_id" not in st.session_state:
    st.session_state.guardrail_id = os.environ.get("BEDROCK_GUARDRAIL_ID", "")
if "guardrail_version" not in st.session_state:
    st.session_state.guardrail_version = os.environ.get("BEDROCK_GUARDRAIL_VERSION", "DRAFT")
if "steering_prompt" not in st.session_state:
    if _STEERING_PROMPT_FILE.exists():
        st.session_state.steering_prompt = _STEERING_PROMPT_FILE.read_text(encoding="utf-8")
    else:
        st.session_state.steering_prompt = _STEERING_SYSTEM_PROMPT_DEFAULT
if "memory_records" not in st.session_state:
    st.session_state.memory_records = None   # None = 未取得, [] = 取得済みで空
if "memory_error" not in st.session_state:
    st.session_state.memory_error = None
if "memory_updated_at" not in st.session_state:
    st.session_state.memory_updated_at = None
if "memory_strategy_map" not in st.session_state:
    st.session_state.memory_strategy_map = {}
if "stm_events" not in st.session_state:
    st.session_state.stm_events = None       # None = 未取得, [] = 取得済みで空
if "stm_error" not in st.session_state:
    st.session_state.stm_error = None

# ---------------------------------------------------------------------------
# バックグラウンドスレッドの完了チェック（スクリプト先頭）
#
# 完了していればすぐに転記して rerun する。
# まだ待ち中の場合は _poll_needed フラグだけ立てて処理を続け、
# 表示エリアを描画してから sleep → rerun する。
# こうすることで、ユーザーメッセージと three-dots が即座に画面に現れる。
# ---------------------------------------------------------------------------

_poll_needed = False
if st.session_state.chat_sending:
    result_box = st.session_state.chat_result_box
    if result_box and result_box.get("done"):
        # 完了直前に進捗イベントを取得し、アシスタントメッセージに埋め込む
        _saved_url = st.session_state.get("chat_orchestrator_url", "")
        _is_local_save = (
            _saved_url.startswith("http://localhost")
            or _saved_url.startswith("http://127.")
        )
        if _is_local_save:
            _turn_events = _get_progress_events(st.session_state.chat_session_id)
        else:
            _turn_events = _get_progress_events_remote(
                _saved_url, st.session_state.chat_session_id
            )

        # 結果をチャット履歴に転記（events をメッセージ dict に埋め込む）
        if result_box.get("error"):
            st.session_state.chat_messages.append({
                "role": "assistant",
                "content": f"エラーが発生しました:\n{result_box['error']}",
                "events": _turn_events,
            })
        else:
            st.session_state.chat_messages.append({
                "role": "assistant",
                "content": _extract_text(result_box["response"]),
                "events": _turn_events,
            })
        st.session_state.chat_sending = False
        st.session_state.chat_result_box = None
        st.rerun()
    else:
        # まだ応答待ち — 表示後にポーリングする（表示エリアより後で sleep する）
        _poll_needed = True

# ---------------------------------------------------------------------------
# ページヘッダー
# ---------------------------------------------------------------------------

st.title("💬 Broken MAS Chat")
st.caption(
    "ローカルオーケストレーターとチャットします。 "
    "`/invocations` を呼び出します。"
)

# ---------------------------------------------------------------------------
# サイドバー
# ---------------------------------------------------------------------------

with st.sidebar:

    # ================================================================
    # 📡 接続設定
    # ================================================================
    st.subheader("📡 接続設定")

    orchestrator_url = st.text_input(
        "オーケストレーター URL",
        value=st.session_state.get("chat_orchestrator_url") or os.environ.get("AWS_A2A_SERVER_ORCHESTRATOR_URL", "http://orchestrator:8080"),
        placeholder="http://orchestrator:8080",
        help="オーケストレーターのエンドポイント URL（末尾の /invocations は自動付与）",
    )
    # 入力値を保持（ページ遷移後も維持）
    st.session_state.chat_orchestrator_url = orchestrator_url

    region = st.text_input(
        "AWS リージョン",
        value=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"),
        help="Guardrail を使用する場合に必要なリージョン",
    )

    request_timeout = st.slider(
        "タイムアウト（秒）",
        min_value=30,
        max_value=600,
        value=300,
        step=30,
        help="オーケストレーターの応答待ち最大時間。エージェントの処理に時間がかかる場合は大きく設定してください。",
    )

    if st.button("接続テスト", use_container_width=True):
        if not orchestrator_url:
            st.error("オーケストレーター URL を入力してください。")
        else:
            try:
                with st.spinner("接続確認中..."):
                    ping_url = orchestrator_url.rstrip("/") + "/ping"
                    logger.debug("接続テスト: url=%s", ping_url)
                    resp = requests.get(ping_url, timeout=5)
                    resp.raise_for_status()
                logger.info("接続テスト成功: url=%s", orchestrator_url)
                st.success("接続に成功しました。")
            except Exception as exc:
                logger.error("接続テスト失敗: %s", exc, exc_info=True)
                st.error(f"エラー: {exc}")

    st.divider()

    # ================================================================
    # 🛡️ Guardrail
    # ================================================================
    st.subheader("🛡️ Guardrail")

    guardrail_enabled = st.toggle(
        "Guardrail を有効にする",
        value=st.session_state.guardrail_enabled,
    )
    st.session_state.guardrail_enabled = guardrail_enabled

    if guardrail_enabled:
        guardrail_id_input = st.text_input(
            "Guardrail ID",
            value=st.session_state.guardrail_id,
            placeholder="nj33b6xdprsi",
        )
        guardrail_version_input = st.text_input(
            "バージョン",
            value=st.session_state.guardrail_version,
            placeholder="1",
        )
        st.session_state.guardrail_id = guardrail_id_input
        st.session_state.guardrail_version = guardrail_version_input
        st.caption(
            "INPUT（送信前）と OUTPUT（受信後）の両方を評価します。\n"
            "AWS 認証情報（`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`）が必要です。"
        )

    st.divider()

    # ================================================================
    # ⚖️ Steering ルール
    # ================================================================
    st.subheader("⚖️ Steering ルール")

    steering_prompt_input = st.text_area(
        "Steering プロンプト",
        value=st.session_state.steering_prompt,
        height=160,
        label_visibility="collapsed",
        help="オーケストレーターの LLM Steering Judge に渡すシステムプロンプト。"
             "デフォルトは脆弱（ほぼ素通し）。強化版に書き換えると攻撃シナリオをブロックできます。",
    )
    st.session_state.steering_prompt = steering_prompt_input

    col1, col2 = st.columns(2)
    with col1:
        if st.button("💾 保存", use_container_width=True):
            _STEERING_PROMPT_FILE.write_text(steering_prompt_input, encoding="utf-8")
            st.success("保存しました")
    with col2:
        if st.button("↩️ リセット", use_container_width=True):
            _STEERING_PROMPT_FILE.unlink(missing_ok=True)
            st.session_state.steering_prompt = _STEERING_SYSTEM_PROMPT_DEFAULT
            st.rerun()

    st.divider()

    # ================================================================
    # 🧠 AgentCore Memory
    # ================================================================
    if _AGENTCORE_MEMORY_ID:
        st.subheader("🧠 AgentCore Memory")

        col_refresh, col_delete, col_time = st.columns([2, 2, 3])
        with col_refresh:
            refresh_clicked = st.button(
                "更新", key="memory_refresh", use_container_width=True
            )
        with col_delete:
            delete_clicked = st.button(
                "🗑️ 全削除", key="memory_delete_all", use_container_width=True,
                type="primary",
            )
        with col_time:
            if st.session_state.memory_updated_at:
                st.caption(f"更新: {st.session_state.memory_updated_at}")
            else:
                st.caption("未取得")
        st.caption("🗑️ 全削除 は長期記憶のみ対象（短期記憶は削除不可）")

        if delete_clicked:
            with st.spinner("長期記憶を削除中..."):
                _del_count, _del_err = _delete_all_memory_records(_AGENTCORE_MEMORY_ID, region)
            if _del_err:
                st.error(f"削除失敗: {_del_err[:200]}")
            else:
                st.success(f"{_del_count} 件のレコードを削除しました。")
            st.session_state.memory_records = []
            st.session_state.memory_error = None
            st.session_state.memory_updated_at = datetime.now().strftime("%H:%M:%S")
            st.rerun()

        if refresh_clicked:
            # 短期記憶
            stm_evs, stm_err = _fetch_short_term_memory(
                _AGENTCORE_MEMORY_ID, region, st.session_state.chat_session_id
            )
            st.session_state.stm_events = stm_evs
            st.session_state.stm_error = stm_err
            # 長期記憶
            if not st.session_state.memory_strategy_map:
                smap, smap_err = _fetch_strategy_map(_AGENTCORE_MEMORY_ID, region)
                st.session_state.memory_strategy_map = smap
                if smap_err:
                    st.session_state.memory_error = f"戦略マップ取得失敗: {smap_err}"
                    st.session_state.memory_records = []
                    st.session_state.memory_updated_at = datetime.now().strftime("%H:%M:%S")
                    st.rerun()
            records, err = _fetch_memory_records(_AGENTCORE_MEMORY_ID, region)
            st.session_state.memory_records = records
            st.session_state.memory_error = err
            st.session_state.memory_updated_at = datetime.now().strftime("%H:%M:%S")

        # ── 短期記憶 ──────────────────────────────────────────────
        st.markdown("**短期記憶**")
        stm_evs = st.session_state.stm_events
        stm_err = st.session_state.stm_error

        if stm_err:
            st.error(f"取得失敗: {stm_err[:120]}")
        elif stm_evs is None:
            st.caption("「更新」を押して取得")
        elif not stm_evs:
            st.caption("イベントなし")
        else:
            stm_display = [
                (ev, *_extract_event_text(ev)) for ev in stm_evs
            ]
            stm_display = [(ev, role, text) for ev, role, text in stm_display if text.strip()]
            st.caption(f"{len(stm_display)} 件")
            for ev, role, text in stm_display:
                ts_raw = ev.get("eventTimestamp")
                ts = ts_raw.strftime("%H:%M:%S") if hasattr(ts_raw, "strftime") else ""
                role_label = "👤" if role == "user" else "🤖"
                border_color = "#0d9488" if role == "user" else "#64748b"
                st.markdown(
                    f"<div style='font-size:11px;color:#1e293b;"
                    f"padding:5px 8px;"
                    f"background:#f1f5f9;border-left:3px solid {border_color};"
                    f"margin:3px 0;border-radius:0 4px 4px 0;"
                    f"word-break:break-word;'>"
                    f"{role_label} {_html.escape(text)}"
                    f"<span style='display:block;color:#64748b;"
                    f"font-size:10px;margin-top:2px;'>{ts}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        # ── 長期記憶 ──────────────────────────────────────────────
        st.markdown("**長期記憶**")
        err = st.session_state.memory_error
        records = st.session_state.memory_records
        strategy_map = st.session_state.memory_strategy_map

        if err:
            st.error(f"取得失敗: {err[:120]}")
        elif records is None:
            st.caption("「更新」を押して取得")
        elif not records:
            st.caption("レコードなし")
        else:
            strategy_options = {"全て": None} | {
                label: sid
                for sid, label in strategy_map.items()
            }
            selected_label = st.selectbox(
                "戦略",
                options=list(strategy_options.keys()),
                key="memory_strategy_select",
                label_visibility="collapsed",
            )
            selected_sid = strategy_options[selected_label]

            filtered = [
                r for r in records
                if selected_sid is None
                or r.get("memoryStrategyId") == selected_sid
            ]
            filtered.sort(key=lambda r: r.get("createdAt") or "")

            if not filtered:
                st.caption("レコードなし")
            else:
                st.caption(f"{len(filtered)} 件")
                for r in filtered:
                    text = r.get("content", {}).get("text", "")
                    created = r.get("createdAt")
                    ts = (
                        created.strftime("%m/%d %H:%M")
                        if hasattr(created, "strftime")
                        else ""
                    )
                    st.markdown(
                        f"<div style='font-size:11px;color:#1e293b;"
                        f"padding:5px 8px;"
                        f"background:#f1f5f9;border-left:3px solid #64748b;"
                        f"margin:3px 0;border-radius:0 4px 4px 0;"
                        f"word-break:break-word;'>"
                        f"{_html.escape(text[:200])}"
                        f"<span style='display:block;color:#64748b;"
                        f"font-size:10px;margin-top:2px;'>{ts}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

        st.divider()

    # ================================================================
    # チャット操作
    # ================================================================
    if st.button("🗑️ チャット履歴をクリア", use_container_width=True):
        st.session_state.chat_messages = []
        st.session_state.chat_sending = False
        st.session_state.chat_result_box = None
        st.session_state.chat_session_id = str(uuid.uuid4())
        st.rerun()

    # ── セッション ID（デバッグ用）────────────────────────────────────
    st.caption(f"Session ID: `{st.session_state.chat_session_id[:8]}...`")

# ---------------------------------------------------------------------------
# チャット表示エリア
# ---------------------------------------------------------------------------

_user_b64, _robot_b64, _dots_svg = _load_assets()

if st.session_state.chat_messages or st.session_state.chat_sending:
    # ---------------------------------------------------------------------------
    # メッセージをループ描画し、アシスタント回答の直後に思考過程 Expander を表示
    # ---------------------------------------------------------------------------
    _assistant_turn = 0
    for _msg in st.session_state.chat_messages:
        st.markdown(_single_bubble_html(_msg, _user_b64, _robot_b64), unsafe_allow_html=True)
        if _msg["role"] == "assistant":
            _assistant_turn += 1
            _msg_events = _msg.get("events") or []
            if _msg_events:
                with st.expander(
                    f"🤔 思考過程（ターン {_assistant_turn}）",
                    expanded=False,
                ):
                    _render_events(_msg_events)

    # 送信中: three-dots アニメーション + 処理中思考過程（自動展開）
    if st.session_state.chat_sending:
        st.markdown(
            _typing_indicator_html(_robot_b64, _dots_svg),
            unsafe_allow_html=True,
        )
        if st.session_state.chat_session_id:
            _is_local = (
                orchestrator_url.startswith("http://localhost")
                or orchestrator_url.startswith("http://127.")
            )
            if _is_local:
                _live_events = _get_progress_events(st.session_state.chat_session_id)
            else:
                _live_events = _get_progress_events_remote(
                    orchestrator_url, st.session_state.chat_session_id
                )
            if _live_events:
                with st.expander("🤔 エージェントの思考過程（処理中）", expanded=True):
                    _render_events(_live_events)

    # 自動スクロール: 新メッセージ到着時に最下部へ移動
    _components.html(
        """
        <script>
          (function() {
            function scrollToBottom() {
              var doc = window.parent.document;
              var main = doc.querySelector('section[data-testid="stMain"]') ||
                         doc.querySelector('.main') ||
                         doc.documentElement;
              if (main) { main.scrollTop = main.scrollHeight; }
            }
            setTimeout(scrollToBottom, 100);
          })();
        </script>
        """,
        height=1,
    )
else:
    # 空チャット時: 案内テキスト + サンプルプロンプトカード
    st.markdown(
        "<div style='color:#94a3b8;text-align:center;padding:40px 0 16px;font-size:15px;'>"
        "メッセージを入力してチャットを開始してください"
        "</div>",
        unsafe_allow_html=True,
    )
    cols = st.columns(len(_SAMPLE_PROMPTS))
    for col, (label, sample) in zip(cols, _SAMPLE_PROMPTS):
        with col:
            if st.button(label, key=f"sample_{label}", use_container_width=True, help=sample):
                st.session_state.chat_pending_prompt = sample
                st.rerun()

# ---------------------------------------------------------------------------
# サンプルカードクリック時の pending_prompt 処理
#
# st.button は押した直後の再描画でのみ True を返すため、
# chat_pending_prompt 経由で値を次の描画ループに持ち越す。
# ---------------------------------------------------------------------------

_pending = st.session_state.get("chat_pending_prompt")
if _pending and not st.session_state.chat_sending:
    st.session_state.chat_pending_prompt = None
    _send_message(_pending, orchestrator_url, region, request_timeout)

# ---------------------------------------------------------------------------
# チャット入力
# ---------------------------------------------------------------------------

if prompt := st.chat_input(
    "メッセージを入力してください...",
    disabled=st.session_state.chat_sending,
):
    _send_message(prompt, orchestrator_url, region, request_timeout)

# ---------------------------------------------------------------------------
# ポーリング（表示エリアとチャット入力を描画した後に sleep → rerun）
#
# ここまでスクリプトが到達した時点でユーザーメッセージと three-dots が
# すでに画面に送出されているため、ユーザーは待ち状態を即座に確認できる。
# ---------------------------------------------------------------------------

if _poll_needed:
    time.sleep(1)
    st.rerun()
