"""LLM プロバイダ切り替えファクトリ.

環境変数 ``LLM_PROVIDER`` で対話 LLM のプロバイダを切り替える。
コードを変更せず ``.env`` の設定だけで Bedrock / Ollama を入れ替えられる。

- ``LLM_PROVIDER=bedrock`` (既定): 従来通り ``BedrockModel`` を生成する。
- ``LLM_PROVIDER=ollama``        : ローカル LLM 用の ``OllamaModel`` を生成する。

``role`` でモデルの「格」を選ぶ（既存の 2 モデル構成を踏襲）:

- ``"orchestrator"`` / ``"steering"`` : 高精度モデル（守る側）。
- ``"agent"``                         : 軽量モデル（攻撃を受ける側）。
  未設定の場合は高精度側のモデル ID にフォールバックする。

このファイルは各エージェントの Docker ビルドコンテキスト（``broken_a2a_agent_*`` /
``broken_a2a_orchestrator_1`` など）ごとに同一内容で複製されている。ビルドコンテキストが
ディレクトリ単位のため、ルートの共有モジュールはコンテナに入らないことによる意図的な複製。
"""

from __future__ import annotations

import os


def _provider() -> str:
    """現在の LLM プロバイダ名を小文字で返す（既定: ``bedrock``）。"""
    return os.environ.get("LLM_PROVIDER", "bedrock").strip().lower()


def _resolve_model_id(role: str, primary_env: str, agent_env: str) -> str | None:
    """role に応じてモデル ID を解決する。

    ``role="agent"`` のときは ``agent_env`` → ``primary_env`` の順にフォールバックする。
    それ以外（orchestrator / steering）は ``primary_env`` を使う。
    """
    if role == "agent":
        return os.environ.get(agent_env) or os.environ.get(primary_env)
    return os.environ.get(primary_env)


def make_model(role: str = "agent"):
    """``role`` と ``LLM_PROVIDER`` に応じた Strands Model インスタンスを生成する。

    Args:
        role: ``"orchestrator"`` / ``"steering"`` / ``"agent"`` のいずれか。

    Returns:
        ``strands.models.Model`` のサブクラスインスタンス（``BedrockModel`` または ``OllamaModel``）。
    """
    provider = _provider()

    if provider == "ollama":
        # ローカル LLM。ツール呼び出し対応モデル（llama3.1 / qwen2.5 等）が必要。
        from strands.models.ollama import OllamaModel

        model_id = _resolve_model_id(role, "OLLAMA_MODEL_ID", "OLLAMA_AGENT_MODEL_ID")
        return OllamaModel(
            host=os.environ.get("OLLAMA_HOST", "http://host.docker.internal:11434"),
            model_id=model_id,
        )

    # 既定: Bedrock（従来挙動を完全に維持）。
    from strands.models import BedrockModel

    model_id = _resolve_model_id(role, "AWS_BEDROCK_MODEL_ID", "AWS_BEDROCK_AGENT_MODEL_ID")
    return BedrockModel(model_id=model_id)
