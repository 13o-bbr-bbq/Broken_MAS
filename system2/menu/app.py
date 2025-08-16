from fastmcp import FastMCP
from typing import List, Dict, Any
from pydantic import BaseModel, Field

MENU = [
    {"name": "margherita", "price": 1800, "description": "tomato, mozzarella, basil"},
    {"name": "pepperoni", "price": 2200, "description": "spicy pepperoni"},
    {"name": "seafood", "price": 1900, "description": "shrimp, crab, squid, tuna"},
    {"name": "marinara", "price": 1200, "description": "Tomato, garlic, oregano"},
]

class MenuItem(BaseModel):
    name: str = Field(..., description="Menu item name")
    price: int = Field(..., ge=0, description="Price in JPY")
    description: str = Field(..., description="Short description of the item")


class MenuResponse(BaseModel):
    items: List[MenuItem]


def create_server() -> FastMCP:
    mcp = FastMCP("pizza_menu")

    @mcp.tool(
        name="get_menu",
        description="Return a list of items on a pizza menu."
    )
    def get_menu() -> MenuResponse:
        res = MenuResponse(items=[MenuItem(**x) for x in MENU])
        print(f"System2: from MCP Server's res: {res}, {type(res)}")
        return MenuResponse(items=[MenuItem(**x) for x in MENU])

    return mcp


def main() -> None:
    mcp = create_server()
    mcp.run(transport="http", host="0.0.0.0", port=8080, path="/mcp")


if __name__ == "__main__":
    main()