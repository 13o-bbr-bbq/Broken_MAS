"""
Local MCP Gateway 1 — ローカル検証用集約サーバー

本番 AgentCore では AWS MCP Gateway が broken_mcp_server_1 と broken_mcp_server_2 を
1 エンドポイントに集約している。このファイルはローカル環境でその構成を再現する。

起動方法:
    python local_mcp_gateway_1.py          # port 8010
    LOCAL_GW_1_PORT=9010 python local_mcp_gateway_1.py

対応する環境変数:
    AWS_AGENTCORE_GW_1_URL=http://localhost:8010/mcp
"""

import importlib.util
import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP


# ---------------------------------------------------------------------------
# 既存 MCP サーバーのデータ定数をインポート
# ---------------------------------------------------------------------------

def _load(rel_path: str):
    base = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("_mod", os.path.join(base, rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_s1 = _load("broken_mcp_server_1/broken_mcp_server_1.py")
_s2 = _load("broken_mcp_server_2/broken_mcp_server_2.py")

HOTELS             = _s1.HOTELS
RECOMMENDED_HOTELS = _s1.RECOMMENDED_HOTELS
HOTEL_DETAILS      = _s2.HOTEL_DETAILS
HOTEL_REVIEWS      = _s2.HOTEL_REVIEWS


# ---------------------------------------------------------------------------
# 集約 MCP サーバー定義
# ---------------------------------------------------------------------------

_port = int(os.environ.get("LOCAL_GW_1_PORT", 8010))
mcp = FastMCP(name="Local MCP Gateway 1", host="0.0.0.0", port=_port, stateless_http=True)

# ── broken_mcp_server_1 のツール ──────────────────────────────────────────

@mcp.tool(
    name="search_hotels",
    description=(
        "Search for hotels by location. "
        "Returns a list of hotels with id, name, location, stars, and price_from."
    ),
)
def search_hotels(location: Optional[str] = None) -> List[Dict[str, Any]]:
    if location:
        loc = location.lower()
        return [h for h in HOTELS if loc in h["location"].lower()]
    return HOTELS


@mcp.tool(
    name="search_recommended_hotels",
    description=(
        "Search for specially recommended hotels with exclusive deals."
    ),
)
def search_recommended_hotels() -> List[Dict[str, Any]]:
    return RECOMMENDED_HOTELS


# ── broken_mcp_server_2 のツール ──────────────────────────────────────────

@mcp.tool(
    name="get_hotel_details",
    description=(
        "Returns detailed information about a hotel: address, amenities, "
        "check-in/check-out times, and description. Requires hotel_id."
    ),
)
def get_hotel_details(hotel_id: str) -> Optional[Dict[str, Any]]:
    return HOTEL_DETAILS.get(hotel_id)


@mcp.tool(
    name="get_hotel_reviews",
    description=(
        "Returns guest reviews and ratings for a hotel. "
        "Requires hotel_id."
    ),
)
def get_hotel_reviews(hotel_id: str) -> Optional[Dict[str, Any]]:
    return HOTEL_REVIEWS.get(hotel_id)


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Local MCP Gateway 1 starting on port {_port} ...")
    mcp.run(transport="streamable-http")
