import os
from mcp.server.fastmcp import FastMCP
from typing import List, Dict, Any


# room_type -> price per night (JPY)
ROOM_INVENTORY: Dict[str, Dict[str, int]] = {
    "sakura_palace": {
        "standard":  28000,
        "deluxe":    38000,
        "suite":     65000,
    },
    "edo_inn": {
        "standard":   8500,
        "superior":  11000,
    },
    "shibuya_stay": {
        "standard":  15000,
        "deluxe":    22000,
        "suite":     40000,
    },
    "harbor_grand": {
        "standard":  35000,
        "deluxe":    48000,
        "suite":     90000,
    },
    "kyoto_annex": {
        "standard":  18000,
        "deluxe":    26000,
        "suite":     45000,
    },
    "akihabara_tech": {
        "standard":   9800,
        "smart":     13000,
    },
}


def create_server() -> FastMCP:
    mcp = FastMCP(
        name="Hotel Availability",
        host="0.0.0.0",
        port=int(os.environ.get("MCP_PORT", 8000)),
        stateless_http=True,
    )

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

        # Calculate nights (simple string parse)
        try:
            from datetime import date
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

    return mcp


if __name__ == "__main__":
    mcp = create_server()
    mcp.run(transport="streamable-http")
