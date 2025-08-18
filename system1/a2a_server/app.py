import os
import json
import uuid
import uvicorn
import httpx
from typing import Any

# A2A.
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    AgentCard,
    AgentSkill,
    AgentCapabilities,
    MessageSendParams,
    SendMessageRequest,
)
from a2a.client import A2ACardResolver, A2AClient
from a2a.utils.message import new_agent_text_message


class OrderProxyExecutor(AgentExecutor):
    """Transfer the received specifications (JSON) to System2's A2A and return the response as is."""

    def __init__(self, peer_base_url: str):
        self.peer_base_url = peer_base_url

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Transfer input to System 1 (text part JSON) to System 2 as is.
        parts = getattr(context.message, "parts", []) or []
        print(f"System1: from Agent's parts: {parts}, {type(parts)}")

        def unwrap(x):
            # Extract from "Part(root=...), RootModel(__root__/root=...)"
            if hasattr(x, "root"):
                return x.root
            if hasattr(x, "__root__"):
                return x.__root__
            return x

        text = None
        for p in parts:
            base = unwrap(p)
            if getattr(base, "kind", None) == "text":
                text = getattr(base, "text", None)
                if text:
                    break

        if text is None:
            raise ValueError("No text part found in message parts.")
        in_json = json.loads(text)
        print(f"System1: from Agent's in_json: {in_json}, {type(in_json)}")

        # Resolve the AgentCard in System2 and execute message/send.
        async with httpx.AsyncClient() as httpx_client:
            # Create A2A rsolver.
            resolver = A2ACardResolver(
                httpx_client=httpx_client,
                base_url=self.peer_base_url
            )

            # Get "/.well-known/agent-card.json" on System2 A2A Server.
            system2_card = await resolver.get_agent_card()

            # Create A2A Client.
            client = A2AClient(
                httpx_client=httpx_client,
                agent_card=system2_card
            )

            # Send message.
            send_message_payload: dict[str, Any] = {
                "message": {
                    "role": "user",
                    "parts": [
                        {"kind": "text", "text": json.dumps(in_json, ensure_ascii=False)}
                    ],
                    "messageId": uuid.uuid4().hex,
                }
            }
            request = SendMessageRequest(
                id=str(uuid.uuid4()),
                params=MessageSendParams(**send_message_payload)
            )
            response = await client.send_message(request)
            model_dump = response.model_dump(mode='json', exclude_none=True)

        parts = (model_dump.get("result") or {}).get("parts") or []
        result_json_text = next((p.get("text") for p in parts if isinstance(p, dict) and p.get("kind") == "text" and p.get("text")), "")
        if result_json_text:
            print(f"System1: from System2 A2A Server's ordered spec: {result_json_text}, {type(result_json_text)}")
            await event_queue.enqueue_event(new_agent_text_message(result_json_text))
        else:
            print("System1: from System2 A2A Server's order error.")
            error_payload = {"status": "error", "reason": "empty A2A response"}
            await event_queue.enqueue_event(new_agent_text_message(json.dumps(error_payload, ensure_ascii=False)))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


def build_agent_card() -> AgentCard:
    skill = AgentSkill(
        id="send_order_spec_to_system2",
        name="Send order spec to System2",
        description="Proxy: forward order spec JSON to System2 A2A and return the result.",
        tags=["proxy", "order"],
        examples=['{"task_id":"t-123","requirements":{"wish":"margherita","budget_jpy":2000}}'],
    )
    return AgentCard(
        name=os.getenv("A2A_1_AGENT_NAME", "System1A2A"),
        description="A2A interface for System1 (proxy to System2)",
        url=os.getenv("A2A_1_SERVER"),
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )


def main():
    server = A2AStarletteApplication(
        agent_card=build_agent_card(),
        http_handler=DefaultRequestHandler(
            agent_executor=OrderProxyExecutor(peer_base_url=os.getenv("PEER_A2A_2_URL")),
            task_store=InMemoryTaskStore(),
        ),
    )
    uvicorn.run(server.build(), host="0.0.0.0", port=int(os.getenv("A2A_1_PORT", "9000")))


if __name__ == "__main__":
    main()
