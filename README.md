# Multi-Agent System Implementation with MCP and A2A

This repository contains an example of how to build two independent AI agent
systems that each use [AutoGen](https://github.com/microsoft/autogen) as the
agent framework, [FastMCP](https://github.com/jlowin/fastmcp) for exposing
tooling via the Model Context Protocol (MCP), and
[python‑a2a](https://github.com/themanojdesai/python-a2a) for Agent‑to‑Agent
(A2A) communication.  Each agent system is fully containerised with Docker.

## Architecture

There are two identical agent systems (system 1 and system 2).  Each
system runs the following three services:

* **Two MCP servers**: one providing a `summarize_text` tool and the other
  providing a `reverse_text` tool.  These servers are implemented with
  FastMCP and are reachable by the agent via HTTP inside the Docker
  network.
* **An AutoGen agent**: this is the LLM‑driven agent that registers both
  local MCP servers as tools using the `autogen-ext[mcp]` package.  The
  agent also runs an A2A server so that other agents can call its
  skills.  Skills in the A2A server delegate to the appropriate local
  MCP tool or forward the request to the peer agent via its A2A
  endpoint.

Within each system, the agent knows which MCP server to call because
their URLs are injected through environment variables.  Upon startup
the agent creates `FastMCPClient` instances for each MCP server and
wraps them with `McpSessionActor` so that AutoGen can invoke them.  For
inter‑system communication, the agent also initialises an
`A2AClient` using the peer agent’s A2A URL; this URL is supplied via
environment variables as well.  According to the `python‑a2a`
documentation, agents can discover and call skills offered by other
agents through the `A2AClient` interface【464873938173021†L485-L495】.  The
`python‑a2a` library implements the official Agent‑to‑Agent protocol
with full MCP integration and enables seamless communication between AI
agents【464873938173021†L485-L495】.

## Running the system

> **Prerequisites**
>
> * Docker 19.03 or later
> * A working internet connection to download Python packages during
>   image builds

To start both systems, run the following command from the
`multi_agent_system` directory:

```bash
docker compose -f docker-compose.yml --project-name myagents up --build -d
```

This spins up six containers: `system1_mcp_analysis`,
`system1_mcp_translation`, `system1_agent`, and their counterparts for
system 2.  Each agent exposes an A2A server on a unique host port, and
the MCP servers are mapped to separate host ports as well.  You can see
all running services with `docker compose ps`.  To stop the stack, run
`docker compose down`.

## Usage

Once the containers are running, you can interact with the agent
systems via their A2A interfaces.  For example, you can send a
JSON‑RPC request to the system 1 agent to summarise a piece of text and
then ask it to reverse the summarised text using the peer system:

```bash
# Summarise text using system 1's local MCP server
curl -X POST http://localhost:9001/v1/skills/summarize_text \
    -H "Content-Type: application/json" \
    -d '{"text": "Hello from Tokyo. Welcome to the agent systems!"}'

# Reverse text via system 1, which delegates to system 2's reverse skill
curl -X POST http://localhost:9001/v1/skills/reverse_text_remote \
    -H "Content-Type: application/json" \
    -d '{"text": "Hello"}'

# Likewise, you can call system 2's summarise_text_remote skill, which
# forwards to system 1
curl -X POST http://localhost:9011/v1/skills/summarize_text_remote \
    -H "Content-Type: application/json" \
    -d '{"text": "The quick brown fox jumps over the lazy dog."}'
```

The above curl calls demonstrate how each agent system can leverage
both its local MCP tools and the tools exposed by its peer via A2A.
