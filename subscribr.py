#!/usr/bin/env python3
"""Zero-dependency CLI transport for the canonical Subscribr Customer API."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

VERSION = "2.3.1"
METADATA_PATH = Path(__file__).resolve().parent / "skills" / "subscribr-api" / "references" / "operations.json"
BODY_METHODS = {"POST", "PUT", "PATCH"}
EXIT_AUTH = 2
EXIT_VALIDATION = 3
EXIT_CONFLICT = 4
EXIT_RATE_LIMIT = 5
EXIT_TRANSIENT = 6
EXIT_USAGE = 64
MAX_AUTOMATIC_RETRY_WAIT_SECONDS = 5.0
TOKEN_PAGE_URL = "https://subscribr.ai/integrations"


def load_metadata(path: Path = METADATA_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        metadata = json.load(handle)
    if not isinstance(metadata.get("operations"), dict):
        raise RuntimeError("CLI operation metadata is invalid.")
    return metadata


METADATA = load_metadata()
ROUTES = METADATA["operations"]

# Compatibility aliases preserve the original concise commands while route
# authority remains entirely in generated metadata.
ALIASES = {
    "team.get": "team.get-team",
    "team.get-credits": "team.get-team-credits",
    "scripts.agent-generate": "scripts.start-script-agent-run",
    "scripts.agent-poll": "scripts.get-script-agent-run",
    "scripts.agent-cancel": "scripts.cancel-script-agent-run",
}

# 2.2.0 shipped the Review & Fix reads/staging writes, apply-revision, and
# quote/create/cancel generation while their server-side routes were still
# unmerged in Main, so a bare 404 there was explained as "may not be
# deployed yet." Main deployed the whole Video slice on 2026-08-31, so that
# 404 now means exactly what a bare 404 means anywhere else in the API: the
# resource was not found. There is nothing left to special-case here.


class CliError(Exception):
    def __init__(self, message: str, exit_code: int = 1, detail: Any = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.detail = detail


def api_base() -> str:
    return os.environ.get("SUBSCRIBR_API_BASE_URL", METADATA["base_url"]).rstrip("/")


def ssl_context() -> ssl.SSLContext | None:
    """
    Production uses the system trust store, so this returns None and urllib
    applies its default.

    `SUBSCRIBR_CA_BUNDLE` exists for the local and staging conformance runs the
    README already describes: those hosts are served by a development root
    (Herd, Valet, mkcert) that the system store does not carry. Supplying a
    bundle is an explicit opt-in, so it also relaxes RFC-strict extension
    checks that development roots routinely omit. Nothing here weakens the
    default production path.
    """
    bundle = os.environ.get("SUBSCRIBR_CA_BUNDLE")
    if not bundle:
        return None

    path = Path(bundle).expanduser()
    if not path.is_file():
        raise CliError(f"SUBSCRIBR_CA_BUNDLE does not point at a file: {path}", EXIT_USAGE)

    context = ssl.create_default_context()
    try:
        context.load_verify_locations(cafile=str(path))
    except ssl.SSLError as error:
        raise CliError(f"SUBSCRIBR_CA_BUNDLE is not a readable CA bundle: {error}", EXIT_USAGE) from error
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def get_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    token = os.environ.get("SUBSCRIBR_API_TOKEN")
    if not token:
        raise CliError(
            f"SUBSCRIBR_API_TOKEN is not set. Create a Team-bound token at {TOKEN_PAGE_URL}.",
            EXIT_AUTH,
        )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"subscribr-cli/{VERSION}",
    }
    headers.update(extra or {})
    return headers


def status_exit_code(status: int) -> int:
    if status in (401, 403):
        return EXIT_AUTH
    if status in (400, 404, 405, 422):
        return EXIT_VALIDATION
    if status in (409, 412):
        return EXIT_CONFLICT
    if status == 429:
        return EXIT_RATE_LIMIT
    if status >= 500:
        return EXIT_TRANSIENT
    return 1


def retryable(method: str, headers: dict[str, str]) -> bool:
    return method in {"GET", "HEAD", "OPTIONS"} or "Idempotency-Key" in headers


def permanent_network_failure(reason: Any) -> bool:
    """
    Certificate and name-resolution failures are configuration problems, not
    blips. Report them immediately with the underlying reason attached.
    """
    if isinstance(reason, ssl.SSLError):
        return True
    if isinstance(reason, socket.gaierror):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(reason) or "Name or service not known" in str(reason)


def retry_after_seconds(value: str | None, current_time: datetime | None = None) -> float | None:
    if value is None:
        return None

    value = value.strip()
    if re.fullmatch(r"\d+", value):
        return float(value)

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)

    current_time = current_time or datetime.now(timezone.utc)
    return max(0.0, (retry_at - current_time).total_seconds())


def request(
    method: str,
    path: str,
    body: Any = None,
    extra_headers: dict[str, str] | None = None,
    max_attempts: int = 3,
    operation: str | None = None,
) -> Any:
    headers = get_headers(extra_headers)
    data = json.dumps(body).encode("utf-8") if body is not None else (b"{}" if method in BODY_METHODS else None)
    req = urllib.request.Request(api_base() + path, data=data, headers=headers, method=method)
    can_retry = retryable(method, headers)
    context = ssl_context()

    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=30, context=context) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {"status": "ok", "http_code": response.status}
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                detail = raw
            if can_retry and error.code in (429, 500, 502, 503, 504) and attempt < max_attempts:
                retry_after = retry_after_seconds(error.headers.get("Retry-After") if error.headers else None)
                if retry_after is not None and retry_after > MAX_AUTOMATIC_RETRY_WAIT_SECONDS:
                    raise CliError(f"HTTP {error.code}", status_exit_code(error.code), detail) from error
                delay = retry_after if retry_after is not None else min(0.25 * (2 ** (attempt - 1)), 2.0)
                time.sleep(delay)
                continue
            raise CliError(f"HTTP {error.code}", status_exit_code(error.code), detail) from error
        except urllib.error.URLError as error:
            reason = str(error.reason)
            # TLS and DNS failures never clear up on their own. Retrying them
            # three times behind a "temporarily unreachable" message hides the
            # one thing the caller needs to know.
            if permanent_network_failure(error.reason):
                raise CliError(
                    f"Cannot reach {api_base()}: {reason}",
                    EXIT_TRANSIENT,
                    reason,
                ) from error
            if can_retry and attempt < max_attempts:
                time.sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))
                continue
            raise CliError(
                f"Subscribr API is temporarily unreachable ({reason}).",
                EXIT_TRANSIENT,
                reason,
            ) from error

    raise CliError("Subscribr API request failed.", EXIT_TRANSIENT)


def extract_path_params(template: str) -> list[str]:
    return re.findall(r"\{(\w+)\}", template)


def parameter_option(parameter: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", parameter).replace("_", "-").lower()


def parameter_argument_key(parameter: str, arguments: dict[str, Any]) -> str | None:
    if parameter in arguments:
        return parameter

    snake_case = re.sub(r"(?<!^)(?=[A-Z])", "_", parameter).lower()
    return snake_case if snake_case in arguments else None


def try_json_parse(value: str) -> Any:
    if value.startswith(("{", "[", '"')) or value in ("true", "false", "null") or re.fullmatch(r"-?\d+(\.\d+)?", value):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
    return value


# These two arguments are always transported as opaque header values
# (see build_request), never as request-body fields. A strong ETag is
# quoted by definition (`"abc123"`), so JSON-decoding it here would strip
# the quotes the server requires and send a malformed If-Match value.
HEADER_BOUND_ARGUMENTS = {"idempotency_key", "if_match"}


def parse_extra_args(args: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    index = 0
    while index < len(args):
        argument = args[index]
        if not argument.startswith("--"):
            raise CliError(f"Unexpected argument: {argument}", EXIT_USAGE)
        key = argument[2:].replace("-", "_")
        if index + 1 < len(args) and not args[index + 1].startswith("--"):
            value = args[index + 1]
            result[key] = value if key in HEADER_BOUND_ARGUMENTS else try_json_parse(value)
            index += 2
        else:
            result[key] = True
            index += 1
    return result


def resolve_route(domain: str, action: str) -> tuple[str, dict[str, Any]]:
    key = f"{domain}.{action}"
    key = ALIASES.get(key, key)
    if key not in ROUTES:
        raise CliError(f"Unknown command: {domain}.{action}", EXIT_USAGE)
    return key, ROUTES[key]


def build_request(route: dict[str, Any], arguments: dict[str, Any]) -> tuple[str, str, Any, dict[str, str]]:
    method = route["method"]
    path = route["path"]
    arguments = dict(arguments)
    raw_body = arguments.pop("body", None)
    if isinstance(raw_body, str) and raw_body.startswith("@"):
        try:
            with Path(raw_body[1:]).open(encoding="utf-8") as handle:
                raw_body = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise CliError(f"Unable to read JSON body file: {error}", EXIT_USAGE) from error
    headers: dict[str, str] = {}
    for argument, header in (("idempotency_key", "Idempotency-Key"), ("if_match", "If-Match")):
        value = arguments.pop(argument, None)
        if value is not None:
            headers[header] = str(value)

    write_safety = route.get("write_safety") or {}
    idempotency = write_safety.get("idempotency")
    concurrency = write_safety.get("concurrency")
    if idempotency == "required" and "Idempotency-Key" not in headers:
        raise CliError("This operation requires --idempotency-key.", EXIT_USAGE)
    if idempotency == "unsupported" and "Idempotency-Key" in headers:
        raise CliError("This operation does not support --idempotency-key.", EXIT_USAGE)
    if concurrency == "required" and "If-Match" not in headers:
        raise CliError("This operation requires --if-match with the current strong ETag.", EXIT_USAGE)
    if concurrency == "unsupported" and "If-Match" in headers:
        raise CliError("This operation does not support --if-match.", EXIT_USAGE)

    for parameter in extract_path_params(path):
        argument_key = parameter_argument_key(parameter, arguments)
        if argument_key is None:
            raise CliError(f"Missing required path param: --{parameter_option(parameter)}", EXIT_USAGE)
        path = path.replace(f"{{{parameter}}}", urllib.parse.quote(str(arguments.pop(argument_key)), safe=""))

    if raw_body is not None and method not in BODY_METHODS:
        raise CliError("--body is only supported for POST, PUT, and PATCH operations.", EXIT_USAGE)
    if raw_body is not None and not isinstance(raw_body, (dict, list)):
        raise CliError("--body must be a JSON object or array.", EXIT_USAGE)
    if raw_body is not None:
        if arguments:
            fields = ", ".join(f"--{key.replace('_', '-')}" for key in sorted(arguments))
            raise CliError(f"--body cannot be combined with request fields ({fields}) for this operation.", EXIT_USAGE)
        body = raw_body
    elif method in BODY_METHODS:
        body = arguments or None
    else:
        body = None
        if arguments:
            path += "?" + urllib.parse.urlencode(arguments, doseq=True)
    return method, path, body, headers


def split_transport_options(args: list[str]) -> tuple[list[str], bool, str | None]:
    wait = False
    output: str | None = None
    remaining: list[str] = []
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--wait":
            wait = True
            index += 1
            continue
        if argument == "--output":
            if index + 1 >= len(args):
                raise CliError("--output requires a file path.", EXIT_USAGE)
            output = args[index + 1]
            index += 2
            continue
        remaining.append(argument)
        index += 1
    return remaining, wait, output


def find_download_url(payload: Any) -> str | None:
    """A response's `download_url`, at the top level or nested under `data`."""
    if not isinstance(payload, dict):
        return None
    direct = payload.get("download_url")
    if isinstance(direct, str) and direct:
        return direct
    nested = payload.get("data")
    if isinstance(nested, dict):
        value = nested.get("download_url")
        if isinstance(value, str) and value:
            return value
    return None


def is_public_download_address(address: str) -> bool:
    """
    True for an address a `download_url` may legitimately resolve to.

    False for private (RFC 1918/4193), loopback, link-local (this is what
    covers a cloud metadata endpoint — 169.254.169.254 and its IPv6
    equivalent are both link-local), multicast, reserved, or unspecified
    addresses.
    """
    parsed = ipaddress.ip_address(address)
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )


def ensure_public_download_host(url: str) -> None:
    """
    Refuses to let a download proceed anywhere that isn't a public host.

    A `download_url` is supposed to name a public storage/CDN object; this
    is what stops a compromised or tampered API response from pointing the
    operator's machine at an internal service or the cloud metadata
    endpoint instead.

    This resolves and classifies the address rather than checking the
    hostname against an allowlist. Signed URLs legitimately come from more
    than one storage host — the account's storage provider can rotate, use
    a regional bucket, or sit behind a CDN domain — and that set is not
    ours to enumerate here; a strict allowlist would eventually reject a
    legitimate signed URL. Classifying the resolved address is proportionate
    to the actual threat, which is a tampered *destination*, not an
    untrusted *provider*.

    This does not defend against DNS rebinding between this check and the
    connection urllib opens moments later — closing that would mean pinning
    the resolved address for the request itself, which is more than this
    hardening pass is proportionate to against a threat that already
    requires a compromised or tampered backend response.
    """
    hostname = urllib.parse.urlparse(url).hostname
    if not hostname:
        raise CliError("The response's download_url has no host.", EXIT_VALIDATION)
    try:
        resolved = socket.getaddrinfo(hostname, None)
    except socket.gaierror as error:
        raise CliError(f"Cannot resolve the download host: {error}", EXIT_TRANSIENT) from error
    for *_rest, sockaddr in resolved:
        address = sockaddr[0]
        if not is_public_download_address(address):
            raise CliError(
                f"Refusing to download from {hostname}: it resolves to {address}, which is not "
                "a public address.",
                EXIT_VALIDATION,
            )


class PublicHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    Re-validates scheme and destination on every redirect hop.

    Without this, a first request to an approved public host could still be
    redirected by a 30x response to an internal address or downgraded to a
    non-http(s) scheme, and urllib's default redirect handling would follow
    it without another look.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme not in ("http", "https"):
            raise CliError("A download redirect pointed at a non-http(s) URL.", EXIT_VALIDATION)
        ensure_public_download_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_to_file(url: str, destination: Path) -> int:
    """
    Streams a pre-signed `download_url` to `destination`.

    The URL points at a third-party host (CDN/object storage), not the
    Subscribr API, so this request never carries the `Authorization` header —
    sending our bearer token to that host would leak the credential to a
    party we do not control. The response is written to a sibling temp file
    and renamed into place only once the full transfer succeeds, so a failed
    or interrupted download never leaves a partial file at the requested path.

    The destination host — and, on redirect, every host the response sends
    it to next — must resolve to a public address: see
    `ensure_public_download_host` for why this is a resolved-address check
    rather than a host allowlist.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise CliError("The response's download_url is not an http(s) URL.", EXIT_VALIDATION)
    ensure_public_download_host(url)

    destination = destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(f".{destination.name}.part-{os.getpid()}-{os.urandom(4).hex()}")

    request_headers = {"User-Agent": f"subscribr-cli/{VERSION}"}
    download_request = urllib.request.Request(url, headers=request_headers, method="GET")
    context = ssl_context()
    # Installed (rather than passed as urlopen's own `context=`) so our
    # redirect handler — which must re-validate every hop's destination —
    # participates too; urlopen's own `context=` argument builds a fresh,
    # bare-default opener that would silently drop it.
    urllib.request.install_opener(
        urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), PublicHostRedirectHandler())
    )
    total_bytes = 0
    try:
        with urllib.request.urlopen(download_request, timeout=120) as response:
            with temp_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    handle.write(chunk)
                    total_bytes += len(chunk)
    except urllib.error.HTTPError as error:
        temp_path.unlink(missing_ok=True)
        # Never echo the URL: a signed URL's own error response can carry
        # signing material in redirected/echoed query strings.
        raise CliError(
            f"Download failed: HTTP {error.code}. The signed URL may have expired; re-fetch it and retry.",
            status_exit_code(error.code),
        ) from error
    except urllib.error.URLError as error:
        temp_path.unlink(missing_ok=True)
        reason = str(error.reason)
        if permanent_network_failure(error.reason):
            raise CliError(f"Cannot reach the download host: {reason}", EXIT_TRANSIENT) from error
        raise CliError(f"Download temporarily failed: {reason}", EXIT_TRANSIENT) from error
    except OSError as error:
        temp_path.unlink(missing_ok=True)
        raise CliError(f"Unable to write {destination}: {error}", EXIT_USAGE) from error
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    temp_path.replace(destination)
    return total_bytes


def print_domains() -> None:
    domains = sorted({key.split(".", 1)[0] for key in ROUTES})
    print("Subscribr CLI — canonical API domains:\n")
    for domain in domains:
        count = sum(key.startswith(domain + ".") for key in ROUTES)
        print(f"  {domain:16s} ({count} actions)")
    print(f"\nTotal: {len(ROUTES)} operations (contract {METADATA['contract_version']})")
    print("\nUsage: subscribr <domain> <action> [--key value ...]")
    print("\nStart here:")
    print("  subscribr doctor                     confirm the token, base URL, and Team")
    print("  subscribr channels list-channels     the Channel IDs most commands need")
    print("  subscribr <domain> help              actions in one domain")
    print("  subscribr <domain> <action> --help   required fields and an example body")


def print_doctor() -> int:
    """
    First command an agent should run. Confirms where requests go, whether the
    token works, and which Team it is bound to — before any real work fails.
    """
    base = api_base()
    print(f"Base URL       {base}")
    if base.rstrip("/") != METADATA["base_url"].rstrip("/"):
        print(f"               (overridden; contract default is {METADATA['base_url']})")
    print(f"CLI version    {VERSION}")
    print(f"Contract       {METADATA['contract_version']} ({len(ROUTES)} operations)")

    if not os.environ.get("SUBSCRIBR_API_TOKEN"):
        print("Token          MISSING")
        print(f"\nSet a Team-bound token, then run this again:\n"
              f"  export SUBSCRIBR_API_TOKEN=...\n"
              f"Create one at {TOKEN_PAGE_URL}")
        return EXIT_AUTH

    print("Token          present")
    try:
        team = request("GET", "/api/v1/team", None, {})
    except CliError as error:
        print(f"\nConnection     FAILED — {error}")
        if error.detail is not None:
            print(f"               {json.dumps(error.detail) if not isinstance(error.detail, str) else error.detail}")
        return error.exit_code

    payload = team if isinstance(team, dict) else {}
    for envelope in ("team", "data"):
        if isinstance(payload.get(envelope), dict):
            payload = payload[envelope]
            break

    identity = f"Team {payload.get('id', '?')}"
    if payload.get("name"):
        identity += f" ({payload['name']})"
    print(f"Connection     OK — {identity}")

    role = payload.get("user_role")
    plan = (payload.get("subscription") or {}).get("plan") if isinstance(payload.get("subscription"), dict) else None
    if role:
        print(f"Your role      {role}")
    if plan:
        print(f"Plan           {plan}")

    print("\nReady. Next: subscribr channels list-channels")
    return 0


def field_option(name: str) -> str:
    return name.replace("_", "-")


def required_options(route: dict[str, Any]) -> list[str]:
    """Every flag the caller must supply: path params first, then body fields."""
    options = [f"--{parameter_option(name)} <value>" for name in extract_path_params(route["path"])]
    body = route.get("body") or {}
    for name in body.get("required", []):
        field = (body.get("fields") or {}).get(name) or {}
        options.append(f"--{field_option(name)} <{field.get('type', 'value')}>")
    return options


def optional_options(route: dict[str, Any]) -> list[str]:
    options = [f"--{parameter_option(name)} <value>" for name in route.get("query_parameters", [])]
    body = route.get("body") or {}
    required = set(body.get("required", []))
    for name, field in (body.get("fields") or {}).items():
        if name not in required:
            options.append(f"--{field_option(name)} <{field.get('type', 'value')}>")
    return options


def print_domain_help(domain: str) -> None:
    actions = {key: value for key, value in ROUTES.items() if key.startswith(domain + ".")}
    if not actions:
        raise CliError(f"Unknown domain: {domain}", EXIT_USAGE)
    print(f"Subscribr CLI — {domain} actions:\n")
    for key, route in sorted(actions.items()):
        action = key.split(".", 1)[1]
        required = " ".join(required_options(route))
        optional = " ".join(optional_options(route))
        print(f"  {action:34s} {route['method']:6s} {route['path']}")
        if required:
            print(f"  {'':34s} required: {required}")
        if optional:
            print(f"  {'':34s} optional: {optional}")
    print(f"\nField detail and an example body: subscribr {domain} <action> --help")


def describe_constraints(field: dict[str, Any]) -> str:
    parts = []
    if "enum" in field:
        parts.append("one of " + ", ".join(str(value) for value in field["enum"]))
    parts.append(range_label("", field.get("minimum"), field.get("maximum")))
    parts.append(range_label("length ", field.get("minLength"), field.get("maxLength")))
    return "; ".join(part for part in parts if part)


def range_label(prefix: str, low: Any, high: Any) -> str:
    """`min 200`, `max 255`, or `200..255` — never a padded bound we invented."""
    if low is None and high is None:
        return ""
    if low is None:
        return f"{prefix}max {high}"
    if high is None:
        return f"{prefix}min {low}"
    return f"{prefix}{low}..{high}"


def print_action_help(domain: str, action: str) -> None:
    """
    Full shape of one operation. An agent that reads this can make a valid call
    on the first attempt instead of discovering the schema through 422s.
    """
    key, route = resolve_route(domain, action)
    body = route.get("body") or {}
    fields = body.get("fields") or {}
    required = set(body.get("required", []))

    print(f"subscribr {key.replace('.', ' ', 1)}")
    if route.get("summary"):
        print(f"  {route['summary']}")
    print()
    print(f"  {route['method']} {route['path']}")
    if route.get("abilities"):
        print(f"  Token abilities: {', '.join(route['abilities'])}")

    path_params = extract_path_params(route["path"])
    if path_params:
        print("\n  Path parameters (required):")
        for name in path_params:
            print(f"    --{parameter_option(name):28s} <value>")

    if route.get("query_parameters"):
        print("\n  Query parameters (optional):")
        for name in route["query_parameters"]:
            print(f"    --{parameter_option(name):28s} <value>")

    if fields:
        for label, names in (
            ("Body fields (required)", [name for name in body.get("required", [])]),
            ("Body fields (optional)", [name for name in fields if name not in required]),
        ):
            if not names:
                continue
            print(f"\n  {label}:")
            for name in names:
                field = fields[name]
                constraints = describe_constraints(field)
                suffix = f" [{constraints}]" if constraints else ""
                print(f"    --{field_option(name):28s} {field.get('type', 'value')}{suffix}")
                if field.get("description"):
                    print(f"      {'':28s} {field['description']}")

    safety = route.get("write_safety") or {}
    if safety:
        notes = []
        if safety.get("idempotency") == "required":
            notes.append("--idempotency-key is required")
        if safety.get("concurrency") == "required":
            notes.append("--if-match with the current strong ETag is required")
        if safety.get("retry") == "never":
            notes.append("never retried automatically")
        if notes:
            print("\n  Write safety: " + "; ".join(notes) + ".")

    if body.get("example"):
        print("\n  Example --body:")
        for line in json.dumps(body["example"], indent=2, ensure_ascii=False).splitlines():
            print(f"    {line}")
        print("\n  Pass fields individually, or all at once with --body '<json>' / --body @file.json.")


def run(args: list[str]) -> int:
    if not args or args[0] in ("help", "--help", "-h"):
        print_domains()
        return 0
    if args[0] in ("version", "--version", "-V"):
        print(VERSION)
        return 0
    if args[0] in ("doctor", "whoami"):
        return print_doctor()
    domain = args[0]
    if len(args) < 2 or args[1] in ("help", "--help", "-h"):
        print_domain_help(domain)
        return 0
    # Resolve before inspecting flags so an unknown command fails as a usage
    # error rather than reaching the network.
    key, route = resolve_route(domain, args[1])
    if any(argument in ("help", "--help", "-h") for argument in args[2:]):
        print_action_help(domain, args[1])
        return 0
    command_args, wait, output = split_transport_options(args[2:])
    if wait:
        raise CliError(
            "Automatic waiting is unavailable. Poll with `operations get-operation --operation <uuid>` instead.",
            EXIT_USAGE,
        )
    method, path, body, headers = build_request(route, parse_extra_args(command_args))
    result = request(method, path, body, headers, operation=key)

    if output is not None:
        download_url = find_download_url(result)
        if download_url is None:
            raise CliError(
                "--output requires a response with a download_url; this operation's response did not include one.",
                EXIT_USAGE,
            )
        destination = Path(output)
        written_bytes = download_to_file(download_url, destination)
        summary: dict[str, Any] = {"downloaded": True, "path": str(destination), "bytes": written_bytes}
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        expires_at = data.get("expires_at") if isinstance(data, dict) else None
        if isinstance(expires_at, str):
            summary["url_expires_at"] = expires_at
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def main() -> None:
    try:
        raise SystemExit(run(sys.argv[1:]))
    except KeyboardInterrupt:
        print("Waiting stopped locally; no server-side operation was cancelled.", file=sys.stderr)
        raise SystemExit(130)
    except CliError as error:
        payload = {"error": True, "message": str(error)}
        if error.detail is not None:
            payload["detail"] = error.detail
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(error.exit_code)


if __name__ == "__main__":
    main()
