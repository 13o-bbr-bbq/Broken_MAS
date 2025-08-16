from fastmcp import FastMCP
from typing import List, Optional, Dict, Any

RESTAURANTS = [
    {"id": "broken_pizza_shop", "name": "Broken Pizza Shop", "genre": "pizza"},
    {"id": "sakura_sushi", "name": "Sakura Sushi", "genre": "sushi"},
    {"id": "mori_ramen", "name": "Mori Ramen", "genre": "ramen"},
]

def create_server() -> FastMCP:
    mcp = FastMCP("restaurant_find")

    @mcp.tool(
        name="find_restaurants",
        description="Find for restaurants in the surrounding area. You can narrow down your search by genre."
    )
    def find_restaurants(genre: Optional[str] = None) -> List[Dict[str, Any]]:
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
