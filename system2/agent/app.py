"""
AutoGen agent and A2A server for System 2

System 2’s agent registers the same two MCP tools (analysis and
translation) via AutoGen.  Its A2A skills provide a local
`reverse_text` skill that calls the translation MCP server and a
`summarize_text_remote` skill that delegates to the peer agent in
System 1.
"""

import os
import asyncio
from typing import Optional

from autogen_agentchat.agents import AssistantAgent as Agent
from autogen_ext.tools.mcp import mcp_server_tools, StreamableHttpServerParams
from autogen_ext.models.openai import OpenAIChatCompletionClient
from fastmcp import Client as FastMCPClient
from python_a2a import A2AServer, A2AClient
from python_a2a import agent as a2a_agent, skill, run_server


def create_autogen_agent() -> Agent:
    # Create model client.
    model_client = OpenAIChatCompletionClient(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        api_key=os.getenv("OPENAI_API_KEY")
    )

    # Asynchronously retrieve tools from the MCP server.
    analysis_params = StreamableHttpServerParams(url=os.getenv("MCP_ANALYSIS_URL"))
    translation_params = StreamableHttpServerParams(url=os.getenv("MCP_TRANSLATION_URL"))
    analysis_tools = asyncio.run(mcp_server_tools(analysis_params))
    translation_tools = asyncio.run(mcp_server_tools(translation_params))

    # Create Agent.
    agent = Agent(
        name="System2Agent",
        system_message="You are an AutoGen agent. Use available tools to complete the user's request.",
        description = "This agent is using summarization and reverse_text on MCP Server",
        model_client=model_client,
        tools=analysis_tools + translation_tools,
    )

    return agent


def create_a2a_server(peer_url: Optional[str]) -> A2AServer:
    """Create an A2A server exposing System2's skills."""
    analysis_client = FastMCPClient(os.getenv("MCP_ANALYSIS_URL"))
    translation_client = FastMCPClient(os.getenv("MCP_TRANSLATION_URL"))

    peer_client = A2AClient(peer_url) if peer_url else None

    @a2a_agent(
        name=os.getenv("A2A_AGENT_NAME", "System2A2A"),
        description="A2A interface for System2 agent"
    )
    class System2A2A(A2AServer):
        """A2A server exposing skills for System2."""

        def __init__(self) -> None:
            super().__init__()
            self.peer_client = peer_client

        @skill(
            name="reverse_text",
            description="Reverse a string via the local MCP translation tool.",
            tags=["reverse", "text"]
        )
        def reverse_text(self, text: str) -> str:
            return translation_client.invoke_tool("reverse_text", text=text)

        @skill(
            name="summarize_text_remote",
            description=(
                "Summarise a string by delegating to the peer system via A2A. "
                "Requires PEER_A2A_URL to be set."
            ),
            tags=["summarise", "remote"]
        )
        def summarize_text_remote(self, text: str) -> str:
            if not self.peer_client:
                raise RuntimeError(
                    "Peer A2A URL not configured; cannot call remote summarise skill."
                )
            return self.peer_client.call_skill("summarize_text", text=text)

    return System2A2A()


def main() -> None:
    # Create the AutoGen agent to register its tools
    _agent = create_autogen_agent()
    peer_url = os.getenv("PEER_A2A_URL")
    server = create_a2a_server(peer_url)
    port = int(os.getenv("A2A_PORT", "9000"))
    run_server(server, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()