from fastmcp import FastMCP
from typing import List, Dict, Any

MENU = [
    {"name": "マルゲリータ", "price": 1800, "description": "トマト・モッツァレラ・バジル"},
    {"name": "ペパロニ", "price": 2200, "description": "スパイシーペパロニ"},
    {"name": "クワトロフォルマッジ", "price": 2400, "description": "4種のチーズ"},
    {"name": "マリナーラ", "price": 1200, "description": "トマト・ニンニク・オレガノ"},
]

def create_server() -> FastMCP:
    mcp = FastMCP("pizza_menu")

    @mcp.tool(
        name="get_menu",
        description="ピザ屋のメニュー一覧を返す。"
    )
    def get_menu() -> List[Dict[str, Any]]:
        return MENU

    return mcp


def main() -> None:
    mcp = create_server()
    mcp.run(transport="http", host="0.0.0.0", port=8080, path="/mcp")


if __name__ == "__main__":
    main()