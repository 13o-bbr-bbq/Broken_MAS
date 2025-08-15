import os
from fastmcp import FastMCP
from typing import Dict, Any, Optional

DETAILS = {
    "broken_pizza_shop": {
        "id": "broken_pizza_shop",
        "name": "Broken Pizza Shop",
        "address": "東京都渋谷区1-2-3",
        "genre": "ピザ",
        "pr": "石窯で焼く本格ナポリピッツァ。",
        "a2a_url": os.getenv("PEER_A2A_2_URL"),
    },
    "sakura_sushi": {
        "id": "sakura_sushi",
        "name": "さくら寿司",
        "address": "東京都港区1-2-3",
        "genre": "寿司",
        "pr": "旬のネタを手頃に。",
        "a2a_url": None,
    },
}

def create_server() -> FastMCP:
    mcp = FastMCP("restaurant_detail")

    @mcp.tool(
        name="get_restaurant_details",
        description="指定IDの飲食店詳細を返却。ピザ屋はA2A URLを持つ。"
    )
    def get_restaurant_details(restaurant_id: str) -> Optional[Dict[str, Any]]:
        return DETAILS.get(restaurant_id)

    return mcp


def main() -> None:
    mcp = create_server()
    mcp.run(transport="http", host="0.0.0.0", port=8080, path="/mcp")


if __name__ == "__main__":
    main()