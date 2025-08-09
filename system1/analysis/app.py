from fastmcp import FastMCP


def create_server() -> FastMCP:
    """Create and configure the MCP server for summarization."""
    mcp = FastMCP("analysis")

    @mcp.tool(
        name="summarize_text",
        description="Summarize the given text by returning the first 200 characters."
    )
    def summarize_text(text: str) -> str:
        return text[:200]

    return mcp


def main() -> None:
    """Run the summarization MCP server."""
    mcp = create_server()
    mcp.run(transport="http", host="0.0.0.0", port=8080, path="/mcp")


if __name__ == "__main__":
    main()