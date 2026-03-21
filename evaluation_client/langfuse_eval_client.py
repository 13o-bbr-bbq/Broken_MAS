"""
Langfuse Evaluation Client

Langfuse に格納済みのスコア（評価結果）とトレース（会話ログ）を取得するクライアント。
ダッシュボードや外部パイプラインから再利用可能な独立モジュールとして設計。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from langfuse import Langfuse

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_LANGFUSE_HOST = "https://us.cloud.langfuse.com"


@dataclass
class ScoreRecord:
    score_id: str
    trace_id: str
    name: str
    value: float | None
    string_value: str | None
    data_type: str
    created_at: datetime
    comment: str | None = None


@dataclass
class TraceDetail:
    trace_id: str
    timestamp: datetime
    full_input: Any
    full_output: Any

    @property
    def input_preview(self) -> str:
        text = _to_text(self.full_input)
        return text[:120] + "..." if len(text) > 120 else text

    @property
    def output_preview(self) -> str:
        text = _to_text(self.full_output)
        return text[:120] + "..." if len(text) > 120 else text


def _to_text(value: Any) -> str:
    """入力/出力値を表示用テキストに変換する。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # よくある {"prompt": "...", "messages": [...]} などの形式
        for key in ("prompt", "content", "text", "input", "message"):
            if key in value:
                return _to_text(value[key])
        return str(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                role = item.get("role", "")
                content = _to_text(item.get("content", ""))
                parts.append(f"[{role}] {content}" if role else content)
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(value)


class LangfuseEvalClient:
    """Langfuse から評価スコアと会話ログを取得するクライアント。"""

    def __init__(self, host: str | None = None) -> None:
        pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
        sec = os.environ.get("LANGFUSE_SECRET_KEY")
        if not pub:
            raise EnvironmentError("環境変数 LANGFUSE_PUBLIC_KEY が設定されていません。")
        if not sec:
            raise EnvironmentError("環境変数 LANGFUSE_SECRET_KEY が設定されていません。")

        resolved_host = (
            host
            or os.environ.get("LANGFUSE_HOST")
            or DEFAULT_LANGFUSE_HOST
        )
        self._lf = Langfuse(public_key=pub, secret_key=sec, host=resolved_host)

    # ------------------------------------------------------------------
    # スコア取得
    # ------------------------------------------------------------------

    def fetch_scores(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        limit: int = 500,
    ) -> list[ScoreRecord]:
        """Langfuse から評価スコアを一括取得する（ページネーション対応）。

        Args:
            from_dt: 取得開始日時（None の場合は 24 時間前）
            to_dt:   取得終了日時（None の場合は現在時刻）
            limit:   取得上限件数

        Returns:
            ScoreRecord のリスト（created_at 昇順）
        """
        if from_dt is None:
            from_dt = datetime.now(timezone.utc) - timedelta(hours=24)
        if to_dt is None:
            to_dt = datetime.now(timezone.utc)

        logger.debug(
            "fetch_scores 開始: from=%s to=%s limit=%d",
            from_dt.isoformat() if from_dt else None,
            to_dt.isoformat() if to_dt else None,
            limit,
        )
        scores: list[ScoreRecord] = []
        page = 1
        per_page = min(limit, 100)

        while len(scores) < limit:
            try:
                response = self._lf.api.score_v_2.get(
                    page=page,
                    limit=per_page,
                    from_timestamp=from_dt,
                    to_timestamp=to_dt,
                )
            except Exception as exc:
                raise RuntimeError(f"Langfuse スコア取得に失敗しました: {exc}") from exc

            batch = list(response.data)
            logger.debug("fetch_scores page=%d/%d: %d 件", page, response.meta.total_pages, len(batch))
            if not batch:
                break

            for item in batch:
                scores.append(
                    ScoreRecord(
                        score_id=item.id,
                        trace_id=item.trace_id,
                        name=item.name,
                        value=getattr(item, "value", None),
                        string_value=getattr(item, "string_value", None),
                        data_type=str(getattr(item, "data_type", "NUMERIC")),
                        created_at=item.created_at,
                        comment=getattr(item, "comment", None),
                    )
                )

            if page >= response.meta.total_pages:
                break
            page += 1

        scores = scores[:limit]
        scores.sort(key=lambda s: s.created_at)
        logger.info("fetch_scores 完了: %d 件取得", len(scores))
        return scores

    # ------------------------------------------------------------------
    # トレース詳細取得
    # ------------------------------------------------------------------

    def fetch_trace_details(
        self, trace_ids: list[str]
    ) -> dict[str, TraceDetail]:
        """指定した trace_id リストのトレース詳細を取得する。

        Args:
            trace_ids: 取得対象のトレース ID リスト

        Returns:
            {trace_id: TraceDetail} の辞書
        """
        result: dict[str, TraceDetail] = {}
        unique_ids = list(dict.fromkeys(trace_ids))  # 順序を保ちつつ重複除去
        logger.debug("fetch_trace_details 開始: %d 件（重複除去後）", len(unique_ids))

        for tid in unique_ids:
            try:
                trace = self._lf.api.trace.get(tid)
                result[tid] = TraceDetail(
                    trace_id=tid,
                    timestamp=trace.timestamp,
                    full_input=trace.input,
                    full_output=trace.output,
                )
                logger.debug("trace 取得成功: id=%s", tid)
            except Exception as exc:
                # 個別トレース取得失敗はスキップ（ログのみ）
                logger.warning("trace %s の取得に失敗しました: %s", tid, exc)

        logger.info("fetch_trace_details 完了: %d / %d 件取得", len(result), len(unique_ids))
        return result

    # ------------------------------------------------------------------
    # DataFrame 構築
    # ------------------------------------------------------------------

    def build_dataframe(
        self,
        scores: list[ScoreRecord],
        traces: dict[str, TraceDetail],
    ) -> pd.DataFrame:
        """スコアとトレース詳細を結合した DataFrame を返す。

        列:
            timestamp       トレースのタイムスタンプ
            trace_id        トレース ID
            score_name      評価観点名（例: Toxicity, Goal Accuracy）
            score_value     数値スコア（Categorical の場合は None）
            string_value    カテゴリ値（Numeric の場合は None）
            data_type       NUMERIC / CATEGORICAL / BOOLEAN
            comment         スコアコメント
            input_preview   入力プロンプトの先頭 120 文字
            full_input      入力プロンプト全文（表示用テキスト）
            full_output     LLM 回答全文（表示用テキスト）
        """
        rows = []
        for score in scores:
            detail = traces.get(score.trace_id)
            rows.append(
                {
                    "timestamp": detail.timestamp if detail else score.created_at,
                    "trace_id": score.trace_id,
                    "score_name": score.name,
                    "score_value": score.value,
                    "string_value": score.string_value,
                    "data_type": score.data_type,
                    "comment": score.comment or "",
                    "input_preview": detail.input_preview if detail else "",
                    "full_input": _to_text(detail.full_input) if detail else "",
                    "full_output": _to_text(detail.full_output) if detail else "",
                }
            )

        if not rows:
            logger.info("build_dataframe: スコアなし、空 DataFrame を返します")
            return pd.DataFrame(
                columns=[
                    "timestamp", "trace_id", "score_name", "score_value",
                    "string_value", "data_type", "comment",
                    "input_preview", "full_input", "full_output",
                ]
            )

        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        logger.info("build_dataframe 完了: %d 行", len(df))
        return df.sort_values("timestamp")

    # ------------------------------------------------------------------
    # 便利メソッド（ワンライナー取得）
    # ------------------------------------------------------------------

    def get_evaluation_dataframe(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """スコア取得→トレース詳細取得→DataFrame構築 を一括実行する。"""
        scores = self.fetch_scores(from_dt=from_dt, to_dt=to_dt, limit=limit)
        if not scores:
            return self.build_dataframe([], {})

        trace_ids = list({s.trace_id for s in scores})
        traces = self.fetch_trace_details(trace_ids)
        return self.build_dataframe(scores, traces)
