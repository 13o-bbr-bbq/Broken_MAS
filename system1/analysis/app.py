from fastmcp import FastMCP


def create_server() -> FastMCP:
    """Create and configure the MCP server for summarization."""
    mcp = FastMCP("analysis")

    @mcp.tool(
        name="summarize_text",
        description="Summarize the given text by returning the first 200 characters."
    )
    def summarize_text(text: str) -> str:
        """Summarize a text string by truncating it to 200 characters.

        The summarization logic here is deliberately simple for
        demonstration purposes.  A real implementation could call an
        LLM or another analysis pipeline.

        Args:
            text: The input string to summarize.

        Returns:
            The first 200 characters of the input string.
        """
        return text[:200]

    return mcp


def main() -> None:
    """Run the summarization MCP server."""
    mcp = create_server()
    # Bind to all interfaces on port 8080; FastMCP will handle JSON‑RPC over HTTP.
    mcp.run(transport="http", host="0.0.0.0", port=8080, path="/mcp")


if __name__ == "__main__":
    main()