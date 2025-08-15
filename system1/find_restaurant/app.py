from fastmcp import FastMCP
from typing import List, Optional, Dict, Any

RESTAURANTS = [
    {"id": "broken_pizza_shop", "name": "Broken Pizza Shop", "genre": "ピザ"},
    {"id": "sakura_sushi", "name": "さくら寿司", "genre": "寿司"},
    {"id": "mori_ramen", "name": "森ラーメン", "genre": "ラーメン"},
]

def create_server() -> FastMCP:
    mcp = FastMCP("restaurant_search")

    @mcp.tool(
        name="search_restaurants",
        description="周辺の飲食店を検索。genreで絞り込み可。"
    )
    def search_restaurants(genre: Optional[str] = None) -> List[Dict[str, Any]]:
        results = RESTAURANTS
        if genre:
            results = [r for r in RESTAURANTS if r["genre"] == genre]
        return results

    return mcp


def main() -> None:
    mcp = create_server()
    mcp.run(transport="http", host="0.0.0.0", port=8080, path="/mcp")


if __name__ == "__main__":
    main()
