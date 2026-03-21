"""
Threat Modeling Agent SOP Loader

strands-agents-sops の __init__.py と同じパターンで、
sops/ ディレクトリ以下の *.sop.md ファイルを動的にロードし、
モジュール属性および _with_input ラッパー関数として公開する。

Usage:
    import threat_modeling_agent as tma

    # SOP テキストをそのまま取得
    system_prompt = tma.threat_modeling

    # アーキテクチャ記述を <user-input> に埋め込んだ形式で取得
    system_prompt = tma.threat_modeling_with_input(system_description)
"""

from pathlib import Path

_sops_dir = Path(__file__).parent / "sops"

for _md_file in _sops_dir.glob("*.sop.md"):
    if _md_file.is_file():
        # ファイル名を Python 識別子に変換（例: threat-modeling.sop.md → threat_modeling）
        _attr_name = (
            _md_file.stem.removesuffix(".sop").replace("-", "_").replace(".", "_")
        )
        _sop_name = _md_file.stem.removesuffix(".sop")
        _content = _md_file.read_text(encoding="utf-8")

        # SOP テキストをモジュール属性として登録
        globals()[_attr_name] = _content

        # <agent-sop> XML でラップする _with_input ラッパーを生成
        def _make_wrapper(content: str, name: str):
            def wrapper(user_input: str = "") -> str:
                return f"""<agent-sop name="{name}">
<content>
{content}
</content>
<user-input>
{user_input}
</user-input>
</agent-sop>"""
            return wrapper

        globals()[f"{_attr_name}_with_input"] = _make_wrapper(_content, _sop_name)

del _sops_dir
