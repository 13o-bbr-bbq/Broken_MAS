"""
FastMCP server for System 2: Translation service

This server exposes the same `reverse_text` tool as System 1.  Having
both systems expose the same tool allows us to demonstrate delegating
calls between agents using A2A.
"""

from fastmcp import FastMCP


def create_server() -> FastMCP:
    mcp = FastMCP("translation")

    @mcp.tool(
        name="reverse_text",
        description="Reverse the characters in the given text."
    )
    def reverse_text(text: str) -> str:
        return text[::-1]

    return mcp


def main() -> None:
    mcp = create_server()
    mcp.run(transport="http", host="0.0.0.0", port=8080, path="/mcp")


if __name__ == "__main__":
    main()