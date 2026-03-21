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
"""

from __future__ import annotations

import base64
import html as _html
import json
import logging
import os
import sys
import tempfile
import time
import threading
import uuid
from pathlib import Path

import boto3
import requests
import streamlit as st

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, _REPO_ROOT)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数・アセット
# ---------------------------------------------------------------------------

_ASSET_DIR = Path(__file__).parent.parent / "assets"


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
            return raw
    if isinstance(raw, dict):
        content = raw.get("content", [])
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                return first.get("text", str(raw))
    return str(raw)


def _chat_html(
    messages: list[dict],
    is_sending: bool,
    user_b64: str,
    robot_b64: str,
    dots_svg: str,
) -> str:
    """全メッセージをアイコン付き吹き出しスタイルの HTML に変換する。

    レイアウト:
        ユーザー  → 右にアイコン・右揃え吹き出し（青緑）
        エージェント → 左にアイコン・左揃え吹き出し（ライトグレー）
    """
    parts: list[str] = []
    for msg in messages:
        safe = _html.escape(str(msg["content"])).replace("\n", "<br>")
        if msg["role"] == "user":
            parts.append(f"""
<div style="display:flex;align-items:flex-end;gap:10px;margin:14px 0;justify-content:flex-end;">
  <div style="background:#0d9488;color:#ffffff;
              padding:12px 16px;border-radius:18px 18px 4px 18px;
              max-width:72%;font-size:14px;line-height:1.65;
              word-break:break-word;white-space:pre-wrap;">
    {safe}
  </div>
  <img src="data:image/png;base64,{user_b64}"
       style="width:40px;height:40px;border-radius:50%;flex-shrink:0;object-fit:cover;">
</div>""")
        else:
            parts.append(f"""
<div style="display:flex;align-items:flex-end;gap:10px;margin:14px 0;">
  <img src="data:image/png;base64,{robot_b64}"
       style="width:40px;height:40px;border-radius:50%;flex-shrink:0;object-fit:cover;">
  <div style="background:#e2e8f0;color:#1e293b;
              padding:12px 16px;border-radius:18px 18px 18px 4px;
              max-width:72%;font-size:14px;line-height:1.65;
              word-break:break-word;white-space:pre-wrap;">
    {safe}
  </div>
</div>""")

    if is_sending:
        parts.append(f"""
<div style="display:flex;align-items:flex-end;gap:10px;margin:14px 0;">
  <img src="data:image/png;base64,{robot_b64}"
       style="width:40px;height:40px;border-radius:50%;flex-shrink:0;object-fit:cover;">
  <div style="background:#e2e8f0;
              padding:10px 16px;border-radius:18px 18px 18px 4px;
              display:flex;align-items:center;">
    {dots_svg}
  </div>
</div>""")

    return "\n".join(parts)


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
            for line in log_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        except Exception:
            pass

    events.sort(key=lambda e: e.get("ts", 0))
    return events


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
    # ポート番号で簡易マッチ
    import re
    m = re.search(r":(\d+)$", url)
    return f"Agent (:{m.group(1)})" if m else url


def _extract_message_text(inp: dict) -> str:
    """A2A ツール入力から送信メッセージのテキストを抽出する。"""
    # よく使われるフィールド名を順に試す
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
            f"{_html.escape(reason[:400])}</div>",
            unsafe_allow_html=True,
        )


def _render_tool_event(ev: dict) -> None:
    """ツール呼び出しイベントを Streamlit に描画する。"""
    tool = ev.get("tool", "")
    # target_url / message_text はオーケストレーター側で抽出済み（部分 JSON からも）
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
            # URL がまだストリーミングされていない（初期の数チャンク）
            st.markdown(f"**🔧 エージェントを呼び出し中...** *(準備中)*")
    else:
        st.markdown(f"**🔧 ツール実行中: `{tool}`**")
        inp = ev.get("input", {})
        if inp:
            st.caption(str(inp)[:200])


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
    # region は Guardrail 用に引き続き使用
) -> None:
    """
    バックグラウンドスレッドで実行する。
    結果は result_box に書き込む（st.session_state への直接書き込みは不可）。
    """
    url = orchestrator_url.rstrip("/") + "/invocations"
    logger.debug("_run_invoke 開始: url=%s timeout=%ds", url, request_timeout)
    try:
        body = json.dumps({"prompt": prompt, "session_id": session_id}, ensure_ascii=False).encode("utf-8")
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
        # result フィールドをそのまま保持（dict または文字列）
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
    st.session_state.chat_session_id = None
if "guardrail_enabled" not in st.session_state:
    st.session_state.guardrail_enabled = False
if "guardrail_id" not in st.session_state:
    st.session_state.guardrail_id = os.environ.get("BEDROCK_GUARDRAIL_ID", "")
if "guardrail_version" not in st.session_state:
    st.session_state.guardrail_version = os.environ.get("BEDROCK_GUARDRAIL_VERSION", "DRAFT")

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
        # 結果をチャット履歴に転記（メインスレッドから書き込む）
        if result_box.get("error"):
            st.session_state.chat_messages.append(
                {
                    "role": "assistant",
                    "content": f"エラーが発生しました:\n{result_box['error']}",
                }
            )
        else:
            # レスポンスから text フィールドを抽出してから保存する
            st.session_state.chat_messages.append(
                {"role": "assistant", "content": _extract_text(result_box["response"])}
            )
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
    st.header("接続設定")

    # ── AWS リージョン（Guardrail 用）────────────────────────────────
    region = st.text_input(
        "AWS リージョン",
        value=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"),
        help="Guardrail を使用する場合に必要なリージョン",
    )

    # ── オーケストレーター URL ──────────────────────────────────────────
    orchestrator_url = st.text_input(
        "オーケストレーター URL",
        value=st.session_state.get("chat_orchestrator_url") or os.environ.get("AWS_A2A_SERVER_ORCHESTRATOR_URL", "http://orchestrator:8080"),
        placeholder="http://orchestrator:8080",
        help="オーケストレーターのエンドポイント URL（末尾の /invocations は自動付与）",
    )
    # 入力値を保持（ページ遷移後も維持）
    st.session_state.chat_orchestrator_url = orchestrator_url

    if not orchestrator_url:
        st.caption("URL が未設定です。")

    st.divider()

    # ── 通信設定 ──────────────────────────────────────────────────────
    request_timeout = st.slider(
        "タイムアウト（秒）",
        min_value=30,
        max_value=600,
        value=300,
        step=30,
        help="オーケストレーターの応答待ち最大時間。エージェントの処理に時間がかかる場合は大きく設定してください。",
    )

    st.divider()

    # ── Guardrail 設定 ────────────────────────────────────────────────
    guardrail_enabled = st.toggle(
        "🛡️ Guardrail を有効にする",
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
        st.caption("INPUT（送信前）と OUTPUT（受信後）の両方を評価します。")

    st.divider()

    # ── 接続テスト ────────────────────────────────────────────────────
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

    # ── 履歴クリア ────────────────────────────────────────────────────
    if st.button("チャット履歴をクリア", use_container_width=True):
        st.session_state.chat_messages = []
        st.session_state.chat_sending = False
        st.session_state.chat_result_box = None
        st.rerun()

    st.divider()
    st.caption(
        "Guardrail を使用する場合は AWS 認証情報が必要です:\n"
        "1. 環境変数 (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`)\n"
        "2. `~/.aws/credentials`"
    )

# ---------------------------------------------------------------------------
# チャット表示エリア
# ---------------------------------------------------------------------------

_user_b64, _robot_b64, _dots_svg = _load_assets()

if st.session_state.chat_messages or st.session_state.chat_sending:
    st.markdown(
        _chat_html(
            st.session_state.chat_messages,
            st.session_state.chat_sending,
            _user_b64,
            _robot_b64,
            _dots_svg,
        ),
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        "<div style='color:#94a3b8;text-align:center;padding:60px 0;font-size:15px;'>"
        "メッセージを入力してチャットを開始してください"
        "</div>",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# 進捗表示（ローカルモード限定）
#
# オーケストレーター URL が localhost / 127.0.0.1 の場合のみ、
# /tmp/mas_progress/{session_id}.jsonl をポーリングして途中経過を表示する。
# ---------------------------------------------------------------------------

_is_local = orchestrator_url.startswith("http://localhost") or orchestrator_url.startswith("http://127.")

if st.session_state.chat_sending and st.session_state.chat_session_id and _is_local:
    events = _get_progress_events(st.session_state.chat_session_id)
    if events:
        with st.expander("🤔 エージェントの思考過程（処理中）", expanded=True):
            for ev in events:
                if ev.get("type") == "tool_call":
                    _render_tool_event(ev)
                elif ev.get("type") == "steering_guide":
                    _render_steering_event(ev)
                elif ev.get("type") == "text":
                    st.markdown(
                        f"<div style='color:#475569;font-size:13px;padding:4px 8px;"
                        f"border-left:3px solid #94a3b8;margin:4px 0;'>"
                        f"{_html.escape(ev.get('content',''))}</div>",
                        unsafe_allow_html=True,
                    )

# ---------------------------------------------------------------------------
# チャット入力
# ---------------------------------------------------------------------------

if prompt := st.chat_input(
    "メッセージを入力してください...",
    disabled=st.session_state.chat_sending,
):
    # ── 入力検証 ──────────────────────────────────────────────────────
    if not orchestrator_url:
        st.error("サイドバーでオーケストレーター URL を設定してください。")
        st.stop()

    # ── INPUT Guardrail チェック（ON の場合のみ）─────────────────────
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

    # ── ユーザーメッセージを履歴に追加 ────────────────────────────────
    st.session_state.chat_messages.append({"role": "user", "content": prompt})

    # ── バックグラウンドスレッドで /invocations を呼び出す ──────────────
    session_id = str(uuid.uuid4())
    result_box: dict = {"done": False, "response": None, "error": None}
    st.session_state.chat_result_box = result_box
    st.session_state.chat_session_id = session_id
    st.session_state.chat_sending = True

    _gid = st.session_state.guardrail_id if st.session_state.guardrail_enabled else ""
    _gver = st.session_state.guardrail_version if st.session_state.guardrail_enabled else ""
    thread = threading.Thread(
        target=_run_invoke,
        args=(orchestrator_url, prompt, region, request_timeout, result_box, session_id, _gid, _gver),
        daemon=True,
    )
    thread.start()

    st.rerun()

# ---------------------------------------------------------------------------
# ポーリング（表示エリアとチャット入力を描画した後に sleep → rerun）
#
# ここまでスクリプトが到達した時点でユーザーメッセージと three-dots が
# すでに画面に送出されているため、ユーザーは待ち状態を即座に確認できる。
# ---------------------------------------------------------------------------

if _poll_needed:
    time.sleep(1)
    st.rerun()
