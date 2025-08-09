from fastmcp import FastMCP


def create_server() -> FastMCP:
    """Create and configure the MCP server for reversing text."""
    mcp = FastMCP("translation")

    @mcp.tool(
        name="reverse_text",
        description="Reverse the characters in the given text."
    )
    def reverse_text(text: str) -> str:
        """Reverse a string.

        Args:
            text: The input string to reverse.

        Returns:
            The reversed string.
        """
        return text[::-1]

    return mcp


def main() -> None:
    """Run the reverse‑text MCP server."""
    mcp = create_server()
    mcp.run(transport="http", host="0.0.0.0", port=8080, path="/mcp")


if __name__ == "__main__":
    main()