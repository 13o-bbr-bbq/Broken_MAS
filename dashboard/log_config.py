"""
ロギング設定モジュール

dashboard/app.py（エントリポイント）から setup_logging() を 1 回だけ呼ぶ。
各モジュールは logging.getLogger(__name__) でロガーを取得する。

ログファイル: <repo_root>/logs/dashboard.log
"""

import logging
from pathlib import Path

_LOG_FILE = Path(__file__).parent.parent / "logs" / "dashboard.log"
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d — %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.DEBUG) -> None:
    """
    ルートロガーにファイルハンドラーを追加する。
    Streamlit は起動時に自分のハンドラーをルートロガーに登録するため
    「handlers が空のときだけ追加」とすると常にスキップされてしまう。
    代わりに「同じパスへの FileHandler が未登録の場合のみ追加」で冪等性を担保する。
    """
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    log_file_abs = str(_LOG_FILE.resolve())
    root = logging.getLogger()

    # 同じファイルへの FileHandler が既に存在する場合はスキップ（2重登録防止）
    for h in root.handlers:
        if isinstance(h, logging.FileHandler) and h.baseFilename == log_file_abs:
            return

    handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
    handler.setLevel(level)

    # Streamlit が WARNING 等に設定している場合でも DEBUG を流せるよう引き下げる
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    root.addHandler(handler)

    # サードパーティの過剰な DEBUG ログを抑制
    for noisy in ("boto3", "botocore", "urllib3", "httpx", "httpcore", "langfuse", "streamlit"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
