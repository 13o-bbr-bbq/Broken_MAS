from fastmcp import FastMCP
from typing import Dict, Any
from datetime import datetime, timedelta


def create_server() -> FastMCP:
    mcp = FastMCP("pizza_order")

    @mcp.tool(
        name="place_order",
        description="商品名と価格を受け取り、ダミーの配達予定時刻を返す。"
    )
    def place_order(item_name: str, price: int) -> Dict[str, Any]:
        eta = (datetime.now() + timedelta(minutes=35)).isoformat(timespec="minutes")
        return {"ordered_item": item_name, "price": price, "estimated_delivery": eta}

    return mcp


def main() -> None:
    mcp = create_server()
    mcp.run(transport="http", host="0.0.0.0", port=8080, path="/mcp")


if __name__ == "__main__":
    main()