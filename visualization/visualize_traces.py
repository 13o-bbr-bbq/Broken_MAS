#!/usr/bin/env python3
"""
MAS Topology Visualizer

Langfuse に蓄積された OTEL トレースを取得し、マルチエージェントシステムの
コンポーネントトポロジーをインタラクティブ HTML グラフとして可視化する。

スパン名パターン（Strands Agents tracer.py より）:
  invoke_agent {name}   : エージェント起動境界
  invoke_{name}         : A2A アウトバウンド呼び出し（SpanKind.CLIENT）
  execute_tool {tool}   : ツール呼び出し（MCP または A2A ツール）
  chat                  : LLM 呼び出し
  execute_event_loop_cycle : 内部処理（スキップ）

使用方法:
  export LANGFUSE_PUBLIC_KEY="pk-lf-..."
  export LANGFUSE_SECRET_KEY="sk-lf-..."
  python visualization/visualize_traces.py [options]

オプション:
  --limit N      最新 N 件のトレースを取得（デフォルト: 100）
  --hours N      過去 N 時間分のトレースを取得（--limit と併用可能）
  --output PATH  出力 HTML ファイルパス
  --host URL     Langfuse ホスト URL
  --no-physics   vis.js の物理シミュレーションを無効化
  --verbose      各 observation のデバッグ出力
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# python-dotenv は任意
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from langfuse import Langfuse
except ImportError:
    sys.exit("ERROR: langfuse がインストールされていません。\n  pip install langfuse")

try:
    import networkx as nx
except ImportError:
    sys.exit("ERROR: networkx がインストールされていません。\n  pip install networkx")

try:
    from pyvis.network import Network
except ImportError:
    sys.exit("ERROR: pyvis がインストールされていません。\n  pip install pyvis")


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _label_to_display_name(label: str) -> str:
    """ノードラベルから表示名を抽出する。

    ラベル形式 "Short Label\\n(Full Name)" の場合は括弧内の Full Name を返す。
    括弧がない場合は改行前の先頭部分を返す。

    例:
        "A2A Agent 1\\n(Finding Restaurants Agent)" → "Finding Restaurants Agent"
        "MCP Server 1\\n(Restaurant Search)"        → "Restaurant Search"
        "Orchestrator Agent"                        → "Orchestrator Agent"
    """
    if "\n(" in label and label.endswith(")"):
        return label.split("\n(", 1)[1].rstrip(")")
    return label.split("\n")[0]


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# MCP サーバー名（スパン名の {server}___{tool} 形式の server 部分） → (ノード ID, ノードラベル)
# Strands Agents は type=TOOL のスパンを "{server_name}___{tool_name}" という名前で記録する
SERVER_NAME_TO_MCP_NODE: dict[str, tuple[str, str]] = {
    "broken-mcp-server-1": ("mcp_server_1", "MCP Server 1\n(Restaurant Search)"),
    "broken-mcp-server-2": ("mcp_server_2", "MCP Server 2\n(Restaurant Details)"),
    "broken-mcp-server-3": ("mcp_server_3", "MCP Server 3\n(Pizza Menu)"),
    "broken-mcp-server-4": ("mcp_server_4", "MCP Server 4\n(Pizza Orders)"),
}

# ツール名 → (MCP ノード ID, MCP ノードラベル)（execute_tool 形式のスパン用フォールバック）
TOOL_TO_MCP_NODE: dict[str, tuple[str, str]] = {
    "finding_restaurants":          ("mcp_server_1", "MCP Server 1\n(Restaurant Search)"),
    "finding_michelin_restaurants": ("mcp_server_1", "MCP Server 1\n(Restaurant Search)"),
    "retrieve_restaurant_details":  ("mcp_server_2", "MCP Server 2\n(Restaurant Details)"),
    "retrieve_restaurant_menu":     ("mcp_server_2", "MCP Server 2\n(Restaurant Details)"),
    "getting_pizza_menu":           ("mcp_server_3", "MCP Server 3\n(Pizza Menu)"),
    "ordering_pizza":               ("mcp_server_4", "MCP Server 4\n(Pizza Orders)"),
}

# ---------------------------------------------------------------------------
# SVG アイコン（agentic-radar プロジェクトのデザインを参考）
# ---------------------------------------------------------------------------

def _svg_to_data_url(svg: str) -> str:
    """SVG 文字列を base64 データ URL に変換する。"""
    import base64
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


# エージェントアイコン：青い円形 + ロボットシルエット（agent.svg）
_AGENT_SVG = (
    '<svg width="40" height="40" viewBox="0 0 40 40" fill="none" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<rect width="40" height="40" rx="20" fill="#164EAF"/>'
    '<path d="M20 11.6666V13.3333M17.5 20V27.5M22.5 20V27.5'
    'M14.1667 23.3333L17.5 21.6666M22.5 21.6666L25.8333 23.3333'
    'M17.5 25H22.5M18.3333 16.6666V16.675M21.6667 16.6666V16.675'
    'M15 15C15 14.5579 15.1756 14.134 15.4882 13.8214'
    'C15.8007 13.5089 16.2246 13.3333 16.6667 13.3333H23.3333'
    'C23.7754 13.3333 24.1993 13.5089 24.5118 13.8214'
    'C24.8244 14.134 25 14.5579 25 15V18.3333'
    'C25 18.7753 24.8244 19.1992 24.5118 19.5118'
    'C24.1993 19.8244 23.7754 20 23.3333 20H16.6667'
    'C16.2246 20 15.8007 19.8244 15.4882 19.5118'
    'C15.1756 19.1992 15 18.7753 15 18.3333V15Z" '
    'stroke="white" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>'
    '</svg>'
)

# MCP サーバーアイコン：黄色い角丸矩形 + チェーンリンク（mcp_server.svg）
_MCP_SVG = (
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<rect width="24" height="24" rx="4" fill="#FFDD00"/>'
    '<g clip-path="url(#c)">'
    '<path d="M14.1363 3.01758C14.1531 3.02925 14.1598 3.04904 14.1704 3.05141'
    'C14.5728 3.14116 14.9416 3.30574 15.2787 3.54292'
    'C15.655 3.80761 15.9668 4.13138 16.2018 4.5288'
    'C16.3938 4.85362 16.5297 5.20145 16.5826 5.57269'
    'C16.6201 5.83557 16.6127 6.10502 16.6217 6.37166'
    'C16.6234 6.42174 16.6079 6.4724 16.6014 6.51556'
    'C16.7039 6.51556 16.7977 6.51367 16.8913 6.51597'
    'C17.0658 6.52025 17.2419 6.51369 17.4141 6.53613'
    'C17.6158 6.56241 17.8156 6.60838 18.0134 6.65752'
    'C18.2643 6.71985 18.4932 6.83719 18.7164 6.96531'
    'C19.0309 7.1458 19.2905 7.39318 19.5294 7.65964'
    'C19.7673 7.92515 19.9495 8.23099 20.0663 8.56925'
    'C20.137 8.7741 20.2058 8.98351 20.2385 9.19654'
    'C20.2746 9.43153 20.2936 9.66993 20.2666 9.91267'
    'C20.2368 10.1801 20.1926 10.4413 20.1041 10.6949'
    'C19.9835 11.0402 19.8242 11.3682 19.5745 11.6363'
    'C19.22 12.0169 18.8478 12.3811 18.4817 12.7508'
    'C18.4021 12.8312 18.3143 12.9033 18.2337 12.9828'
    'C17.7172 13.4921 17.202 14.0028 16.6856 14.5122'
    'C16.386 14.8076 16.0849 15.1015 15.7845 15.3961'
    'C15.4821 15.6927 15.1795 15.9893 14.8771 16.286'
    'C14.5746 16.5826 14.2722 16.8793 13.9697 17.1759'
    'C13.6693 17.4705 13.3693 17.7655 13.0681 18.0593'
    'C12.9913 18.1342 12.9088 18.2033 12.8325 18.2786'
    'C12.7268 18.3828 12.728 18.5037 12.8349 18.6092'
    'C13.1928 18.9623 13.5519 19.3142 13.9101 19.667'
    'C14.0149 19.7702 14.1248 19.8692 14.2208 19.9802'
    'C14.387 20.1722 14.3746 20.5389 14.2103 20.7334'
    'C14.0645 20.906 13.8811 20.9904 13.6573 20.9814'
    'C13.4962 20.9749 13.3518 20.9153 13.2365 20.8024'
    'C12.7893 20.3646 12.3459 19.923 11.8989 19.4851'
    'C11.7685 19.3573 11.6586 19.2155 11.5874 19.048'
    'C11.4822 18.8006 11.433 18.5464 11.4686 18.2726'
    'C11.5045 17.9967 11.6034 17.7543 11.7717 17.5368'
    'C11.7875 17.5163 11.8041 17.4962 11.8224 17.4779'
    'C12.0582 17.2414 12.2933 17.0042 12.5309 16.7695'
    'C12.8426 16.4614 13.1563 16.1552 13.4693 15.8484'
    'C13.7426 15.5805 14.0164 15.3132 14.2895 15.0453'
    'C14.6259 14.7153 14.9615 14.3845 15.2982 14.0548'
    'C15.515 13.8424 15.7338 13.632 15.9504 13.4193'
    'C16.3343 13.0425 16.7169 12.6644 17.1005 12.2873'
    'C17.4047 11.9882 17.709 11.6893 18.0143 11.3913'
    'C18.154 11.255 18.3 11.125 18.436 10.9852'
    'C18.7254 10.6878 18.9105 10.3382 18.9618 9.92091'
    'C19.0269 9.39158 18.8929 8.92352 18.5566 8.50886'
    'C18.2712 8.15694 17.9077 7.92924 17.4725 7.82136'
    'C17.2447 7.76492 17.0102 7.76874 16.7752 7.79666'
    'C16.4212 7.83871 16.1146 7.98603 15.8333 8.19457'
    'C15.7662 8.24429 15.7061 8.30409 15.6466 8.36313'
    'C15.397 8.61078 15.1501 8.86103 14.9001 9.10819'
    'C14.5822 9.42252 14.2622 9.73483 13.9431 10.0479'
    'C13.6469 10.3385 13.3506 10.6289 13.0544 10.9194'
    'C12.7415 11.2263 12.4286 11.5333 12.1157 11.8402'
    'C11.8153 12.1348 11.5149 12.4293 11.2145 12.7239'
    'C10.9058 13.0267 10.5964 13.3289 10.2886 13.6326'
    'C10.043 13.8749 9.73211 13.9159 9.45742 13.7689'
    'C9.09264 13.5737 9.02687 13.0867 9.30381 12.7906'
    'C9.45381 12.6303 9.61264 12.4781 9.76915 12.3239'
    'C10.073 12.0245 10.3781 11.7262 10.6826 11.4275'
    'C10.9872 11.1288 11.2917 10.8301 11.5963 10.5314'
    'C11.903 10.2307 12.2097 9.92999 12.5164 9.62927'
    'C12.8168 9.3347 13.1172 9.04016 13.4176 8.74556'
    'C13.7263 8.44275 14.0345 8.1394 14.3439 7.83724'
    'C14.494 7.69063 14.6507 7.55048 14.7965 7.39982'
    'C15.0678 7.11952 15.2395 6.78926 15.3073 6.40021'
    'C15.3986 5.87648 15.268 5.40585 14.9599 4.99221'
    'C14.6797 4.616 14.2998 4.36613 13.8276 4.28434'
    'C13.6669 4.25652 13.5018 4.22579 13.3408 4.23482'
    'C12.939 4.25736 12.5698 4.38636 12.251 4.63985'
    'C12.1854 4.69197 12.1241 4.74987 12.0642 4.80854'
    'C11.7113 5.15417 11.3596 5.50102 11.0071 5.84706'
    'C10.7256 6.12333 10.4435 6.39889 10.1619 6.67502'
    'C9.84064 6.99012 9.51967 7.30556 9.19844 7.62073'
    'C8.89811 7.91539 8.5976 8.20986 8.29723 8.50446'
    'C7.98849 8.80727 7.67982 9.11015 7.37111 9.41298'
    'C7.06867 9.70966 6.76622 10.0063 6.46375 10.303'
    'C6.1571 10.6038 5.85071 10.9048 5.54367 11.2051'
    'C5.32248 11.4215 5.10237 11.639 4.87826 11.8523'
    'C4.5992 12.1179 4.19348 12.1136 3.9366 11.8514'
    'C3.68652 11.5961 3.69371 11.2199 3.93871 10.9685'
    'C4.3018 10.596 4.67811 10.2363 5.0495 9.87188'
    'C5.28289 9.64286 5.51812 9.41571 5.75152 9.18669'
    'C6.12083 8.82431 6.48905 8.46083 6.85817 8.09826'
    'C7.14161 7.81985 7.42584 7.54226 7.70948 7.26405'
    'C8.03494 6.94483 8.36005 6.62524 8.68547 6.30597'
    'C8.97954 6.01748 9.27387 5.72927 9.568 5.44085'
    'C9.87676 5.13806 10.1852 4.8349 10.4942 4.53241'
    'C10.7279 4.30369 10.9577 4.07076 11.1979 3.84903'
    'C11.4031 3.6596 11.6262 3.49244 11.8749 3.36038'
    'C12.1407 3.2193 12.4163 3.10865 12.7117 3.04881'
    'C12.7192 3.0473 12.7248 3.03635 12.726 3.02369'
    'C13.1898 3.01758 13.6589 3.01758 14.1363 3.01758Z" fill="black"/>'
    '<path d="M15.1333 12.4087C14.9466 12.5954 14.7654 12.7787 14.5819 12.9597'
    'C14.2533 13.284 13.9234 13.607 13.5939 13.9304'
    'C13.2916 14.2271 12.9895 14.5241 12.6866 14.8202'
    'C12.4905 15.0118 12.2991 15.209 12.0948 15.3915'
    'C11.7005 15.7435 11.2542 16.0026 10.736 16.1336'
    'C10.49 16.1958 10.2437 16.2288 9.99037 16.239'
    'C9.52785 16.2575 9.08469 16.1757 8.65822 16.0055'
    'C8.35671 15.8852 8.08251 15.7151 7.83207 15.5062'
    'C7.53973 15.2623 7.28999 14.9819 7.10397 14.6504'
    'C6.94326 14.3639 6.81035 14.0628 6.76363 13.7342'
    'C6.73013 13.4985 6.69378 13.2599 6.69627 13.0231'
    'C6.70015 12.6546 6.77928 12.2962 6.91887 11.9524'
    'C7.08792 11.5361 7.3345 11.1744 7.65924 10.8636'
    'C7.86233 10.6692 8.05666 10.4657 8.25678 10.2681'
    'C8.58117 9.94791 8.90686 9.62901 9.23244 9.31'
    'C9.48062 9.06684 9.72992 8.82482 9.97792 8.58148'
    'C10.3388 8.22736 10.6988 7.87229 11.0595 7.51804'
    'C11.3409 7.24177 11.623 6.9663 11.9045 6.69019'
    'C12.232 6.36896 12.557 6.04507 12.8878 5.72733'
    'C12.9955 5.62387 13.109 5.52615 13.2636 5.48683'
    'C13.6549 5.38728 14.0676 5.66502 14.0751 6.084'
    'C14.0786 6.27617 14.0086 6.43332 13.8743 6.56656'
    'C13.7058 6.73365 13.5397 6.90302 13.3707 7.06956'
    'C13.0711 7.36484 12.77 7.6587 12.4697 7.95322'
    'C12.1672 8.24979 11.8648 8.54645 11.5624 8.84304'
    'C11.262 9.13757 10.9616 9.43204 10.6613 9.72658'
    'C10.3567 10.0252 10.0522 10.3239 9.74771 10.6226'
    'C9.4432 10.9212 9.14358 11.2251 8.8324 11.5167'
    'C8.63406 11.7025 8.43754 11.8865 8.28668 12.1155'
    'C8.1325 12.3494 8.04679 12.6048 8.00808 12.8811'
    'C7.97065 13.1482 7.99905 13.4063 8.07847 13.6616'
    'C8.17821 13.9822 8.35467 14.2552 8.60182 14.4796'
    'C8.87649 14.7289 9.20149 14.8899 9.56609 14.95'
    'C9.97648 15.0177 10.3801 14.9689 10.7509 14.7734'
    'C10.9176 14.6854 11.0757 14.5699 11.2141 14.4415'
    'C11.5396 14.1395 11.8506 13.8221 12.1678 13.5113'
    'C12.4202 13.2641 12.6736 13.0178 12.9258 12.7704'
    'C13.2825 12.4204 13.6383 12.0695 13.9949 11.7194'
    'C14.2721 11.4472 14.5502 11.1759 14.8275 10.9039'
    'C15.1591 10.5786 15.4902 10.2526 15.822 9.9274'
    'C16.066 9.6883 16.3115 9.45084 16.5549 9.21119'
    'C16.8998 8.87164 17.4247 8.98565 17.6145 9.34437'
    'C17.7373 9.57647 17.698 9.89897 17.5055 10.089'
    'C17.104 10.4853 16.7022 10.8813 16.2999 11.277'
    'C16.0042 11.5678 15.7081 11.8584 15.4112 12.148'
    'C15.3217 12.2352 15.2289 12.319 15.1333 12.4087Z" fill="black"/>'
    '</g>'
    '<defs><clipPath id="c"><rect width="18" height="18" fill="white" '
    'transform="translate(3 3)"/></clipPath></defs>'
    '</svg>'
)

# LLM / カスタムツールアイコン：紫の破線枠 + レンチ（custom_tool.svg）
_TOOL_SVG = (
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<rect x="0.5" y="0.5" width="23" height="23" rx="3.5" fill="#F5DDFF"/>'
    '<rect x="0.5" y="0.5" width="23" height="23" rx="3.5" stroke="#A738D6" '
    'stroke-dasharray="2 2"/>'
    '<g clip-path="url(#ct)">'
    '<path d="M9.5 10.9999H11V9.49993L9.25 7.74993'
    'C9.80982 7.48256 10.4388 7.39533 11.0502 7.50024'
    'C11.6617 7.60515 12.2255 7.89704 12.6642 8.33571'
    'C13.1029 8.77439 13.3948 9.33828 13.4997 9.94973'
    'C13.6046 10.5612 13.5174 11.1901 13.25 11.7499'
    'L16.25 14.7499C16.4489 14.9488 16.5607 15.2186 16.5607 15.4999'
    'C16.5607 15.7812 16.4489 16.051 16.25 16.2499'
    'C16.0511 16.4488 15.7813 16.5606 15.5 16.5606'
    'C15.2187 16.5606 14.9489 16.4488 14.75 16.2499'
    'L11.75 13.2499C11.1902 13.5173 10.5613 13.6045 9.9498 13.4996'
    'C9.33835 13.3947 8.77447 13.1028 8.33579 12.6641'
    'C7.89711 12.2255 7.60522 11.6616 7.50031 11.0501'
    'C7.39541 10.4387 7.48264 9.80974 7.75 9.24993L9.5 10.9999Z" '
    'stroke="black" stroke-linecap="round" stroke-linejoin="round"/>'
    '</g>'
    '<defs><clipPath id="ct"><rect width="12" height="12" fill="white" '
    'transform="translate(6 6)"/></clipPath></defs>'
    '</svg>'
)

# 不明ノードアイコン：ティール色のひし形 + 歯車（basic.svg）
_BASIC_SVG = (
    '<svg width="29" height="29" viewBox="0 0 29 29" fill="none" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<g clip-path="url(#bs)">'
    '<rect x="14.1421" width="20" height="20" rx="4" '
    'transform="rotate(45 14.1421 0)" fill="#009C92"/>'
    '<g clip-path="url(#bs2)">'
    '<path d="M16.2635 12.0207L17.3241 10.9601M17.1473 14.142L18.6676 14.142'
    'M16.2635 16.2634L17.3241 17.324M14.1421 17.1472V18.6675'
    'M12.0208 16.2634L10.9602 17.324M11.1369 14.142L9.61665 14.142'
    'M12.0208 12.0207L10.9602 10.9601M14.1421 11.1368V9.61655" '
    'stroke="white" stroke-linecap="round" stroke-linejoin="round"/>'
    '</g>'
    '</g>'
    '<defs>'
    '<clipPath id="bs"><rect x="14.1421" width="20" height="20" rx="4" '
    'transform="rotate(45 14.1421 0)" fill="white"/></clipPath>'
    '<clipPath id="bs2"><rect width="12" height="12" fill="white" '
    'transform="translate(14.1421 5.65674) rotate(45)"/></clipPath>'
    '</defs>'
    '</svg>'
)

# ノード種別ごとの pyvis スタイル
# shape="circularImage" でアイコン画像を円形クロップ、
# shape="image" で元のアイコン形状をそのまま表示する
NODE_STYLES: dict[str, dict] = {
    "orchestrator": {
        "shape": "circularImage",
        "image": _svg_to_data_url(_AGENT_SVG),
        "size": 40,
        "border_color": "#FF6B6B",   # 赤いボーダーでオーケストレーターを強調
        "border_width": 4,
    },
    "a2a_agent": {
        "shape": "circularImage",
        "image": _svg_to_data_url(_AGENT_SVG),
        "size": 28,
        "border_color": "#4ECDC4",   # ティール
        "border_width": 2,
    },
    "mcp_server": {
        "shape": "image",
        "image": _svg_to_data_url(_MCP_SVG),
        "size": 24,
        "border_color": "#FFDD00",   # 黄色
        "border_width": 2,
    },
    "llm": {
        "shape": "image",
        "image": _svg_to_data_url(_TOOL_SVG),
        "size": 26,
        "border_color": "#A738D6",   # 紫
        "border_width": 2,
    },
    "unknown": {
        "shape": "image",
        "image": _svg_to_data_url(_BASIC_SVG),
        "size": 16,
        "border_color": "#009C92",   # ティール
        "border_width": 1,
    },
}

# A2A エージェント名 → (ノード ID, ノードラベル)
AGENT_NAME_TO_NODE: dict[str, tuple[str, str]] = {
    "Orchestrator Agent":        ("orchestrator",  "Orchestrator Agent"),
    "Finding Restaurants Agent": ("a2a_agent_1",   "A2A Agent 1\n(Finding Restaurants Agent)"),
    "Ordering Pizza Agent":      ("a2a_agent_2",   "A2A Agent 2\n(Ordering Pizza Agent)"),
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Langfuse OTEL トレースから MAS トポロジーを可視化する"
    )
    parser.add_argument(
        "--limit", type=int, default=100,
        help="取得するトレースの最大件数（デフォルト: 100）"
    )
    parser.add_argument(
        "--hours", type=float, default=None,
        help="過去 N 時間分のトレースを取得（省略時は --limit 件）"
    )
    parser.add_argument(
        "--output",
        default="visualization/output/mas_topology.html",
        help="出力 HTML ファイルパス"
    )
    parser.add_argument(
        "--host",
        default="https://us.cloud.langfuse.com",
        help="Langfuse ホスト URL"
    )
    parser.add_argument(
        "--no-physics", action="store_true",
        help="vis.js の物理シミュレーションを無効化（静的レイアウト）"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="各 observation のデバッグ出力を表示"
    )
    parser.add_argument(
        "--export-schema",
        default=None,
        metavar="PATH",
        help=(
            "システムスキーマ JSON を指定パスに出力する。"
            " threat_modeling_agent の --system-file に直接渡せる形式。"
            " （例: visualization/output/system_schema.json）"
        ),
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Langfuse 接続
# ---------------------------------------------------------------------------

def create_langfuse_client(args: argparse.Namespace) -> "Langfuse":
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sec = os.environ.get("LANGFUSE_SECRET_KEY")
    if not pub:
        sys.exit("ERROR: 環境変数 LANGFUSE_PUBLIC_KEY が設定されていません。")
    if not sec:
        sys.exit("ERROR: 環境変数 LANGFUSE_SECRET_KEY が設定されていません。")
    return Langfuse(public_key=pub, secret_key=sec, host=args.host)


# ---------------------------------------------------------------------------
# トレース取得
# ---------------------------------------------------------------------------

def fetch_traces(lf: "Langfuse", args: argparse.Namespace) -> list:
    """Langfuse からトレース一覧を取得する。ページネーション対応。

    Langfuse v3 では lf.api.trace.list() を使用する。
    ページネーションは page=1, 2, ... で行い meta.total_pages で終端を判定する。
    """
    kwargs: dict = {}
    if args.hours is not None:
        kwargs["from_timestamp"] = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    logger.info("fetch_traces 開始: limit=%d hours=%s", args.limit, args.hours)
    print(f"トレースを取得中 (limit={args.limit}, hours={args.hours}) ...")

    traces = []
    page = 1
    per_page = min(args.limit, 100)

    while len(traces) < args.limit:
        try:
            response = lf.api.trace.list(page=page, limit=per_page, **kwargs)
        except Exception as e:
            logger.error("Langfuse への接続に失敗: %s", e, exc_info=True)
            sys.exit(f"ERROR: Langfuse への接続に失敗しました: {e}")

        batch = list(response.data)
        logger.debug("fetch_traces page=%d: %d 件", page, len(batch))
        if not batch:
            break

        traces.extend(batch)

        # meta.total_pages に達したら終了
        if page >= response.meta.total_pages:
            break
        page += 1

    traces = traces[: args.limit]
    logger.info("fetch_traces 完了: %d 件", len(traces))
    print(f"  取得完了: {len(traces)} 件")
    return traces


# ---------------------------------------------------------------------------
# Observation 取得
# ---------------------------------------------------------------------------

def fetch_observations_for_traces(
    lf: "Langfuse",
    traces: list,
    verbose: bool,
) -> dict[str, dict]:
    """
    各トレースの observation を取得する。
    戻り値: {trace_id: {obs_id: obs_object}}
    """
    _MAX_RETRIES = 3
    _RETRY_BASE_WAIT = 2.0  # 秒（指数バックオフの初期値）

    obs_by_trace: dict[str, dict] = {}
    logger.info("fetch_observations_for_traces 開始: %d 件のトレース", len(traces))
    print(f"{len(traces)} 件のトレースから observation を取得中 ...")

    for i, trace in enumerate(traces):
        logger.debug("[%d/%d] trace_id=%s", i + 1, len(traces), trace.id)
        if verbose:
            print(f"  [{i + 1}/{len(traces)}] trace_id={trace.id}")
        obs_map: dict = {}
        page = 1
        try:
            # observations.get_many() の limit 上限は 100 のためページネーションで全件取得
            while True:
                # 429 レートリミット対策: 指数バックオフでリトライ
                for attempt in range(_MAX_RETRIES):
                    try:
                        response = lf.api.observations.get_many(
                            trace_id=trace.id, limit=100, page=page
                        )
                        break
                    except Exception as e:
                        is_rate_limit = "429" in str(e)
                        if is_rate_limit and attempt < _MAX_RETRIES - 1:
                            wait = _RETRY_BASE_WAIT * (2 ** attempt)
                            logger.warning("429 レートリミット: trace=%s page=%d, %.0f秒後にリトライ [%d/%d]",
                                           trace.id, page, wait, attempt + 1, _MAX_RETRIES - 1)
                            print(
                                f"  429 レートリミット (trace {trace.id}, page {page})。"
                                f"{wait:.0f} 秒後にリトライ [{attempt + 1}/{_MAX_RETRIES - 1}]..."
                            )
                            time.sleep(wait)
                        else:
                            raise
                for o in response.data:
                    obs_map[o.id] = o
                if page >= response.meta.total_pages:
                    break
                page += 1
        except Exception as e:
            logger.warning("trace %s の observation 取得に失敗: %s", trace.id, e)
            print(f"  WARNING: trace {trace.id} の observation 取得に失敗: {e}")
        obs_by_trace[trace.id] = obs_map

    total = sum(len(m) for m in obs_by_trace.values())
    logger.info("fetch_observations_for_traces 完了: 合計 %d observation", total)
    print(f"  合計 observation 数: {total}")
    return obs_by_trace


# ---------------------------------------------------------------------------
# スパン分類ロジック
# ---------------------------------------------------------------------------

def _get_safe_metadata(obs) -> tuple[dict, dict]:
    """observation.metadata から resourceAttributes と attributes を安全に取得する。"""
    meta = obs.metadata or {}
    resource_attrs = meta.get("resourceAttributes", {}) if isinstance(meta, dict) else {}
    attrs = meta.get("attributes", {}) if isinstance(meta, dict) else {}
    return resource_attrs, attrs


def resolve_parent_agent(obs, obs_map: dict) -> tuple[str, str]:
    """
    parentObservationId チェーンを辿り、最も近い invoke_agent スパンを探す。
    戻り値: (node_id, node_label)
    """
    current = obs
    seen: set[str] = set()

    while current.parent_observation_id:
        pid = current.parent_observation_id
        if pid in seen:
            break  # 循環ガード
        seen.add(pid)

        parent = obs_map.get(pid)
        if parent is None:
            break

        pname = parent.name or ""
        if pname.startswith("invoke_agent "):
            agent_name = pname[len("invoke_agent "):]
            node_id, node_label = AGENT_NAME_TO_NODE.get(
                agent_name, (f"agent_{agent_name[:20]}", agent_name)
            )
            return node_id, node_label

        current = parent

    return "unknown_agent", "Unknown Agent"


def classify_and_extract_edges(
    observations: list,
    obs_map: dict,
    verbose: bool,
) -> list[dict]:
    """
    トレース内の全 observation を分類し、グラフノード・エッジ情報を返す。

    戻り値の各要素:
      {
        "node_id": str,
        "node_type": str,       # orchestrator / a2a_agent / mcp_server / llm / unknown
        "node_label": str,
        "edge": (src, dst) | None,
        "edge_label": str | None,
      }
    """
    results: list[dict] = []

    for obs in observations:
        name = obs.name or ""
        _, attrs = _get_safe_metadata(obs)

        if verbose:
            print(f"    obs: name={name!r}  type={obs.type}  parent={obs.parent_observation_id}")

        # ── エージェント起動境界スパン ─────────────────────────────────────
        if name.startswith("invoke_agent "):
            agent_name = name[len("invoke_agent "):]
            node_id, node_label = AGENT_NAME_TO_NODE.get(
                agent_name, (f"agent_{agent_name[:20]}", agent_name)
            )
            node_type = "orchestrator" if "Orchestrator" in agent_name else "a2a_agent"
            results.append({
                "node_id": node_id,
                "node_type": node_type,
                "node_label": node_label,
                "edge": None,
                "edge_label": None,
            })

        # ── A2A アウトバウンド呼び出しスパン（SpanKind.CLIENT） ───────────
        # tracer.py start_multiagent_span(): span_name = f"invoke_{instance}"
        elif name.startswith("invoke_") and not name.startswith("invoke_agent"):
            agent_instance = name[len("invoke_"):]
            # A2A 呼び出し元はオーケストレーター
            results.append({
                "node_id": "orchestrator",
                "node_type": "orchestrator",
                "node_label": "Orchestrator Agent",
                "edge": None,
                "edge_label": None,
            })
            # 呼び出し先エージェントのノードを追加し、エッジを張る
            node_id, node_label = AGENT_NAME_TO_NODE.get(
                agent_instance, (f"agent_{agent_instance[:20]}", agent_instance)
            )
            results.append({
                "node_id": node_id,
                "node_type": "a2a_agent",
                "node_label": node_label,
                "edge": ("orchestrator", node_id),
                "edge_label": "A2A",
            })

        # ── MCP ツール呼び出しスパン（Strands type=TOOL, "{server}___{tool}" 形式） ──
        # Strands Agents は MCPClient 経由のツール呼び出しを
        # type=TOOL, name="{server_name}___{tool_name}" として記録する
        elif "___" in name:
            server_name, tool_name = name.split("___", 1)
            parent_id, _ = resolve_parent_agent(obs, obs_map)

            if server_name in SERVER_NAME_TO_MCP_NODE:
                mcp_id, mcp_label = SERVER_NAME_TO_MCP_NODE[server_name]
            elif tool_name in TOOL_TO_MCP_NODE:
                # server_name が不明でもツール名から推定
                mcp_id, mcp_label = TOOL_TO_MCP_NODE[tool_name]
            else:
                # 完全に未知のサーバー → サーバー名からノードを動的生成
                safe = server_name.replace("-", "_")[:25]
                mcp_id = f"mcp_{safe}"
                mcp_label = f"MCP Server\n({server_name})"

            results.append({
                "node_id": mcp_id,
                "node_type": "mcp_server",
                "node_label": mcp_label,
                "edge": (parent_id, mcp_id),
                "edge_label": f"MCP\n({tool_name})",
            })

        # ── ツール呼び出しスパン（execute_tool 形式、旧 SDK との互換） ────────
        elif name.startswith("execute_tool "):
            tool_name = name[len("execute_tool "):]

            if tool_name in TOOL_TO_MCP_NODE:
                mcp_id, mcp_label = TOOL_TO_MCP_NODE[tool_name]
                parent_id, _ = resolve_parent_agent(obs, obs_map)
                results.append({
                    "node_id": mcp_id,
                    "node_type": "mcp_server",
                    "node_label": mcp_label,
                    "edge": (parent_id, mcp_id),
                    "edge_label": f"MCP\n({tool_name})",
                })

            else:
                unknown_id = f"tool_{tool_name[:25]}"
                parent_id, _ = resolve_parent_agent(obs, obs_map)
                results.append({
                    "node_id": unknown_id,
                    "node_type": "unknown",
                    "node_label": f"Tool: {tool_name}",
                    "edge": (parent_id, unknown_id),
                    "edge_label": tool_name,
                })

        # ── LLM 呼び出しスパン ────────────────────────────────────────────
        elif name == "chat":
            model_id = attrs.get("gen_ai.request.model", "bedrock")
            parent_id, _ = resolve_parent_agent(obs, obs_map)
            safe_model = model_id.replace(".", "_").replace("-", "_").replace(":", "_")
            llm_node_id = f"llm_{safe_model[:30]}"
            results.append({
                "node_id": llm_node_id,
                "node_type": "llm",
                "node_label": f"Amazon Bedrock\n({model_id})",
                "edge": (parent_id, llm_node_id),
                "edge_label": "LLM",
            })

        # ── 内部処理スパン（スキップ） ────────────────────────────────────
        elif name == "execute_event_loop_cycle":
            pass

    return results


# ---------------------------------------------------------------------------
# グラフ構築
# ---------------------------------------------------------------------------

def build_graph(obs_by_trace: dict[str, dict], verbose: bool) -> nx.DiGraph:
    """observation データから NetworkX 有向グラフを構築する。"""
    G = nx.DiGraph()
    node_registry: dict[str, dict] = {}
    edge_counts: dict[tuple, int] = defaultdict(int)
    edge_labels: dict[tuple, str] = {}

    for trace_id, obs_map in obs_by_trace.items():
        observations = list(obs_map.values())
        items = classify_and_extract_edges(observations, obs_map, verbose)

        for item in items:
            nid = item["node_id"]
            if nid and nid not in node_registry:
                node_registry[nid] = {
                    "node_type":  item["node_type"],
                    "node_label": item["node_label"],
                }

            if item["edge"]:
                src, dst = item["edge"]
                edge_counts[(src, dst)] += 1
                if item.get("edge_label") and (src, dst) not in edge_labels:
                    edge_labels[(src, dst)] = item["edge_label"]

    # ノードをグラフに追加
    for nid, attrs in node_registry.items():
        G.add_node(nid, **attrs)

    # エッジをグラフに追加（重み = 累計呼び出し回数）
    for (src, dst), count in edge_counts.items():
        if G.has_node(src) and G.has_node(dst):
            label = edge_labels.get((src, dst), "")
            G.add_edge(src, dst, weight=count, edge_label=label)

    logger.info("build_graph 完了: %d ノード, %d エッジ", G.number_of_nodes(), G.number_of_edges())
    print(f"グラフ: {G.number_of_nodes()} ノード, {G.number_of_edges()} エッジ")
    return G


# ---------------------------------------------------------------------------
# システムスキーマ自動生成
# ---------------------------------------------------------------------------

def export_system_schema(
    G: "nx.DiGraph",
    obs_by_trace: dict[str, dict],
    output_path: str,
) -> None:
    """
    NetworkX グラフと observation データから system_schema.json を生成する。

    グラフノード（エージェント・MCP サーバー・LLM）とエッジ（A2A / MCP 通信）を解析し、
    threat_modeling_agent の --system-file に渡せる JSON スキーマを出力する。

    トレースから読み取れない項目（memory, authentication, human_interaction 等）は
    null で出力されるため、手動で補完すること。
    """
    # --- ノード情報とLLMモデルを収集 ---
    agents: list[dict] = []
    mcp_servers_dict: dict[str, dict] = {}   # mcp_node_id -> {"name": str, "tools": set[str]}
    llm_models: set[str] = set()

    for node_id, attrs in G.nodes(data=True):
        node_type = attrs.get("node_type", "unknown")
        label = attrs.get("node_label", node_id)
        display_name = _label_to_display_name(label)

        if node_type == "orchestrator":
            agents.append({
                "name": display_name,
                "role": "全体を調整するオーケストレーター",
                "llm": None,
                "framework": "Strands Agents",
                "reasoning_pattern": "ReAct",
            })
        elif node_type == "a2a_agent":
            agents.append({
                "name": display_name,
                "role": "A2A サブエージェント",
                "llm": None,
                "framework": "Strands Agents",
                "reasoning_pattern": None,
            })
        elif node_type == "mcp_server":
            mcp_servers_dict[node_id] = {"name": display_name, "tools": set()}
        elif node_type == "llm":
            # ラベル形式: "Amazon Bedrock\n({model_id})"
            if "\n(" in label:
                model_id = label.split("\n(", 1)[1].rstrip(")")
                llm_models.add(model_id)

    # --- 全 observation を再スキャンしてMCPツール名を完全収集 ---
    # build_graph() のエッジは (src, dst) ペアごとに最初のツール名しか保持しないため、
    # 同一サーバーへの複数ツール呼び出しを正確に把握するには observation を直接見る。
    for _trace_id, obs_map in obs_by_trace.items():
        observations = list(obs_map.values())
        items = classify_and_extract_edges(observations, obs_map, verbose=False)
        for item in items:
            if item["node_type"] == "mcp_server":
                mcp_id = item["node_id"]
                edge_label = item.get("edge_label") or ""
                # エッジラベル形式: "MCP\n({tool_name})"
                if mcp_id in mcp_servers_dict and "MCP\n(" in edge_label:
                    tool_name = edge_label.split("MCP\n(", 1)[1].rstrip(")")
                    mcp_servers_dict[mcp_id]["tools"].add(tool_name)

    # --- LLM モデルをエージェントに付与 ---
    primary_llm = next(iter(llm_models), None)
    if primary_llm:
        for ag in agents:
            ag["llm"] = primary_llm

    # --- 通信フロー・プロトコル・ツール一覧を収集 ---
    protocols: set[str] = set()
    flows: list[dict] = []
    seen_flow_keys: set[tuple] = set()
    tool_list: list[str] = []

    for src, dst, data in G.edges(data=True):
        edge_label = data.get("edge_label", "")
        src_name = _label_to_display_name(G.nodes[src].get("node_label", src)) if G.has_node(src) else src
        dst_name = _label_to_display_name(G.nodes[dst].get("node_label", dst)) if G.has_node(dst) else dst

        if edge_label == "A2A":
            protocols.add("A2A")
            key = (src_name, dst_name, "A2A")
            if key not in seen_flow_keys:
                seen_flow_keys.add(key)
                flows.append({"from": src_name, "to": dst_name, "protocol": "A2A", "auth": None})

        elif edge_label.startswith("MCP"):
            protocols.add("MCP")
            key = (src_name, dst_name, "MCP")
            if key not in seen_flow_keys:
                seen_flow_keys.add(key)
                flows.append({"from": src_name, "to": dst_name, "protocol": "MCP", "auth": None})

        elif edge_label == "LLM":
            protocols.add("Bedrock")

    # MCP サーバーのツールをフラットなリストに変換
    for mcp_data in mcp_servers_dict.values():
        for tool in sorted(mcp_data["tools"]):
            if tool not in tool_list:
                tool_list.append(tool)

    mcp_servers_list = [
        {"name": v["name"], "tools": sorted(v["tools"]), "hosted_by": None}
        for v in mcp_servers_dict.values()
    ]

    # --- マルチエージェント判定 ---
    a2a_count = sum(1 for ag in agents if ag.get("role") == "A2A サブエージェント")
    is_multi = a2a_count > 0

    # --- スキーマ構築 ---
    schema = {
        "name": "（トレースから自動生成 — 手動で修正してください）",
        "overview": (
            "Langfuse OTEL トレースから自動生成されたシステムスキーマです。\n"
            "null の項目は実際のシステム設定を確認して補完してください。"
        ),
        "components": {
            "agents": agents,
            "mcp_servers": mcp_servers_list,
            "storage": [],
            "external_apis": [],
        },
        "communication": {
            "protocols": sorted(protocols),
            "flows": flows,
            "encryption": None,
        },
        "memory": {
            "short_term": None,
            "long_term": None,
            "vector_db": None,
            "shared_memory": None,
        },
        "tools": {
            "code_execution": None,
            "file_access": None,
            "external_api_calls": len(tool_list) > 0 or None,
            "email_or_messaging": None,
            "database_write": None,
            "tool_list": tool_list,
        },
        "authentication": {
            "enabled": None,
            "method": None,
            "rbac": None,
            "nhi": None,
            "least_privilege": None,
            "token_rotation": None,
        },
        "human_interaction": {
            "hitl": None,
            "user_interaction": None,
            "interaction_type": None,
            "user_trust_level": None,
        },
        "multi_agent": {
            "enabled": is_multi,
            "agent_count": len(agents),
            "architecture": (
                f"オーケストレーター 1 台 + サブエージェント {a2a_count} 台"
                if is_multi else None
            ),
            "delegation_mechanism": "A2A プロトコル" if "A2A" in protocols else None,
            "trust_boundaries": None,
            "shared_resources": [],
        },
        "notes": (
            "このスキーマは Langfuse OTEL トレースから自動生成されました。\n"
            "null の項目は実際のシステム設定を確認して補完してください。\n"
            f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        ),
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)

    abs_path = os.path.abspath(output_path)
    print(f"システムスキーマを出力しました: {abs_path}")
    print(f"脅威モデリング実行例:")
    print(f"  python threat_modeling_agent/threat_modeling_agent.py --system-file {abs_path}")


# ---------------------------------------------------------------------------
# HTML レンダリング
# ---------------------------------------------------------------------------

def render_html(
    G: nx.DiGraph,
    output_path: str,
    args: argparse.Namespace,
    trace_count: int,
) -> None:
    """pyvis で NetworkX グラフをインタラクティブ HTML に変換する。"""
    heading = (
        f"MAS Topology — {trace_count} traces"
        f" — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    net = Network(
        height="850px",
        width="100%",
        directed=True,
        bgcolor="#1a1a2e",
        font_color="#ffffff",
        notebook=False,
        heading=heading,
    )

    for node_id, attrs in G.nodes(data=True):
        node_type = attrs.get("node_type", "unknown")
        style = NODE_STYLES.get(node_type, NODE_STYLES["unknown"])
        label = attrs.get("node_label", node_id)

        out_calls = sum(G[node_id][n]["weight"] for n in G.successors(node_id))
        in_calls  = sum(G[n][node_id]["weight"] for n in G.predecessors(node_id))

        net.add_node(
            node_id,
            label=label,
            shape=style["shape"],
            image=style["image"],
            size=style["size"],
            color={
                "border":     style["border_color"],
                "background": style["border_color"],
                "highlight":  {"border": "#ffffff", "background": style["border_color"]},
                "hover":      {"border": "#ffffff", "background": style["border_color"]},
            },
            borderWidth=style["border_width"],
            borderWidthSelected=style["border_width"] + 2,
            title=(
                f"<b>{label.replace(chr(10), '<br>')}</b><br>"
                f"種別: {node_type}<br>"
                f"送信呼び出し数: {out_calls}<br>"
                f"受信呼び出し数: {in_calls}"
            ),
            font={"size": 13, "color": "#ffffff", "bold": True},
        )

    for src, dst, data in G.edges(data=True):
        weight = data.get("weight", 1)
        edge_label = data.get("edge_label", "")
        # エッジ幅: 1〜8 px でスケール
        width = 1 + min(7, weight // 3)

        net.add_edge(
            src,
            dst,
            width=width,
            title=f"{edge_label}<br>呼び出し数: {weight}",
            label=f"{weight}x",
            color={"color": "#88aacc", "highlight": "#ffffff"},
            arrows={"to": {"enabled": True, "scaleFactor": 1.2}},
            font={"size": 10, "color": "#ccddee", "align": "top"},
        )

    physics_enabled = "false" if args.no_physics else "true"
    net.set_options(f"""
    {{
      "physics": {{
        "enabled": {physics_enabled},
        "solver": "barnesHut",
        "barnesHut": {{
          "gravitationalConstant": -12000,
          "centralGravity": 0.25,
          "springLength": 250,
          "springConstant": 0.035,
          "damping": 0.12,
          "avoidOverlap": 0.8
        }},
        "stabilization": {{"iterations": 300}}
      }},
      "interaction": {{
        "hover": true,
        "tooltipDelay": 150,
        "navigationButtons": true,
        "keyboard": true
      }},
      "edges": {{
        "smooth": {{"type": "curvedCW", "roundness": 0.2}}
      }}
    }}
    """)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    net.write_html(output_path)

    abs_path = os.path.abspath(output_path)
    print(f"\n可視化ファイルを出力しました: {abs_path}")
    print(f"ブラウザで開く: file://{abs_path}")


# ---------------------------------------------------------------------------
# メインエントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    # 1. Langfuse クライアントを作成
    lf = create_langfuse_client(args)

    # 2. トレースを取得
    traces = fetch_traces(lf, args)
    if not traces:
        sys.exit(
            "トレースが見つかりませんでした。\n"
            "  --hours や --limit を調整するか、Langfuse 認証情報を確認してください。"
        )

    # 3. 各トレースの observation を取得
    obs_by_trace = fetch_observations_for_traces(lf, traces, args.verbose)
    total_obs = sum(len(m) for m in obs_by_trace.values())
    if total_obs == 0:
        sys.exit(
            "observation が見つかりませんでした。\n"
            "  OTEL エクスポーターが正しく設定されているか確認してください。"
        )

    # 4. NetworkX グラフを構築
    G = build_graph(obs_by_trace, args.verbose)
    if G.number_of_nodes() == 0:
        sys.exit(
            "グラフにノードが存在しません。\n"
            "  --verbose オプションで observation 名を確認し、"
            "  AGENT_NAME_TO_NODE や TOOL_TO_MCP_NODE を調整してください。"
        )

    # 5. HTML グラフをレンダリング
    render_html(G, args.output, args, trace_count=len(traces))

    # 6. （オプション）システムスキーマ JSON を出力
    if args.export_schema:
        export_system_schema(G, obs_by_trace, args.export_schema)


if __name__ == "__main__":
    main()
