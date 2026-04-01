# Subscribr CLI

CLI and AI agent skill for the [Subscribr YouTube API](https://subscribr.ai/youtube-api). Built for developers, creators, and AI agents that want to automate YouTube content workflows — research competitors, generate video ideas, write scripts with AI, create thumbnails, and manage webhooks.

Works with [Claude Code](https://claude.ai/claude-code), [OpenAI Codex](https://developers.openai.com/codex/skills/), [Cursor](https://cursor.com/docs/skills), and any agent that supports the [Agent Skills spec](https://agentskills.io).

New to Subscribr? [See what the API can do](https://subscribr.ai/youtube-api) or check out the [full API reference](https://subscribr.ai/youtube-api/reference).

## What's in This Repo

| File | What It Does |
|------|-------------|
| `skills/subscribr-api/SKILL.md` | AI agent skill — teaches coding agents how to use the Subscribr API |
| `subscribr.py` | Standalone Python CLI — wraps every API endpoint, zero dependencies |

## What is a Skill?

A skill is a markdown file that gives AI coding agents specialized knowledge. When you install the Subscribr skill, your agent learns how to authenticate, call endpoints, handle async polling, and chain together workflows like "research a niche and write a script" — without you spelling out every API call.

The skill follows the [Agent Skills spec](https://agentskills.io/specification) and is compatible with Claude Code, Codex, Cursor, and other agents.

## Installation

### Option 1: npx (Recommended)

```bash
npx @subscribr/cli
```

Installs the skill into both `.agents/skills/` (Codex, Cursor) and `.claude/skills/` (Claude Code).

To also install the Python CLI:

```bash
npx @subscribr/cli --with-cli
```

### Option 2: curl

```bash
# For Codex / Cursor / Agent Skills spec
mkdir -p .agents/skills/subscribr-api
curl -sL https://raw.githubusercontent.com/giltotherescue/subscribr-cli/main/skills/subscribr-api/SKILL.md \
  -o .agents/skills/subscribr-api/SKILL.md

# For Claude Code
mkdir -p .claude/skills/subscribr-api
curl -sL https://raw.githubusercontent.com/giltotherescue/subscribr-cli/main/skills/subscribr-api/SKILL.md \
  -o .claude/skills/subscribr-api/SKILL.md
```

### Option 3: Clone

```bash
git clone https://github.com/giltotherescue/subscribr-cli.git
cp -r subscribr-cli/skills/subscribr-api .agents/skills/
cp -r subscribr-cli/skills/subscribr-api .claude/skills/  # Claude Code
```

## Setup

1. **Get a token** at [subscribr.ai/developer](https://subscribr.ai/developer) (requires Automation or Scale plan)
2. **Set it in your environment:**

```bash
export SUBSCRIBR_API_TOKEN=sk_live_your_token_here
```

## Usage with AI Agents

Once the skill is installed, ask your agent to do Subscribr tasks in natural language:

```
"Research the top personal finance YouTube channels"
→ Uses Intel search to find channels and outlier videos

"Generate 10 video ideas for my channel"
→ Lists your channels, picks one, generates AI-powered ideas

"Write a 1500-word script about index fund investing"
→ Creates a script, generates an outline, then writes the full draft

"Export my latest script as markdown"
→ Finds recent scripts and exports the content

"Create a thumbnail for my video about investing"
→ Generates AI thumbnail concepts with brainstorm mode

"Set up a webhook to notify me when scripts are done"
→ Creates a webhook subscription for the script.generated event
```

The skill handles authentication, endpoint selection, async polling, and error handling automatically.

## CLI Reference

The CLI wraps all 45 Subscribr API endpoints. Requires Python 3.7+ with zero external dependencies.

```
subscribr <domain> <action> [--key value ...]
```

### Domains

| Domain | Actions | Description |
|--------|---------|-------------|
| team | 2 | Account info and credit balance |
| channels | 7 | Channel details, templates, voices, competitors |
| intel | 4 | YouTube channel/video lookup and search |
| bookmarks | 3 | Saved YouTube channels and videos |
| ideas | 8 | Video ideas: list, create, generate, convert to script |
| scripts | 11 | Scripts: create, outline, generate, humanize, export |
| thumbnails | 4 | AI thumbnail generation: brainstorm, clone, improve |
| webhooks | 6 | Webhook CRUD and testing |

Run `subscribr <domain> help` to see all actions and required parameters.

### Examples

```bash
# List your channels
subscribr channels list

# Search YouTube for channels in a niche
subscribr intel search-channels --body '{"query": "personal finance", "limit": 20}'

# Generate video ideas
subscribr ideas generate --channel_id 42

# Create and generate a script
subscribr scripts create --channel_id 42 --title "My Video" --topic "How to invest" --length 1500
subscribr scripts generate-outline --script_id 123
subscribr scripts poll --script_id 123 --run_id abc123
subscribr scripts generate --script_id 123
subscribr scripts export --script_id 123 --format markdown

# Generate a thumbnail
subscribr thumbnails create --channel_id 42 --prompt "Investing in 2026" --num_variations 4
```

### Complex JSON Payloads

For endpoints that need arrays or nested objects, use `--body`:

```bash
subscribr intel lookup-channels --body '{"identifiers": ["@mkbhd", "@linustechtips"]}'
subscribr intel search-videos --body '{"query": "AI productivity tools", "limit": 15}'
subscribr webhooks create --body '{"url": "https://example.com/hook", "events": ["script.generated"]}'
```

## MCP Server

For conversational AI clients (Claude Desktop, ChatGPT, Cursor), Subscribr also offers an [MCP server](https://subscribr.ai/help/advanced/mcp-integration) for research and ideation. The MCP server is complementary to this CLI — it handles research and reading, while the CLI/API handles creation, generation, and automation.

## API Documentation

- **[YouTube API overview](https://subscribr.ai/youtube-api)** — what the API can do, use cases, and getting started
- **[API reference](https://subscribr.ai/youtube-api/reference)** — full endpoint docs with examples
- **[API reference for AI agents](https://subscribr.ai/api/docs/reference/ai)** — LLM-optimized plain text:
  ```bash
  curl -s https://subscribr.ai/api/docs/reference/ai
  ```

## Contributing

Found a bug or want to add an endpoint? [Open an issue](https://github.com/giltotherescue/subscribr-cli/issues) or submit a PR.

## License

[MIT](LICENSE)
