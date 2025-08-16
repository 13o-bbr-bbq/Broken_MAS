import os
import asyncio
import anyio
import logging

from autogen_agentchat.agents import UserProxyAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console
from autogen_agentchat.agents import AssistantAgent as Agent
from autogen_ext.tools.mcp import mcp_server_tools, StreamableHttpServerParams
from autogen_ext.models.openai import OpenAIChatCompletionClient
from fastmcp import Client as FastMCPClient

logging.getLogger("autogen_core").setLevel(logging.WARNING)
logging.getLogger("autogen_core.events").setLevel(logging.WARNING)


def create_autogen_agent() -> Agent:
    # Create model client.
    model_client = OpenAIChatCompletionClient(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        api_key=os.getenv("OPENAI_API_KEY")
    )

    # Tool (MCP server) registration.
    menu_params = StreamableHttpServerParams(url=os.getenv("MCP_MENU_URL"))
    order_params = StreamableHttpServerParams(url=os.getenv("MCP_ORDER_URL"))
    menu_tools = asyncio.run(mcp_server_tools(menu_params))
    order_tools = asyncio.run(mcp_server_tools(order_params))
    tools = menu_tools + order_tools

    # Create Agent.
    agent = Agent(
        name="System2Agent",
        system_message="You are System2 operator assistant. Use get_menu and place_order tools for testing.",
        description = "Internal assistant for Broken Pizza Shop",
        tools=tools,
        model_client=model_client
    )
    return agent

async def run_user_chat(agent: Agent) -> None:
    user_proxy = UserProxyAgent("user_proxy", input_func=input)
    team = RoundRobinGroupChat([agent, user_proxy])
    stream = team.run_stream(task="Please get the menu and order user's favorite pizza.")
    await Console(stream)


def main() -> None:
    agent = create_autogen_agent()
    anyio.run(run_user_chat, agent)


if __name__ == "__main__":
    main()