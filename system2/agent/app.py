import os
import asyncio
import anyio
from typing import Optional

from autogen_agentchat.agents import UserProxyAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console
from autogen_agentchat.agents import AssistantAgent as Agent
from autogen_ext.tools.mcp import mcp_server_tools, StreamableHttpServerParams
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.tools import FunctionTool
from python_a2a import A2AClient


def create_autogen_agent(peer_url: Optional[str]) -> Agent:
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
    tools = analysis_tools + translation_tools

    if peer_url:
        a2a_client = A2AClient(peer_url)

        def reverse_remote(text: str) -> str:
            """Call peer’s reverse_text skill over A2A."""
            return a2a_client.call_skill("reverse_text", text=text)

        a2a_tool = FunctionTool(
            name="reverse_remote",
            func=reverse_remote,
            description="Reverse text by delegating to peer via A2A."
        )
        tools.append(a2a_tool)

    # Create Agent.
    agent = Agent(
        name="System2Agent",
        system_message="You are an AutoGen agent. Use available tools to complete the user's request.",
        description = "This agent is using summarization and reverse_text on MCP Server",
        tools=tools,
        model_client=model_client
    )

    return agent


async def run_user_chat(agent: Agent) -> None:
    user_proxy = UserProxyAgent("user_proxy", input_func=input)
    team = RoundRobinGroupChat([agent, user_proxy], max_turns=1)
    stream = team.run_stream(task="")
    await Console(stream)


def main() -> None:
    peer_url = os.getenv("PEER_A2A_URL")
    agent = create_autogen_agent(peer_url)
    anyio.run(run_user_chat, agent)


if __name__ == "__main__":
    main()