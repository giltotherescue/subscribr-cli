#!/usr/bin/env python3
"""subscribr — CLI for the Subscribr YouTube API.

Interact with the Subscribr REST API from your terminal or AI agent.
Covers channels, ideas, scripts (including Agent Mode), Intel research,
thumbnails, bookmarks, and webhooks.

Usage:
    subscribr <domain> <action> [--key value ...]
    subscribr <domain> <action> --body '{"json": "data"}'
    subscribr help                    # list domains
    subscribr <domain> help           # list actions in domain

Auth:
    Set the SUBSCRIBR_API_TOKEN environment variable to your
    Personal Access Token (sk_live_...).
    Create one at https://subscribr.ai/developer

Arguments:
    Path params ({name} in route) are filled from --name value.
    Remaining args: query params for GET/DELETE, JSON body for POST.
    Use --body '...' to pass a raw JSON body for complex payloads.
    Values that look like JSON (arrays/objects) are auto-parsed.

Examples:
    subscribr channels list
    subscribr scripts create --channel_id 42 --title "My Video" --topic "..." --length 1500
    subscribr scripts agent-generate --script_id 123
    subscribr scripts agent-poll --script_id 123 --run_id 42
    subscribr intel lookup-channels --body '{"identifiers": ["@mkbhd"]}'
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://subscribr.ai/api/v1"

# ---------------------------------------------------------------------------
# Route table: "domain.action" -> (METHOD, path_template)
# ---------------------------------------------------------------------------
ROUTES = {
    # ── Team ──────────────────────────────────────────────────────────────
    "team.get":         ("GET", "/team"),
    "team.get-credits": ("GET", "/team/credits"),

    # ── Channels ──────────────────────────────────────────────────────────
    "channels.list":             ("GET",    "/channels"),
    "channels.get":              ("GET",    "/channels/{channel_id}"),
    "channels.list-templates":   ("GET",    "/channels/{channel_id}/templates"),
    "channels.list-voices":      ("GET",    "/channels/{channel_id}/voices"),
    "channels.list-competitors": ("GET",    "/channels/{channel_id}/competitors"),
    "channels.add-competitor":   ("POST",   "/channels/{channel_id}/competitors"),
    "channels.delete-competitor":("DELETE", "/channels/{channel_id}/competitors/{competitor_id}"),

    # ── YouTube Intel — Channel ────────────────────────────────────────────
    "intel.lookup-channels": ("POST", "/intel/channels/lookup"),
    "intel.search-channels": ("POST", "/intel/channels/search"),

    # ── YouTube Intel — Video ──────────────────────────────────────────────
    "intel.lookup-videos": ("POST", "/intel/videos/lookup"),
    "intel.search-videos": ("POST", "/intel/videos/search"),

    # ── Bookmarks ─────────────────────────────────────────────────────────
    "bookmarks.list":   ("GET",    "/intel/bookmarks"),
    "bookmarks.add":    ("POST",   "/intel/bookmarks"),
    "bookmarks.delete": ("DELETE", "/intel/bookmarks/{bookmark_id}"),

    # ── Ideas ─────────────────────────────────────────────────────────────
    "ideas.list":                  ("GET",  "/channels/{channel_id}/ideas"),
    "ideas.create":                ("POST", "/channels/{channel_id}/ideas"),
    "ideas.generate":              ("POST", "/channels/{channel_id}/ideas/generate"),
    "ideas.generate-from-video":   ("POST", "/channels/{channel_id}/ideas/generate-from-video"),
    "ideas.generate-from-channel": ("POST", "/channels/{channel_id}/ideas/generate-from-channel"),
    "ideas.get":                   ("GET",  "/ideas/{idea_id}"),
    "ideas.to-script":             ("POST", "/ideas/{idea_id}/write"),
    "ideas.change-topic":          ("POST", "/ideas/{idea_id}/change-topic"),

    # ── Scripts ───────────────────────────────────────────────────────────
    "scripts.list":             ("GET",  "/channels/{channel_id}/scripts"),
    "scripts.create":           ("POST", "/channels/{channel_id}/scripts"),
    "scripts.get":              ("GET",  "/scripts/{script_id}"),
    "scripts.get-content":      ("GET",  "/scripts/{script_id}/content"),
    "scripts.generate-outline": ("POST", "/scripts/{script_id}/outline/generate"),
    "scripts.generate":         ("POST", "/scripts/{script_id}/script/generate"),
    "scripts.humanize":         ("POST", "/scripts/{script_id}/script/humanize"),
    "scripts.poll":             ("GET",  "/scripts/{script_id}/generate/poll"),
    "scripts.export":           ("GET",  "/scripts/{script_id}/export"),
    # Script Agent — single async job: research + outline + script
    "scripts.agent-generate":   ("POST", "/scripts/{script_id}/agent/generate"),
    "scripts.agent-poll":       ("GET",  "/scripts/{script_id}/agent/runs/{run_id}"),

    # ── Thumbnails ───────────────────────────────────────────────────────
    "thumbnails.usage":  ("GET",  "/team/thumbnails/usage"),
    "thumbnails.create": ("POST", "/channels/{channel_id}/thumbnails/generations"),
    "thumbnails.get":    ("GET",  "/channels/{channel_id}/thumbnails/generations/{run_id}"),
    "thumbnails.list":   ("GET",  "/channels/{channel_id}/thumbnails/generations"),

    # ── Webhooks ─────────────────────────────────────────────────────────
    "webhooks.list":   ("GET",    "/webhooks"),
    "webhooks.create": ("POST",   "/webhooks"),
    "webhooks.get":    ("GET",    "/webhooks/{webhook_id}"),
    "webhooks.update": ("PUT",    "/webhooks/{webhook_id}"),
    "webhooks.delete": ("DELETE", "/webhooks/{webhook_id}"),
    "webhooks.test":   ("POST",   "/webhooks/{webhook_id}/test"),
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_headers():
    token = os.environ.get("SUBSCRIBR_API_TOKEN")
    if not token:
        print("Error: SUBSCRIBR_API_TOKEN not set.", file=sys.stderr)
        print("Create a token at https://subscribr.ai/developer", file=sys.stderr)
        sys.exit(1)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "subscribr-cli/2.0",
    }

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def request(method, path, body=None):
    url = API_BASE + path
    headers = get_headers()
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    elif method == "POST":
        data = b"{}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            if raw.strip():
                return json.loads(raw)
            return {"status": "ok", "http_code": resp.status}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body_text)
        except (json.JSONDecodeError, ValueError):
            err = body_text
        print(json.dumps({"error": True, "status": e.code, "detail": err}, indent=2), file=sys.stderr)
        sys.exit(1)

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

def extract_path_params(template):
    return re.findall(r"\{(\w+)\}", template)


def try_json_parse(value):
    if value.startswith(("{", "[")):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
    return value


def parse_extra_args(args):
    result = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i][2:].replace("-", "_")
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                result[key] = try_json_parse(args[i + 1])
                i += 2
            else:
                result[key] = True
                i += 1
        else:
            i += 1
    return result

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

def print_domains():
    domains = sorted(set(k.split(".")[0] for k in ROUTES))
    print("Subscribr CLI — available domains:\n")
    for d in domains:
        actions = sorted(k.split(".", 1)[1] for k in ROUTES if k.startswith(d + "."))
        print(f"  {d:24s} ({len(actions)} actions)")
    print(f"\nTotal: {len(ROUTES)} endpoints")
    print("\nUsage: subscribr <domain> <action> [--key value ...]")
    print("       subscribr <domain> help")


def print_domain_help(domain):
    actions = {k: v for k, v in ROUTES.items() if k.startswith(domain + ".")}
    if not actions:
        print(f"Unknown domain: {domain}", file=sys.stderr)
        print_domains()
        sys.exit(1)

    print(f"Subscribr CLI — {domain} actions:\n")
    for key in sorted(actions):
        method, template = actions[key]
        action = key.split(".", 1)[1]
        path_params = extract_path_params(template)
        params_str = " ".join(f"--{p} <val>" for p in path_params)
        print(f"  {action:32s} {method:6s} {template}")
        if path_params:
            print(f"  {'':32s} required: {params_str}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]

    if not args or args[0] in ("help", "--help", "-h"):
        print_domains()
        return

    domain = args[0]

    if len(args) < 2 or args[1] in ("help", "--help", "-h"):
        print_domain_help(domain)
        return

    action = args[1]
    route_key = f"{domain}.{action}"

    if route_key not in ROUTES:
        print(f"Unknown command: {route_key}", file=sys.stderr)
        print_domain_help(domain)
        sys.exit(1)

    method, template = ROUTES[route_key]
    extra = parse_extra_args(args[2:])

    # Extract --body for raw JSON payloads
    raw_body = extra.pop("body", None)
    if isinstance(raw_body, str):
        raw_body = json.loads(raw_body)

    # Fill path params
    path_params = extract_path_params(template)
    path = template
    for p in path_params:
        if p not in extra:
            print(f"Missing required path param: --{p}", file=sys.stderr)
            sys.exit(1)
        path = path.replace(f"{{{p}}}", urllib.parse.quote(str(extra.pop(p)), safe=""))

    # Build body or query params from remaining args
    if raw_body is not None:
        body = raw_body
    elif method in ("POST", "PUT"):
        body = extra if extra else None
    else:
        body = None
        if extra:
            qs = urllib.parse.urlencode(extra)
            path = f"{path}?{qs}"

    result = request(method, path, body)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
