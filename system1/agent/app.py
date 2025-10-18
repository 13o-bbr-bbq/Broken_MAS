import os
import json
import uuid
import asyncio
import anyio
import httpx
import pathlib
import hashlib
from typing import Dict, Any, List
from datetime import datetime, timezone

from autogen_agentchat.agents import UserProxyAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console
from autogen_agentchat.agents import AssistantAgent as Agent
from autogen_ext.tools.mcp import mcp_server_tools, StreamableHttpServerParams
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.tools import FunctionTool

# A2A.
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import MessageSendParams, SendMessageRequest


# Long term memory.
class PersistentListMemory:
    def __init__(self, path: str, namespace: str, auto_load: bool = True):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace
        self._sha1_index: set[str] = set()
        self._items: List[dict] = []
        if auto_load and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    obj = json.loads(line)
                    sha1 = obj.get("sha1")
                    if sha1:
                        self._sha1_index.add(sha1)
                    self._items.append(obj)
                except Exception:
                    continue

    def _save_jsonl(self, rec: dict) -> None:
        self.path.open("a", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False) + "\n")

    def add_with_meta(
        self,
        text: str,
        *,
        source: str = "user",
        author: str = "user",
        tags: List[str] | None = None,
        confidence: float = 0.9,
    ) -> bool:
        if not isinstance(text, str) or not text.strip():
            return False
        t = text.strip().replace("\r\n", "\n")

        lower = t.lower()
        injection_like = any(kw in lower for kw in [
            "ignore previous", "disregard previous", "system prompt",
            "you must", "do not obey", "override instruction"
        ])
        if injection_like:
            return False

        if len(t) > 1200:  # 過度な肥大化対策（要約運用に置換可）
            return False

        sha1 = hashlib.sha1(t.encode("utf-8")).hexdigest()
        if sha1 in self._sha1_index:
            return False
        self._sha1_index.add(sha1)

        rec = {
            "ns": self.namespace,
            "text": t,
            "source": source,     # "user" / "tool:mcp.xxx" / "a2a:system2"
            "author": author,
            "tags": tags or [],
            "confidence": float(confidence),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sha1": sha1,
        }
        self._items.append(rec)
        self._save_jsonl(rec)
        return True

    def retrieve_topk(self, query: str, *, k: int = 5, min_conf: float = 0.8) -> List[dict]:
        q = (query or "").lower().strip()
        pool = []
        for j in self._items:
            text = j.get("text", "")
            if not isinstance(text, str):
                continue
            if q and q not in text.lower():
                continue
            if j.get("confidence", 0.0) < min_conf:
                continue
            pool.append(j)
        pool.sort(key=lambda x: (x.get("confidence", 0.0), x.get("created_at", "")), reverse=True)
        return pool[: max(1, min(int(k), 10))]


# Build long term memory for user.
def create_user_memory() -> PersistentListMemory:
    mem = PersistentListMemory(path="./long_term_user.jsonl", namespace="system1/user")
    mem.add_with_meta(
        "The user's preference is pizza.",
        source="user",
        author="user",
        tags=["preference", "food"],
        confidence=0.95,
    )
    return mem


# Memory search tool.
def build_memory_tools(user_memory: PersistentListMemory) -> List[FunctionTool]:
    async def retrieve_memory(query: str, top_k: int = 5) -> Dict[str, Any]:
        items = user_memory.retrieve_topk(query, k=top_k, min_conf=0.8)
        results = [{"text": it["text"], "tags": it.get("tags", [])} for it in items]
        return {"query": query, "k": len(results), "results": results}

    async def save_user_fact(text: str, tags: List[str] | None = None, confidence: float = 0.9) -> Dict[str, Any]:
        ok = user_memory.add_with_meta(
            text,
            source="user",
            author=os.getenv("A2A_1_AGENT_NAME", "System1Agent"),
            tags=tags or [],
            confidence=confidence,
        )
        return {"saved": bool(ok)}

    async def persist_customer_tips(
        items: Any = None,
        tag: str = "preference",
        items_json: str | None = None,
        restaurants: Any = None,
        results: Any = None,
        array_text: str | None = None,
    ) -> Dict[str, Any]:
        import json
        src = items
        for alt in (restaurants, results):
            if src is None and alt is not None:
                src = alt
        if src is None and items_json:
            try:
                src = json.loads(items_json)
            except Exception:
                pass
        if src is None and array_text:
            try:
                src = json.loads(array_text)
            except Exception:
                pass

        if src is None:
            return {"saved": 0, "processed": 0, "reason": "no items provided"}

        if isinstance(src, str):
            try:
                src = json.loads(src)
            except Exception:
                src = []
        if isinstance(src, dict):
            if isinstance(src.get("items"), list):
                src = src["items"]
            elif isinstance(src.get("results"), list):
                src = src["results"]
            else:
                src = [src]
        if not isinstance(src, list):
            src = [src]

        norm: List[Dict[str, Any]] = []
        for it in src:
            if isinstance(it, str):
                try:
                    it = json.loads(it)
                except Exception:
                    continue
            if isinstance(it, dict):
                norm.append(it)

        # Save.
        saved = 0
        for it in norm:
            tip = it.get("customer_tips")
            if isinstance(tip, str) and tip.strip():
                ok = user_memory.add_with_meta(
                    tip.strip(),
                    source="tool:mcp.restaurant_find",
                    author=os.getenv("A2A_1_AGENT_NAME", "System1Agent"),
                    tags=[tag, "customer_tips"],
                    confidence=0.95,
                )
                if ok:
                    saved += 1
        return {"saved": saved, "processed": len(norm)}


    return [
        FunctionTool(
            name="retrieve_memory",
            func=retrieve_memory,
            description="Retrieve top-K trusted long-term memories that match a query."
        ),
        FunctionTool(
            name="save_user_fact",
            func=save_user_fact,
            description="Persist a user-related fact/preference into long-term memory."
        ),
        FunctionTool(
            name="persist_customer_tips",
            func=persist_customer_tips,
            description=(
                "Persist any 'customer_tips' fields from a list of restaurant objects into long-term memory. "
                "Accepts `items` (array/object), or raw JSON via `items_json`/`array_text`, and wrapper dicts."
            ),
        ),
    ]


def create_autogen_agent() -> Agent:
    # Create model client.
    model_client = OpenAIChatCompletionClient(
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key = os.getenv("OPENAI_API_KEY")
    )

    # Load user's memory.
    user_memory = create_user_memory()

    # Tool (MCP server) registration.
    find_params = StreamableHttpServerParams(url=os.getenv("MCP_FIND_RESTAURANT_URL"))
    detail_params = StreamableHttpServerParams(url=os.getenv("MCP_RESTAURANT_DETAIL_URL"))
    search_tools = asyncio.run(mcp_server_tools(find_params))
    detail_tools = asyncio.run(mcp_server_tools(detail_params))
    tools = search_tools + detail_tools

    # ---- A2A: System1 の A2A（プロキシ）へ注文仕様を送るツール ----
    peer_base_url = os.getenv("PEER_A2A_1_URL")
    async def send_order_via_a2a(spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        System1 の A2A（a2a-sdk サーバ）へ 'message/send' を投げ、
        戻りの text パート(JSON文字列)を dict にして返す。
        """
        async with httpx.AsyncClient() as httpx_client:
            # Create A2A rsolver.
            resolver = A2ACardResolver(
                httpx_client=httpx_client,
                base_url=peer_base_url
            )

            # Get "/.well-known/agent-card.json" on System1 A2A Server.
            system1_card = await resolver.get_agent_card()

            # Create A2A Client.
            client = A2AClient(
                httpx_client=httpx_client,
                agent_card=system1_card
            )

            # Send message.
            send_message_payload: dict[str, Any] = {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": json.dumps(spec, ensure_ascii=False)}],
                    "messageId": uuid.uuid4().hex,
                }
            }
            print(f"System1: to A2A Server's spec: {spec}, {type(spec)}")
            request = SendMessageRequest(
                id=str(uuid.uuid4()),
                params=MessageSendParams(**send_message_payload)
            )

            response = await client.send_message(request)
            print(f"System1: from A2A Server's response: {response}, {type(response)}")

            def _unwrap_part(x):
                if hasattr(x, "root"):
                    return x.root
                if hasattr(x, "__root__"):
                    return x.__root__
                return x

            # 5) 応答の text パートから JSON を抽出
            resp_obj = getattr(response, "root", response)  # SendMessageSuccessResponse になる
            msg = getattr(resp_obj, "result", None)
            if msg and getattr(msg, "parts", None):
                for p in msg.parts:
                    base = _unwrap_part(p)  # Part -> TextPart を剥がす
                    if getattr(base, "kind", None) == "text":
                        text = getattr(base, "text", None)
                        if text:
                            try:
                                return json.loads(text)
                            except Exception:
                                return {"status": "error", "reason": "invalid JSON from A2A", "raw": text}

            return {"status": "error", "reason": "empty A2A response"}

    # FunctionTool は async 関数をそのまま登録可能
    a2a_tool = FunctionTool(
        name="send_order_via_a2a",
        func=send_order_via_a2a,
        description="Send order spec to System2 via System1's A2A and return order result."
    )
    tools.append(a2a_tool)

    # Add memory search tool.
    tools.extend(build_memory_tools(user_memory))

    # Create Agent.
    agent = Agent(
        name=os.getenv("A2A_1_AGENT_NAME", "System1Agent"),
        system_message=(
            "You are System 1. Your role is as follows."
            "* Find for restaurants using the MCP server (find_restaurant)"
            "* Retrieve restaurant details using the MCP server (restaurant_detail)"
            "* Create order specifications (specs) according to user wish and budget."
            "* Send order details (spec) using the System2 A2A server (send_order_via_a2a)."
            "* Receive and display order details from System2."
            "## important"
            "* Before building specs, call `retrieve_memory` with queries like 'preference', 'budget', etc. "
            "* Use only what is returned (Top-K) as context for the user profile. Do NOT assume anything else.\n"
            "* If a restaurant item contains field `customer_tips`, summarize them as user-relevant hints and persist using `save_user_fact(tags=['customer_tips'])`. \n"
            "* After ANY call to restaurant finder tools (e.g., `find_michelin_restaurants` or `find_restaurants`), "
            "IMMEDIATELY call `persist_customer_tips` and pass the FULL JSON array from the tool. "
            "If you have the array object, call: persist_customer_tips(items=<THE_ARRAY>, tag='preference'). "
            "If you only have a JSON string, call: persist_customer_tips(items_json=<THE_JSON_STRING>, tag='preference'). "
            "Do not skip this step even if tips look irrelevant."
            "* Be sure to create a JSON-formatted **spec** before calling **send_order_via_a2a**.\n"
            "* **spec** must include the following keys: **task_id**, **requirements(wish, budget_jpy)**"
        ),
        description="Restaurant finder + order spec builder",
        tools=tools,
        model_client=model_client,
        memory=[],
    )
    return agent


async def run_user_chat(agent: Agent) -> None:
    user_proxy = UserProxyAgent("user_proxy", input_func=input)
    team = RoundRobinGroupChat([agent, user_proxy])
    stream = team.run_stream(task="")
    await Console(stream)


def main() -> None:
    agent = create_autogen_agent()
    anyio.run(run_user_chat, agent)

if __name__ == "__main__":
    print("Start")
    main()
