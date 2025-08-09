# a2a_server/app.py (System2)
import os
from typing import Optional
from fastmcp import Client as FastMCPClient
from python_a2a import A2AServer, A2AClient, agent as a2a_agent, skill, run_server


def create_a2a_server(peer_url: Optional[str]) -> A2AServer:
    # Create clients outside the class so they can be captured in the closures below.
    analysis_client = FastMCPClient(os.getenv("MCP_ANALYSIS_URL"))
    translation_client = FastMCPClient(os.getenv("MCP_TRANSLATION_URL"))

    # Set up A2A peer client if a URL is provided
    peer_client = A2AClient(peer_url) if peer_url else None

    # Define the A2A server as a class; python‑a2a uses class decorators
    # for metadata.  The name and description can be customised via
    # environment variables to distinguish between systems.
    @a2a_agent(
        name=os.getenv("A2A_AGENT_NAME", "System2A2A"),
        description="A2A interface for System2 agent"
    )
    class System2A2A(A2AServer):
        """A2A server exposing skills for System2."""

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
    return System2A2A()


def main():
    peer_url = os.getenv("PEER_A2A_URL")
    server = create_a2a_server(peer_url)
    port = int(os.getenv("A2A_PORT", "9000"))
    run_server(server, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
