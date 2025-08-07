"""
AutoGen agent and A2A server for System1

This module defines a combined AutoGen agent and A2A server.  The
AutoGen agent registers two MCP tools – summarization and reverse text –
and the A2A server exposes skills that either call those local tools or
delegate to the peer agent via A2A.  Configuration values (such as
service URLs) are provided through environment variables.
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
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key = os.getenv("OPENAI_API_KEY")
    )

    # Asynchronously retrieve tools from the MCP server.
    analysis_params = StreamableHttpServerParams(url=os.getenv("MCP_ANALYSIS_URL"))
    translation_params = StreamableHttpServerParams(url=os.getenv("MCP_TRANSLATION_URL"))
    analysis_tools = asyncio.run(mcp_server_tools(analysis_params))
    translation_tools = asyncio.run(mcp_server_tools(translation_params))

    # Create Agent.
    agent = Agent(
        name="System1Agent",
        system_message="You are an AutoGen agent. Use available tools to complete the user's request.",
        description="This agent is using summarization and reverse_text on MCP Server",
        tools=analysis_tools + translation_tools,
        model_client=model_client
    )

    return agent


def create_a2a_server(peer_url: Optional[str]) -> A2AServer:
    """Create the A2A server and define its skills.

    Args:
        peer_url: The base URL of the peer agent’s A2A server.  If not
            provided, skills that delegate to the peer will raise an
            exception when called.

    Returns:
        An A2AServer subclass instance ready to be passed to run_server().
    """
    # Create clients outside the class so they can be captured in the
    # closures below.
    analysis_client = FastMCPClient(os.getenv("MCP_ANALYSIS_URL"))
    translation_client = FastMCPClient(os.getenv("MCP_TRANSLATION_URL"))

    # Set up A2A peer client if a URL is provided
    peer_client = A2AClient(peer_url) if peer_url else None

    # Define the A2A server as a class; python‑a2a uses class decorators
    # for metadata.  The name and description can be customised via
    # environment variables to distinguish between systems.
    @a2a_agent(
        name=os.getenv("A2A_AGENT_NAME", "System1A2A"),
        description="A2A interface for System1 agent"
    )
    class System1A2A(A2AServer):
        """A2A server exposing skills for System1."""

        def __init__(self):
            super().__init__()
            # Attach the peer client as an instance attribute so it’s available inside skills.
            self.peer_client = peer_client

        @skill(
            name="summarize_text",
            description="Summarise a string using the local MCP analysis tool.",
            tags=["summarise", "text"]
        )
        def summarize_text(self, text: str) -> str:  # noqa: D401
            """A2A skill that summarises text via the local MCP server."""
            return analysis_client.invoke_tool("summarize_text", text=text)

        @skill(
            name="reverse_text_remote",
            description=(
                "Reverse a string by delegating to the peer system via A2A. "
                "Requires PEER_A2A_URL to be set."
            ),
            tags=["reverse", "remote"]
        )
        def reverse_text_remote(self, text: str) -> str:  # noqa: D401
            """A2A skill that forwards a reverse request to the peer system."""
            if not self.peer_client:
                raise RuntimeError(
                    "Peer A2A URL not configured; cannot call remote reverse skill."
                )
            # Call the peer agent’s reverse_text skill.
            return self.peer_client.call_skill("reverse_text", text=text)

    # Instantiate and return the server
    return System1A2A()


def main() -> None:
    """Entry point for running the A2A server."""
    # The AutoGen agent is created here to ensure its tools are
    # registered; however, this example does not start an interactive
    # chat loop.  The agent instance is kept alive so that tasks using
    # `agent.run()` could be added in the future.
    _agent = create_autogen_agent()

    # Determine the peer A2A endpoint (if any).  The peer URL must
    # include the protocol and port, e.g. http://system2_agent:9000.
    peer_url = os.getenv("PEER_A2A_URL")
    server = create_a2a_server(peer_url)
    # Determine the port on which to expose this A2A server.
    port = int(os.getenv("A2A_PORT", "9000"))
    run_server(server, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
