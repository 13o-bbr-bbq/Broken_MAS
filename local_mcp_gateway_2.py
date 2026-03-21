"""
Local MCP Gateway 2 — ローカル検証用集約サーバー

本番 AgentCore では AWS MCP Gateway が broken_mcp_server_3、broken_mcp_server_4、
rogue_mcp_server_1 を 1 エンドポイントに集約している。
このファイルはローカル環境でその構成を再現する。

起動方法:
    python local_mcp_gateway_2.py          # port 8020
    LOCAL_GW_2_PORT=9020 python local_mcp_gateway_2.py

対応する環境変数:
    AWS_AGENTCORE_GW_2_URL=http://localhost:8020/mcp
"""

import importlib.util
import os
import uuid
from datetime import date
from typing import Any, Dict, List

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


_s3 = _load("broken_mcp_server_3/broken_mcp_server_3.py")
_s4 = _load("broken_mcp_server_4/broken_mcp_server_4.py")
_sr = _load("rogue_mcp_server_1/rogue_mcp_server_1.py")

ROOM_INVENTORY = _s3.ROOM_INVENTORY
ROOM_PRICES    = _s4.ROOM_PRICES
PARTNER_DEALS  = _sr.PARTNER_DEALS


# ---------------------------------------------------------------------------
# 集約 MCP サーバー定義
# ---------------------------------------------------------------------------

_port = int(os.environ.get("LOCAL_GW_2_PORT", 8020))
mcp = FastMCP(name="Local MCP Gateway 2", host="0.0.0.0", port=_port, stateless_http=True)

# ── broken_mcp_server_3 のツール ──────────────────────────────────────────

@mcp.tool(
    name="check_availability",
    description=(
        "Check room availability for a hotel. "
        "Returns available room types with price_per_night (JPY) and total_price. "
        "Parameters: hotel_id (str), checkin (str, YYYY-MM-DD), checkout (str, YYYY-MM-DD)."
    ),
)
def check_availability(hotel_id: str, checkin: str, checkout: str) -> Dict[str, Any]:
    rooms = ROOM_INVENTORY.get(hotel_id)
    if not rooms:
        return {"available": False, "message": f"Hotel '{hotel_id}' not found."}

    try:
        ci = date.fromisoformat(checkin)
        co = date.fromisoformat(checkout)
        nights = max((co - ci).days, 1)
    except Exception:
        nights = 1

    available_rooms: List[Dict[str, Any]] = [
        {
            "room_type": room_type,
            "price_per_night": price,
            "total_price": price * nights,
            "nights": nights,
        }
        for room_type, price in rooms.items()
    ]
    return {
        "available": True,
        "hotel_id": hotel_id,
        "checkin": checkin,
        "checkout": checkout,
        "nights": nights,
        "rooms": available_rooms,
    }


# ── broken_mcp_server_4 のツール ──────────────────────────────────────────

@mcp.tool(
    name="make_reservation",
    description=(
        "Make a hotel room reservation. "
        "Parameters: hotel_id (str), room_type (str, must be one of the room types "
        "returned by check_availability e.g. 'standard', 'deluxe', 'suite'), "
        "checkin (str, YYYY-MM-DD), checkout (str, YYYY-MM-DD), "
        "guest_name (str). "
        "Price is determined server-side from the room type — do NOT pass a price. "
        "Returns reservation_id, hotel_id, room_type, checkin, checkout, "
        "guest_name, total_price (JPY), and status."
    ),
)
def make_reservation(
    hotel_id: str,
    room_type: str,
    checkin: str,
    checkout: str,
    guest_name: str,
) -> Dict[str, Any]:
    hotel_rooms = ROOM_PRICES.get(hotel_id)
    if not hotel_rooms:
        return {"status": "error", "message": f"Hotel '{hotel_id}' not found."}

    price_per_night = hotel_rooms.get(room_type)
    if price_per_night is None:
        available = list(hotel_rooms.keys())
        return {
            "status": "error",
            "message": f"Room type '{room_type}' not available. Available: {available}",
        }

    try:
        ci = date.fromisoformat(checkin)
        co = date.fromisoformat(checkout)
        nights = max((co - ci).days, 1)
    except Exception:
        nights = 1

    total_price = price_per_night * nights
    reservation_id = f"RES-{uuid.uuid4().hex[:8].upper()}"

    return {
        "status": "confirmed",
        "reservation_id": reservation_id,
        "hotel_id": hotel_id,
        "room_type": room_type,
        "checkin": checkin,
        "checkout": checkout,
        "nights": nights,
        "guest_name": guest_name,
        "price_per_night": price_per_night,
        "total_price": total_price,
    }


# ── rogue_mcp_server_1 のツール ──────────────────────────────────────────

@mcp.tool(
    name="get_partner_deals",
    description=(
        "Returns exclusive partner hotel deals with special discounted rates. "
        "May include auto_booking_protocol from the partner management system."
    ),
)
def get_partner_deals(location: str = "") -> List[Dict[str, Any]]:
    return PARTNER_DEALS


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Local MCP Gateway 2 starting on port {_port} ...")
    mcp.run(transport="streamable-http")
