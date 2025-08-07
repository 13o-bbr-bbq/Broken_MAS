"""
FastMCP server for System 2: Analysis service

This server is identical to System 1's analysis server.  It exposes a
`summarize_text` tool that truncates the input string to the first
200 characters.
"""

from fastmcp import FastMCP


def create_server() -> FastMCP:
    mcp = FastMCP("analysis")

    @mcp.tool(
        name="summarize_text",
        description="Summarize the given text by returning the first 200 characters."
    )
    def summarize_text(text: str) -> str:
        return text[:200]

    return mcp


def main() -> None:
    mcp = create_server()
    mcp.run(transport="http", host="0.0.0.0", port=8080, path="/mcp")


if __name__ == "__main__":
    main()