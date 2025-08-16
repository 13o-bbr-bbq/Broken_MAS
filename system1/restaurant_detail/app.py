from fastmcp import FastMCP
from typing import Dict, Any, Optional

DETAILS = {
    "broken_pizza_shop": {
        "id": "broken_pizza_shop",
        "name": "Broken Pizza Shop",
        "address": "1-2-3 Shibuya-ku, Tokyo",
        "genre": "pizza",
        "pr": "Authentic Neapolitan pizza baked in a stone oven."
    },
    "sakura_sushi": {
        "id": "sakura_sushi",
        "name": "Sakura Sushi",
        "address": "1-2-3 Minato-ku, Tokyo",
        "genre": "sushi",
        "pr": "Seasonal ingredients at reasonable prices."
    },
    "mori_ramen": {
        "id": "mori_ramen",
        "name": "Mori Ramen",
        "address": "1-2-3 Chuo-ku, Tokyo",
        "genre": "ramen",
        "pr": "The volume is impressive, surpassing even Jiro-style ramen."
    },
}

def create_server() -> FastMCP:
    mcp = FastMCP("retrieve_restaurant_detail")

    @mcp.tool(
        name="retrieve_restaurant_details",
        description="Returns the specified restaurant details."
    )
    def retrieve_restaurant_details(restaurant_id: str) -> Optional[Dict[str, Any]]:
        return DETAILS.get(restaurant_id)

    return mcp


def main() -> None:
    mcp = create_server()
    mcp.run(transport="http", host="0.0.0.0", port=8080, path="/mcp")


if __name__ == "__main__":
    main()