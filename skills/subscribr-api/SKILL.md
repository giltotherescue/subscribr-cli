---
name: subscribr-api
description: "Use Subscribr's REST API and CLI for YouTube content creation, research, and automation. Covers authentication, all endpoint groups (scripts, channels, Intel, ideas, thumbnails, bookmarks, webhooks), and common workflows. Also documents the MCP server for research-only use in AI chat clients.\n\nTriggers on: Subscribr API calls, YouTube data lookups, Intel search, channel/script/video management via Subscribr, script generation or export, webhook setup, or any request involving subscribr.ai endpoints."
---

# Subscribr REST API — AI Agent Reference

You are helping a developer or creator who uses **Subscribr**, a YouTube content creation platform. This skill teaches you how to authenticate, call API endpoints, and build workflows on their behalf.

## Quick Reference

| Property | Value |
|---|---|
| **Base URL** | `https://subscribr.ai` |
| **API Prefix** | `/api/v1/` |
| **Auth** | Bearer token (Personal Access Token or OAuth 2.1) |
| **Rate Limit** | 120 requests/minute |
| **Full API Reference** | `https://subscribr.ai/api/docs/reference/ai` (plain text, LLM-optimized) |
| **Plan Required** | Automation or higher for REST API |

## Before You Start

**Always fetch the full API reference first.** The plain-text docs at the URL above contain every endpoint, parameter, response shape, and error code. Use `WebFetch` or `curl` to retrieve it:

```
curl -s https://subscribr.ai/api/docs/reference/ai
```

This skill provides orientation and common patterns. The full reference is the source of truth for endpoint details.

## Authentication

All API requests require a bearer token in the `Authorization` header:

```
Authorization: Bearer <token>
```

**How to get a token:**
1. Go to https://subscribr.ai/developer
2. Click "Create Token" in the Access Tokens tab
3. Copy the token — it's shown once

**Plan requirements:**
- REST API access requires **Automation or Scale** plan
- MCP server access requires **Creator, Automation, or Scale** plan
- Intel endpoints (search channels/videos, lookups) require **Creator or higher**
- Write actions require **editor access or higher** on the team

## Subscribr CLI

This repo includes `subscribr.py`, a zero-dependency Python CLI that wraps every API endpoint. If it's available in the project, prefer using it over raw `curl` — it handles auth, path params, JSON parsing, and polling automatically.

```bash
# Set your token
export SUBSCRIBR_API_TOKEN=sk_live_...

# List domains and actions
subscribr help
subscribr scripts help

# Call any endpoint
subscribr channels list
subscribr scripts create --channel_id 42 --title "My Video" --topic "..." --length 1500
subscribr intel lookup-channels --body '{"identifiers": ["@mkbhd"]}'
```

Path parameters (shown as `{name}` in routes) are passed via `--name value`. Extra `--key value` args become query params (GET) or JSON body fields (POST). Use `--body '...'` for complex JSON payloads.

## REST API Endpoints

The REST API covers the **complete Subscribr platform** — research, content creation, automation, and infrastructure.

### Endpoint Groups

- **Team** — `/api/v1/team` — Account info, credits, plan status
- **Channels** — `/api/v1/channels` — List/get channels, templates, voice profiles, competitors
- **Scripts** — `/api/v1/scripts` — Full script lifecycle: create, outline, generate, humanize, export
- **Ideas** — `/api/v1/channels/{id}/ideas` — Create, batch generate, generate from video/channel references
- **Intel Search** — `/api/v1/intel/channels/search`, `/api/v1/intel/videos/search` — AI-powered YouTube research
- **Intel Lookups** — `/api/v1/intel/channels/lookup`, `/api/v1/intel/videos/lookup` — Look up YouTube channels or videos
- **Bookmarks** — `/api/v1/intel/bookmarks` — Saved channels and videos
- **Thumbnails** — `/api/v1/channels/{id}/thumbnails` — AI thumbnail generation (brainstorm, clone, improve)
- **Webhooks** — `/api/v1/webhooks` — Full CRUD for webhook endpoints and real-time event notifications

## Common Workflows

### Write a script

```bash
# 1. Create script
subscribr scripts create --channel_id 42 --title "Title" --topic "Topic here" --length 1500
# → returns script_id

# 2. Generate outline (async)
subscribr scripts generate-outline --script_id 123
# → returns run_id

# 3. Poll until complete
subscribr scripts poll --script_id 123 --run_id abc123

# 4. Generate full script (requires outline)
subscribr scripts generate --script_id 123
# → returns new run_id — poll again

# 5. Optional: humanize the script
subscribr scripts humanize --script_id 123
# → returns run_id — poll again

# 6. Export
subscribr scripts export --script_id 123 --format markdown
```

### Research a niche

```bash
# Search for channels by topic
subscribr intel search-channels --body '{"query": "personal finance", "limit": 20}'

# Find outlier videos
subscribr intel search-videos --body '{"query": "how to invest in index funds", "limit": 10}'

# Look up specific channels
subscribr intel lookup-channels --body '{"identifiers": ["@mkbhd", "@linustechtips"]}'

# Save interesting finds
subscribr bookmarks add --type channel --external_id UCBcRF18a7Qf58cCRy5xuWwQ --title "MKBHD"
```

### Generate video ideas

```bash
# List channels to get IDs
subscribr channels list

# Generate AI-powered ideas
subscribr ideas generate --channel_id 42

# Generate ideas from a competitor video
subscribr ideas generate-from-video --channel_id 42 --video_url https://youtube.com/watch?v=VIDEO_ID

# Convert an idea to a script
subscribr ideas to-script --idea_id 789
```

### Generate thumbnails

```bash
# Check remaining quota
subscribr thumbnails usage

# Brainstorm concept sketches
subscribr thumbnails create --channel_id 42 --prompt "How I Made $1M on YouTube" --topic "Revenue breakdown" --num_variations 4
# → returns run_id

# Poll until complete
subscribr thumbnails get --channel_id 42 --run_id RUN_ID
# → when completed: output_urls[]

# Clone a thumbnail style from a reference
subscribr thumbnails create --channel_id 42 --prompt "My Video Title" --clone_strategy style_analysis --reference_image_url "https://..."
```

### Set up webhooks

```bash
# Create a webhook
subscribr webhooks create --body '{"url": "https://example.com/webhook", "events": ["script.generated", "idea.created"]}'

# Test it
subscribr webhooks test --webhook_id WEBHOOK_ID

# List existing webhooks
subscribr webhooks list
```

## MCP Server (Research & Ideation)

Subscribr also offers an **MCP server** for use inside AI chat clients like Claude, ChatGPT, and Cursor. The MCP server is a **subset** of the REST API — it supports research, ideation, and reviewing content, but **not** content creation or generation.

**MCP Server URL:** `https://subscribr.ai/mcp/subscribr`
**Plan Required:** Creator, Automation, or Scale

### What MCP Can Do
- Read your channels, scripts, videos, ideas, and bookmarks
- Search Intel for YouTube channels and outlier videos
- Look up any YouTube channel or video
- Save bookmarks and generate video ideas

### What MCP Cannot Do (Use the REST API Instead)
- Create or generate scripts (outlines, full scripts)
- Export scripts to any format
- Generate thumbnails
- Manage competitors or templates
- Create or manage webhooks

### MCP Tools Reference

| Tool | What It Does |
|------|-------------|
| `my_team` | Team info, plan, credits |
| `my_channels` | List your channels |
| `get_my_channel` | Details for a specific channel |
| `my_scripts` | List scripts (filterable, searchable) |
| `get_my_script` | Full script content by ID |
| `my_videos` | Videos from linked YouTube channels |
| `get_my_video` | Video details + optional transcript |
| `get_my_channel_ideas` | Ideas for a channel (filterable by status) |
| `my_bookmarks` | Saved bookmarks (filterable by type) |
| `intel_search_channels` | Search 100K+ YouTube channels |
| `intel_search_videos` | Find outlier videos by topic |
| `get_youtube_channel` | Look up a channel by handle/URL/ID |
| `get_youtube_video` | Look up a video by URL/ID |
| `add_bookmark` | Save a channel or video |
| `generate_ideas` | Generate AI video ideas |

### MCP Setup for CLI Agents

```json
{
  "mcpServers": {
    "subscribr": {
      "url": "https://subscribr.ai/mcp/subscribr",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN_HERE"
      }
    }
  }
}
```

## Error Handling

- **401 Unauthorized** — Token is missing, expired, or invalid. Create a new one at https://subscribr.ai/developer
- **403 Forbidden** — Feature requires a higher plan or editor+ role
- **404 Not Found** — Resource doesn't exist or belongs to a different team
- **422 Unprocessable** — Validation error. Check the `errors` object in the response
- **429 Too Many Requests** — Rate limit exceeded (120/min). Back off and retry

## Important Notes

- All data is scoped to the authenticated user's team. You cannot access other teams' data.
- Write actions require **editor access or higher** on the team.
- Intel tools require a **Creator or higher** plan.
- When in doubt, call `subscribr team get` or the `my_team` MCP tool first to check the user's plan and permissions.

## Getting the Full Reference

For complete endpoint documentation with all parameters, response schemas, pagination details, and code examples:

```
curl -s https://subscribr.ai/api/docs/reference/ai
```

This returns a comprehensive plain-text reference optimized for LLM consumption.
