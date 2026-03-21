import os
import uuid
from mcp.server.fastmcp import FastMCP
from typing import Dict, Any
from datetime import datetime, timedelta


# Price lookup table (same as MCP Server 3)
ROOM_PRICES: Dict[str, Dict[str, int]] = {
    "sakura_palace": {"standard": 28000, "deluxe": 38000, "suite": 65000},
    "edo_inn":       {"standard":  8500, "superior": 11000},
    "shibuya_stay":  {"standard": 15000, "deluxe": 22000, "suite": 40000},
    "harbor_grand":  {"standard": 35000, "deluxe": 48000, "suite": 90000},
    "kyoto_annex":   {"standard": 18000, "deluxe": 26000, "suite": 45000},
    "akihabara_tech":{"standard":  9800, "smart": 13000},
}


def create_server() -> FastMCP:
    mcp = FastMCP(
        name="Hotel Reservation",
        host="0.0.0.0",
        port=int(os.environ.get("MCP_PORT", 8000)),
        stateless_http=True,
    )

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
            from datetime import date
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

    return mcp


if __name__ == "__main__":
    mcp = create_server()
    mcp.run(transport="streamable-http")
