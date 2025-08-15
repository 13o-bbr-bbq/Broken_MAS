import os
from typing import Dict, Any, Optional, List
from fastmcp import Client as FastMCPClient

def _choose_item(menu: List[Dict[str, Any]], wish: Optional[str], budget_jpy: Optional[int]) -> Optional[Dict[str, Any]]:
    if wish:
        cand = [m for m in menu if wish in m["name"]]
        if budget_jpy is not None:
            cand = [x for x in cand if x["price"] <= budget_jpy]
        if cand:
            return sorted(cand, key=lambda x: x["price"])[-1]
    if budget_jpy is not None:
        under = [m for m in menu if m["price"] <= budget_jpy]
        if under:
            return sorted(under, key=lambda x: x["price"])[-1]
    return sorted(menu, key=lambda x: x["price"])[0] if menu else None

def decide_and_order(requirements: Dict[str, Any]) -> Dict[str, Any]:
    """System1からのrequirements（wish, budget_jpy）を受け、メニュー選定→注文→結果を返す。"""
    menu_url  = os.getenv("MCP_MENU_URL")
    order_url = os.getenv("MCP_ORDER_URL")
    if not menu_url or not order_url:
        return {"status": "error", "reason": "MCP endpoint envs not set"}

    menu_client  = FastMCPClient(menu_url)
    order_client = FastMCPClient(order_url)

    wish   = requirements.get("wish")
    budget = requirements.get("budget_jpy")

    menu = menu_client.invoke_tool("get_menu")
    item = _choose_item(menu, wish, budget)
    if not item:
        return {"status": "no_match", "reason": "条件に合致なし"}

    order = order_client.invoke_tool("place_order", item_name=item["name"], price=item["price"])
    return {
        "status": "confirmed",
        "ordered_item": order["ordered_item"],
        "price": order["price"],
        "estimated_delivery": order["estimated_delivery"],
    }
