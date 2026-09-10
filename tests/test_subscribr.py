import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("subscribr", ROOT / "subscribr.py")
subscribr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subscribr)


class CliContractTest(unittest.TestCase):
    # The original nine: capability discovery, Channels, and custom assets.
    VIDEO_READ_ROUTES = {
        "video.list-capabilities": ("videoListCapabilities", "/api/v1/video/capabilities"),
        "video.list-channels": ("videoListChannels", "/api/v1/video/channels"),
        "video.get-channel": ("videoGetChannel", "/api/v1/video/channels/{videoChannel}"),
        "video.list-voices": ("videoListVoices", "/api/v1/video/voices"),
        "video.get-voice": ("videoGetVoice", "/api/v1/video/voices/{voice}"),
        "video.list-avatars": ("videoListAvatars", "/api/v1/video/avatars"),
        "video.get-avatar": ("videoGetAvatar", "/api/v1/video/avatars/{avatar}"),
        "video.list-media-assets": ("videoListMediaAssets", "/api/v1/video/media-assets"),
        "video.get-media-asset": ("videoGetMediaAsset", "/api/v1/video/media-assets/{mediaAsset}"),
        # Review & Fix reads added alongside the staging writes below.
        "video.list-projects": ("videoListProjects", "/api/v1/video/projects"),
        "video.get-project": ("videoGetProject", "/api/v1/video/projects/{project}"),
        "video.get-project-download": ("videoGetProjectDownload", "/api/v1/video/projects/{project}/download"),
        "video.get-editable-content": ("videoGetEditableContent", "/api/v1/video/projects/{project}/editable-content"),
        "video.get-revision-manifest": ("videoGetRevisionManifest", "/api/v1/video/projects/{project}/revision-manifest"),
        "video.list-overlay-templates": ("videoListOverlayTemplates", "/api/v1/video/projects/{project}/overlay-templates"),
        "video.get-quality-report": ("videoGetQualityReport", "/api/v1/video/projects/{project}/quality-report"),
        "video.get-revision-pass": ("videoGetRevisionPass", "/api/v1/video/projects/{project}/revision/passes/{pass}"),
    }
    # Review & Fix staging/discard writes: video:edit, idempotency+concurrency required.
    VIDEO_EDIT_WRITE_ROUTES = {
        "video.add-overlay": ("videoAddOverlay", "/api/v1/video/projects/{project}/revision/overlays"),
        "video.remove-staged-overlay": ("videoRemoveStagedOverlay", "/api/v1/video/projects/{project}/revision/overlays/{item}"),
        "video.update-overlay": ("videoUpdateOverlay", "/api/v1/video/projects/{project}/revision/published-overlays/{overlay}"),
        "video.remove-overlay": ("videoRemoveOverlay", "/api/v1/video/projects/{project}/revision/published-overlays/{overlay}"),
        "video.update-captions": ("videoUpdateCaptions", "/api/v1/video/projects/{project}/revision/captions"),
        "video.remove-music": ("videoRemoveMusic", "/api/v1/video/projects/{project}/revision/music"),
        "video.edit-slide-text": ("videoEditSlideText", "/api/v1/video/projects/{project}/revision/slide-text"),
        "video.regenerate-visual": ("videoRegenerateVisual", "/api/v1/video/projects/{project}/revision/regenerate-visual"),
        "video.replace-with-media": ("videoReplaceWithMedia", "/api/v1/video/projects/{project}/revision/replace-with-media"),
        "video.show-presenter": ("videoShowPresenter", "/api/v1/video/projects/{project}/revision/presenter"),
        "video.discard-edit": ("videoDiscardEdit", "/api/v1/video/projects/{project}/revision/items/{item}"),
    }
    # Publishing a revision: video:publish, its own ability, same write safety.
    VIDEO_PUBLISH_WRITE_ROUTES = {
        "video.apply-revision": ("videoApplyRevision", "/api/v1/video/projects/{project}/revision/apply"),
    }
    # A quote spends nothing: video:generate, idempotency AND concurrency both unsupported.
    VIDEO_QUOTE_ROUTES = {
        "video.quote-video": ("videoQuoteVideo", "/api/v1/video/projects/quote"),
    }
    # Generation writes: video:generate, idempotency required, concurrency unsupported
    # (there is no existing revision for a create or a cancel to conflict with).
    VIDEO_GENERATE_WRITE_ROUTES = {
        "video.create-video": ("videoCreateVideo", "/api/v1/video/projects"),
        "video.cancel-video": ("videoCancelVideo", "/api/v1/video/projects/{project}/cancel"),
    }
    VIDEO_ROUTES = {
        **VIDEO_READ_ROUTES,
        **VIDEO_EDIT_WRITE_ROUTES,
        **VIDEO_PUBLISH_WRITE_ROUTES,
        **VIDEO_QUOTE_ROUTES,
        **VIDEO_GENERATE_WRITE_ROUTES,
    }

    def test_generated_metadata_covers_all_current_operations(self):
        operation_ids = {route["operation_id"] for route in subscribr.ROUTES.values()}
        self.assertEqual(set(subscribr.METADATA["required_operation_ids"]), operation_ids)
        self.assertIn("operations.get-operation", subscribr.ROUTES)
        self.assertEqual("getOperation", subscribr.ROUTES["operations.get-operation"]["operation_id"])
        self.assertIn("team.create-api-token", subscribr.ROUTES)
        self.assertIn("scripts.cancel-script-agent-run", subscribr.ROUTES)
        self.assertIn("projects.list-projects", subscribr.ROUTES)
        self.assertIn("templates.create-template", subscribr.ROUTES)
        self.assertIn("voices.commit-voice-profile", subscribr.ROUTES)

    def test_video_surface_contains_every_operation_shape(self):
        routes = {
            key: (route["operation_id"], route["path"])
            for key, route in subscribr.ROUTES.items()
            if key.startswith("video.")
        }

        self.assertEqual(self.VIDEO_ROUTES, routes)

        for key in self.VIDEO_READ_ROUTES:
            route = subscribr.ROUTES[key]
            self.assertEqual("GET", route["method"], key)
            self.assertEqual(["video:read"], route["abilities"], key)
            self.assertIsNone(route["write_safety"], key)

        for key in self.VIDEO_EDIT_WRITE_ROUTES:
            route = subscribr.ROUTES[key]
            self.assertNotEqual("GET", route["method"], key)
            self.assertEqual(["video:edit"], route["abilities"], key)
            self.assertEqual(
                {"idempotency": "required", "concurrency": "required", "retry": "same-key"},
                route["write_safety"],
                key,
            )

        for key in self.VIDEO_PUBLISH_WRITE_ROUTES:
            route = subscribr.ROUTES[key]
            self.assertNotEqual("GET", route["method"], key)
            self.assertEqual(["video:publish"], route["abilities"], key)
            self.assertEqual(
                {"idempotency": "required", "concurrency": "required", "retry": "same-key"},
                route["write_safety"],
                key,
            )

        for key in self.VIDEO_QUOTE_ROUTES:
            route = subscribr.ROUTES[key]
            self.assertNotEqual("GET", route["method"], key)
            self.assertEqual(["video:generate"], route["abilities"], key)
            self.assertEqual(
                {"idempotency": "unsupported", "concurrency": "unsupported", "retry": "never"},
                route["write_safety"],
                key,
            )

        for key in self.VIDEO_GENERATE_WRITE_ROUTES:
            route = subscribr.ROUTES[key]
            self.assertNotEqual("GET", route["method"], key)
            self.assertEqual(["video:generate"], route["abilities"], key)
            self.assertEqual(
                {"idempotency": "required", "concurrency": "unsupported", "retry": "same-key"},
                route["write_safety"],
                key,
            )

    def test_no_pending_deploy_special_case_remains(self):
        """2.2.0's `VIDEO_OPERATIONS_PENDING_DEPLOY` explained a bare 404 as
        "may not be deployed yet" while the Video slice was still rolling
        out. Main deployed all of it on 2026-08-31, so that special case is
        gone — this pins it staying gone rather than quietly coming back."""
        self.assertFalse(hasattr(subscribr, "VIDEO_OPERATIONS_PENDING_DEPLOY"))

    def test_aliases_resolve_to_generated_routes(self):
        key, route = subscribr.resolve_route("scripts", "agent-cancel")
        self.assertEqual("scripts.cancel-script-agent-run", key)
        self.assertEqual("cancelScriptAgentRun", route["operation_id"])

    def test_build_request_encodes_paths_query_body_and_safety_headers(self):
        get_route = {"method": "GET", "path": "/api/v1/projects/{project}"}
        method, path, body, headers = subscribr.build_request(get_route, {"project": "project:v1:idea:7", "include": ["tasks", "comments"]})
        self.assertEqual("GET", method)
        self.assertEqual("/api/v1/projects/project%3Av1%3Aidea%3A7?include=tasks&include=comments", path)
        self.assertIsNone(body)
        self.assertEqual({}, headers)

        patch_route = {"method": "PATCH", "path": "/api/v1/projects/{project}"}
        _, path, body, headers = subscribr.build_request(patch_route, {
            "project": "project:v1:script:8",
            "title": "Updated",
            "idempotency_key": "once-1",
            "if_match": '"project-r1"',
        })
        self.assertEqual("/api/v1/projects/project%3Av1%3Ascript%3A8", path)
        self.assertEqual({"title": "Updated"}, body)
        self.assertEqual({"Idempotency-Key": "once-1", "If-Match": '"project-r1"'}, headers)

    def test_parse_extra_args_leaves_header_bound_values_untouched(self):
        """A strong ETag is quoted by definition (`"abc123"`). try_json_parse
        auto-decodes a value starting with a double-quote as JSON, which
        strips those required quotes before the value ever reaches
        build_request. --if-match and --idempotency-key are always
        transported as opaque header strings, never as body fields, so they
        must skip JSON auto-decoding entirely."""
        parsed = subscribr.parse_extra_args(["--if-match", '"abc123"'])
        self.assertEqual('"abc123"', parsed["if_match"])

        parsed = subscribr.parse_extra_args(["--idempotency-key", '"abc123"'])
        self.assertEqual('"abc123"', parsed["idempotency_key"])

        # A bare numeric or true/false/null-looking idempotency key must also
        # survive as a string — it is an opaque token, not a JSON literal.
        parsed = subscribr.parse_extra_args(["--idempotency-key", "12345"])
        self.assertEqual("12345", parsed["idempotency_key"])
        parsed = subscribr.parse_extra_args(["--idempotency-key", "true"])
        self.assertEqual("true", parsed["idempotency_key"])

    def test_parse_extra_args_still_json_decodes_ordinary_body_fields(self):
        """Narrowing JSON auto-decoding to skip header-bound arguments must
        not disturb it for everything else: a body field genuinely carrying
        a JSON object, array, number, or boolean still decodes today."""
        parsed = subscribr.parse_extra_args(["--metadata", '{"a": 1}'])
        self.assertEqual({"a": 1}, parsed["metadata"])

        parsed = subscribr.parse_extra_args(["--tags", '["x", "y"]'])
        self.assertEqual(["x", "y"], parsed["tags"])

        parsed = subscribr.parse_extra_args(["--count", "3", "--active", "true", "--title", "Plain text"])
        self.assertEqual(3, parsed["count"])
        self.assertIs(True, parsed["active"])
        self.assertEqual("Plain text", parsed["title"])

    def test_if_match_reaches_the_wire_with_its_quotes_intact(self):
        """End-to-end regression for the reported bug: a correct strong ETag
        passed on the command line must be sent byte-for-byte, or the server
        rejects it as an unquoted/malformed If-Match and the user sees a
        confusing validation error instead of a working request."""
        captured = []

        def urlopen(req, timeout=None, context=None):
            captured.append(req)
            response = MagicMock()
            response.status = 200
            response.headers = {}
            response.read.return_value = b'{"ok": true}'
            response.__enter__.return_value = response
            return response

        with patch.dict(os.environ, {"SUBSCRIBR_API_TOKEN": "t"}, clear=True), \
                patch("urllib.request.urlopen", urlopen), \
                redirect_stdout(io.StringIO()):
            subscribr.run([
                "projects", "update-project",
                "--project", "project:v1:idea:7",
                "--title", "Updated",
                "--idempotency-key", "once-1",
                "--if-match", '"project-r1"',
            ])

        self.assertEqual(1, len(captured))
        self.assertEqual('"project-r1"', captured[0].get_header("If-match"))
        self.assertEqual("once-1", captured[0].get_header("Idempotency-key"))

    def test_camel_case_contract_parameters_use_kebab_case_cli_options(self):
        route = subscribr.ROUTES["video.get-media-asset"]
        method, path, body, headers = subscribr.build_request(
            route,
            {"media_asset": "820e8400-e29b-41d4-a716-446655440006"},
        )

        self.assertEqual("GET", method)
        self.assertEqual(
            "/api/v1/video/media-assets/820e8400-e29b-41d4-a716-446655440006",
            path,
        )
        self.assertIsNone(body)
        self.assertEqual({}, headers)
        self.assertEqual("video-channel", subscribr.parameter_option("videoChannel"))

    def test_video_asset_lists_encode_page_and_per_page_query_options(self):
        for key in ("video.list-voices", "video.list-avatars", "video.list-media-assets"):
            route = subscribr.ROUTES[key]
            method, path, body, headers = subscribr.build_request(route, {"page": 2, "per_page": 5})

            self.assertEqual("GET", method)
            self.assertEqual(f"{route['path']}?page=2&per_page=5", path)
            self.assertIsNone(body)
            self.assertEqual({}, headers)
            self.assertEqual(["page", "per_page"], route["query_parameters"])

    def test_generated_write_safety_requires_and_rejects_transport_headers(self):
        update_route = subscribr.ROUTES["projects.update-project"]
        with self.assertRaises(subscribr.CliError) as raised:
            subscribr.build_request(update_route, {"project": "project:v1:idea:7", "title": "Updated"})
        self.assertEqual(subscribr.EXIT_USAGE, raised.exception.exit_code)
        self.assertIn("idempotency", str(raised.exception).lower())

        with self.assertRaises(subscribr.CliError) as raised:
            subscribr.build_request(update_route, {
                "project": "project:v1:idea:7",
                "title": "Updated",
                "idempotency_key": "once-7",
            })
        self.assertIn("if-match", str(raised.exception).lower())

        create_idea_route = subscribr.ROUTES["ideas.create-channel-idea"]
        with self.assertRaises(subscribr.CliError) as raised:
            subscribr.build_request(create_idea_route, {
                "channel": 7,
                "title": "No transport header",
                "idempotency_key": "unsupported",
            })
        self.assertIn("does not support", str(raised.exception))

    def test_wait_is_not_sent_as_a_write_field_and_points_to_operation_polling(self):
        with self.assertRaises(subscribr.CliError) as raised:
            subscribr.run(["projects", "create-project", "--wait"])
        self.assertEqual(subscribr.EXIT_USAGE, raised.exception.exit_code)
        self.assertIn("operations get-operation", str(raised.exception).lower())

    def test_team_bound_auth_and_versioned_user_agent(self):
        with patch.dict(os.environ, {"SUBSCRIBR_API_TOKEN": "secret"}, clear=True):
            headers = subscribr.get_headers()
        self.assertEqual("Bearer secret", headers["Authorization"])
        self.assertEqual(f"subscribr-cli/{subscribr.VERSION}", headers["User-Agent"])

    def test_body_can_be_loaded_from_json_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as handle:
            json.dump({"profile_schema_version": 2}, handle)
            handle.flush()
            route = {"method": "POST", "path": "/api/v1/channels/{channel}/voices/validate"}
            _, _, body, _ = subscribr.build_request(route, {"channel": 4, "body": "@" + handle.name})
        self.assertEqual({"profile_schema_version": 2}, body)

    def test_raw_body_calls_fail_closed_when_the_method_or_extra_fields_are_ambiguous(self):
        with self.assertRaises(subscribr.CliError) as raised:
            subscribr.build_request({"method": "GET", "path": "/api/v1/operations/{operation}"}, {
                "operation": "6b33d5a6-72c8-4e1e-9bc4-8024f38be3fb",
                "body": {"ignored": True},
            })
        self.assertEqual(subscribr.EXIT_USAGE, raised.exception.exit_code)
        self.assertIn("only supported", str(raised.exception))

        with self.assertRaises(subscribr.CliError) as raised:
            subscribr.build_request({"method": "POST", "path": "/api/v1/channels/{channel}/voices/validate"}, {
                "channel": 4,
                "body": {"profile_schema_version": 2},
                "extra_field": "would previously disappear",
            })
        self.assertEqual(subscribr.EXIT_USAGE, raised.exception.exit_code)
        self.assertIn("cannot be combined", str(raised.exception))

    def test_missing_auth_has_stable_exit_class(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.get_headers()
        self.assertEqual(subscribr.EXIT_AUTH, raised.exception.exit_code)

    def test_help_and_version_are_transport_only(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(0, subscribr.run(["version"]))
        self.assertEqual(f"{subscribr.VERSION}\n", stdout.getvalue())

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(0, subscribr.run(["video", "help"]))
        self.assertIn("optional: --page <value> --per-page <value>", stdout.getvalue())

    def test_run_prints_the_server_json_without_rewriting_it(self):
        payload = {
            "data": [{"id": "820e8400-e29b-41d4-a716-446655440003", "future": {"nested": True}}],
            "pagination": {"current_page": 1, "per_page": 20, "total": 1, "last_page": 1},
        }
        stdout = io.StringIO()
        with patch.object(subscribr, "request", return_value=payload), redirect_stdout(stdout):
            self.assertEqual(0, subscribr.run(["video", "list-voices", "--page", "1", "--per-page", "20"]))

        self.assertEqual(payload, json.loads(stdout.getvalue()))

    def test_authored_docs_define_the_video_slice_and_its_boundaries(self):
        readme = (ROOT / "README.md").read_text()
        skill = (ROOT / "skills/subscribr-api/SKILL.md").read_text()

        # The addendum's command table is the exhaustive command reference;
        # every generated video command must be documented there.
        for command in self.VIDEO_ROUTES:
            action = command.split(".", 1)[1]
            self.assertIn(f"video {action}", skill)

        for document in (readme, skill):
            self.assertIn("video_capability_unavailable", document)
            self.assertIn("video_provisioning_required", document)
            self.assertIn("Team-bound", document)
            self.assertIn("owner/admin-only", document)

        for document in (readme, skill):
            self.assertIn("video:edit", document)
            self.assertIn("video:publish", document)
            self.assertIn("video:generate", document)
            self.assertIn("--idempotency-key", document)
            self.assertIn("--if-match", document)
            self.assertIn("apply-revision", document)
            self.assertIn("immutable", document)
            self.assertIn("--output", document)
            self.assertIn("required_credits", document)
            self.assertIn("cancel-video", document)

        # apply-revision needs explicit user approval before it is called; it
        # publishes an unrecoverable change.
        self.assertIn("confirm", skill.lower())
        self.assertIn("download", skill.lower())



class AgentDiscoveryTest(unittest.TestCase):
    """
    An agent must be able to learn an operation's shape locally. Everything
    here has to work without touching the network.
    """

    WRITE = "scripts.create-channel-script"

    def domain_and_action(self):
        return self.WRITE.split(".", 1)

    def test_the_canonical_base_url_is_the_domain_we_control(self):
        """
        `subscribr.com` is a parked third-party domain. Shipping it as the
        default sent every customer bearer token off-platform.
        """
        self.assertEqual("https://subscribr.ai", subscribr.METADATA["base_url"])
        self.assertNotIn("subscribr.com", json.dumps(subscribr.METADATA))

    def test_token_guidance_points_at_a_host_we_control(self):
        self.assertTrue(subscribr.TOKEN_PAGE_URL.startswith("https://subscribr.ai/"))
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.get_headers()
        self.assertIn(subscribr.TOKEN_PAGE_URL, str(raised.exception))

    def test_domain_help_lists_required_body_fields_for_writes(self):
        domain, action = self.domain_and_action()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(0, subscribr.run([domain, "help"]))
        output = stdout.getvalue()

        # Path param and the three body fields the server actually requires.
        for flag in ("--channel", "--title", "--topic", "--length"):
            self.assertIn(flag, output)

    def test_action_help_describes_fields_without_making_a_request(self):
        domain, action = self.domain_and_action()
        stdout = io.StringIO()
        with patch.object(subscribr, "request", side_effect=AssertionError("help must not call the API")):
            with redirect_stdout(stdout):
                self.assertEqual(0, subscribr.run([domain, action, "--help"]))
        output = stdout.getvalue()

        self.assertIn("Body fields (required)", output)
        self.assertIn("Body fields (optional)", output)
        self.assertIn("Token abilities: scripts:write", output)
        self.assertIn("Example --body:", output)

    def test_action_help_is_not_sent_as_a_request_field(self):
        """`--help` used to become a body field and reach the server."""
        domain, action = self.domain_and_action()
        with patch.object(subscribr, "request", side_effect=AssertionError("help must not call the API")):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, subscribr.run([domain, action, "-h"]))

    def test_unknown_action_fails_as_usage_before_reaching_the_network(self):
        with patch.object(subscribr, "request", side_effect=AssertionError("must not call the API")):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.run(["scripts", "invent-a-route", "--help"])
        self.assertEqual(subscribr.EXIT_USAGE, raised.exception.exit_code)

    def test_required_options_cover_path_params_then_required_body_fields(self):
        route = subscribr.ROUTES[self.WRITE]

        self.assertEqual(
            ["--channel <value>", "--title <string>", "--topic <string>", "--length <integer>"],
            subscribr.required_options(route),
        )

    def test_optional_options_exclude_required_body_fields(self):
        route = subscribr.ROUTES[self.WRITE]
        optional = subscribr.optional_options(route)

        self.assertIn("--voice-id <integer>", optional)
        self.assertNotIn("--title <string>", optional)

    def test_range_labels_never_invent_a_bound(self):
        self.assertEqual("max 255", subscribr.range_label("", None, 255))
        self.assertEqual("min 1", subscribr.range_label("", 1, None))
        self.assertEqual("50..20000", subscribr.range_label("", 50, 20000))
        self.assertEqual("", subscribr.range_label("", None, None))

    def test_doctor_reports_the_bound_team_from_the_real_envelope(self):
        payload = {"team": {
            "id": 7,
            "name": "Acme",
            "user_role": "owner",
            "subscription": {"plan": "Automation"},
        }}
        stdout = io.StringIO()
        with patch.dict(os.environ, {"SUBSCRIBR_API_TOKEN": "t"}, clear=True), \
                patch.object(subscribr, "request", return_value=payload), \
                redirect_stdout(stdout):
            self.assertEqual(0, subscribr.run(["doctor"]))
        output = stdout.getvalue()

        self.assertIn("Team 7 (Acme)", output)
        self.assertIn("owner", output)
        self.assertIn("Automation", output)

    def test_doctor_without_a_token_explains_the_fix_and_never_calls_the_api(self):
        stdout = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(subscribr, "request", side_effect=AssertionError("must not call the API")), \
                redirect_stdout(stdout):
            self.assertEqual(subscribr.EXIT_AUTH, subscribr.run(["doctor"]))
        output = stdout.getvalue()

        self.assertIn("Token          MISSING", output)
        self.assertIn(subscribr.TOKEN_PAGE_URL, output)

    def test_doctor_surfaces_the_underlying_failure_reason(self):
        error = subscribr.CliError("Cannot reach x: bad cert", subscribr.EXIT_TRANSIENT, "bad cert")
        stdout = io.StringIO()
        with patch.dict(os.environ, {"SUBSCRIBR_API_TOKEN": "t"}, clear=True), \
                patch.object(subscribr, "request", side_effect=error), \
                redirect_stdout(stdout):
            self.assertEqual(subscribr.EXIT_TRANSIENT, subscribr.run(["doctor"]))

        self.assertIn("bad cert", stdout.getvalue())


class TransportTrustTest(unittest.TestCase):
    def test_production_uses_the_system_trust_store(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(subscribr.ssl_context())

    def test_a_missing_ca_bundle_is_a_usage_error(self):
        with patch.dict(os.environ, {"SUBSCRIBR_CA_BUNDLE": "/nope/missing.pem"}, clear=True):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.ssl_context()
        self.assertEqual(subscribr.EXIT_USAGE, raised.exception.exit_code)

    def test_certificate_and_dns_failures_are_not_treated_as_transient(self):
        import socket as socket_module
        import ssl as ssl_module

        self.assertTrue(subscribr.permanent_network_failure(ssl_module.SSLError("boom")))
        self.assertTrue(subscribr.permanent_network_failure(socket_module.gaierror("boom")))
        self.assertTrue(subscribr.permanent_network_failure("CERTIFICATE_VERIFY_FAILED"))
        self.assertFalse(subscribr.permanent_network_failure("connection reset by peer"))

    def test_a_certificate_failure_reports_the_reason_and_does_not_retry(self):
        attempts = []

        def urlopen(request, timeout=None, context=None):
            attempts.append(request)
            raise urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")

        with patch.dict(os.environ, {"SUBSCRIBR_API_TOKEN": "t"}, clear=True), \
                patch("urllib.request.urlopen", urlopen):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.request("GET", "/api/v1/team")

        self.assertEqual(1, len(attempts), "a certificate failure must not be retried")
        self.assertIn("CERTIFICATE_VERIFY_FAILED", str(raised.exception))


class PackagingTest(unittest.TestCase):
    def test_declared_versions_stay_in_lockstep(self):
        """package.json, plugin.json, the Claude Code plugin manifests, and the CLI
        must agree, or `doctor` and the User-Agent report a version the registry
        never published, and `/plugin install` ships a version nobody can match."""
        package = json.loads((ROOT / "package.json").read_text())
        plugin = json.loads((ROOT / "plugin.json").read_text())
        claude_plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        marketplace = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())

        self.assertEqual(subscribr.VERSION, package["version"])
        self.assertEqual(subscribr.VERSION, plugin["version"])
        self.assertEqual(subscribr.VERSION, claude_plugin["version"])
        for entry in marketplace["plugins"]:
            self.assertEqual(subscribr.VERSION, entry["version"])


class VerifyPackageVideoGuardTest(unittest.TestCase):
    """`scripts/verify_package.py` must fail closed on a Video operation it
    has never reviewed, even when that operation reuses an existing write
    shape (idempotency/concurrency/abilities) byte-for-byte. Both cases here
    were confirmed passing verification before the fix."""

    def _temp_copy(self) -> Path:
        temp_root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_root, ignore_errors=True)
        destination = temp_root / "repo"
        shutil.copytree(ROOT, destination, ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"))
        return destination

    @staticmethod
    def _mutate_operations(repo: Path, mutate) -> None:
        """Applies `mutate` to a parsed copy of operations.json, then refreshes
        its provenance digest so the mutation is judged on its own merits,
        not rejected earlier by the unrelated artifact-digest check."""
        operations_path = repo / "skills/subscribr-api/references/operations.json"
        provenance_path = repo / "skills/subscribr-api/references/provenance.json"
        operations = json.loads(operations_path.read_text())
        mutate(operations)
        operations_path.write_text(json.dumps(operations, indent=2) + "\n")
        provenance = json.loads(provenance_path.read_text())
        provenance["artifacts"]["skills/subscribr-api/references/operations.json"] = hashlib.sha256(
            operations_path.read_bytes()
        ).hexdigest()
        provenance_path.write_text(json.dumps(provenance, indent=2) + "\n")

    @staticmethod
    def _run_verify_package(repo: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(repo / "scripts/verify_package.py")],
            capture_output=True,
            text=True,
        )

    def test_the_real_package_still_passes(self):
        result = self._run_verify_package(ROOT)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("package verification passed", result.stdout)

    def test_a_new_irreversible_operation_cannot_pass_by_sharing_the_edit_shape(self):
        """CONFIRMED repro: a synthetic videoDeleteProjectPermanently, with the
        exact idempotency+concurrency shape of an ordinary edit write and
        abilities == ["video:edit"], used to validate silently because only
        the nine/five known ids were checked by shape. It must now fail
        before shape is even considered, because the id is unreviewed."""

        def add_delete_operation(operations: dict) -> None:
            edit_write = copy.deepcopy(operations["operations"]["video.remove-music"])
            edit_write["operation_id"] = "videoDeleteProjectPermanently"
            operations["operations"]["video.delete-project-permanently"] = edit_write
            operations["required_operation_ids"].append("videoDeleteProjectPermanently")

        repo = self._temp_copy()
        self._mutate_operations(repo, add_delete_operation)

        result = self._run_verify_package(repo)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("package verification failed", result.stderr)
        self.assertIn("videoDeleteProjectPermanently", result.stderr)
        self.assertIn("unreviewed", result.stderr)

    def test_an_unnamed_operation_cannot_pass_by_sharing_a_read_shape(self):
        """CONFIRMED repro: cloning video.get-avatar into a new video.rogue-read
        key, with a fresh operation_id and refreshed provenance, used to
        validate silently and print "108 operations" because only the HTTP
        method decided the read branch. It must now fail because the id was
        never named as reviewed."""

        def add_rogue_read(operations: dict) -> None:
            read = copy.deepcopy(operations["operations"]["video.get-avatar"])
            read["operation_id"] = "videoRogueRead"
            operations["operations"]["video.rogue-read"] = read
            operations["required_operation_ids"].append("videoRogueRead")

        repo = self._temp_copy()
        self._mutate_operations(repo, add_rogue_read)

        result = self._run_verify_package(repo)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("package verification failed", result.stderr)
        self.assertIn("videoRogueRead", result.stderr)
        self.assertIn("unreviewed", result.stderr)

    def test_an_irreversible_looking_id_is_rejected_even_once_named_as_an_edit_write(self):
        """Defense in depth: even if a human miscategorizes a dangerous-looking
        id straight into VIDEO_EDIT_WRITE_OPERATIONS (so the inventory check
        above no longer catches it), the keyword gate must still refuse to
        let it validate as an ordinary edit."""

        def add_delete_operation(operations: dict) -> None:
            edit_write = copy.deepcopy(operations["operations"]["video.remove-music"])
            edit_write["operation_id"] = "videoDeleteProjectPermanently"
            operations["operations"]["video.delete-project-permanently"] = edit_write
            operations["required_operation_ids"].append("videoDeleteProjectPermanently")

        repo = self._temp_copy()
        self._mutate_operations(repo, add_delete_operation)
        verify_package_path = repo / "scripts/verify_package.py"
        source = verify_package_path.read_text()
        marked_source = source.replace(
            '"videoDiscardEdit",\n}',
            '"videoDiscardEdit", "videoDeleteProjectPermanently",\n}',
            1,
        )
        self.assertNotEqual(source, marked_source, "VIDEO_EDIT_WRITE_OPERATIONS literal moved; update this test")
        verify_package_path.write_text(marked_source)

        result = self._run_verify_package(repo)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("package verification failed", result.stderr)
        self.assertIn("videoDeleteProjectPermanently", result.stderr)
        self.assertIn("irreversible or privileged", result.stderr)


class RequestTest(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "SUBSCRIBR_API_TOKEN": "secret",
            "SUBSCRIBR_API_BASE_URL": "https://example.test",
        }, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_get_retries_transient_transport_failure(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true}'
        with patch("urllib.request.urlopen", side_effect=[urllib.error.URLError("down"), response]) as opener, patch("time.sleep"):
            self.assertEqual({"ok": True}, subscribr.request("GET", "/api/v1/team"))
        self.assertEqual(2, opener.call_count)

    def test_unkeyed_write_is_never_retried(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")) as opener:
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.request("POST", "/api/v1/projects", {"title": "A"})
        self.assertEqual(1, opener.call_count)
        self.assertEqual(subscribr.EXIT_TRANSIENT, raised.exception.exit_code)

    def test_keyed_write_can_retry_the_identical_request(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"data":{"id":"project:v1:idea:1"}}'
        with patch("urllib.request.urlopen", side_effect=[urllib.error.URLError("down"), response]) as opener, patch("time.sleep"):
            result = subscribr.request("POST", "/api/v1/projects", {"title": "A"}, {"Idempotency-Key": "same"})
        self.assertEqual("project:v1:idea:1", result["data"]["id"])
        self.assertEqual(2, opener.call_count)

    def test_numeric_retry_after_is_honored_without_retrying_early(self):
        error = urllib.error.HTTPError(
            "https://example.test",
            429,
            "Rate limited",
            {"Retry-After": "3"},
            io.BytesIO(b'{"error":{"code":"rate_limited"}}'),
        )
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true}'

        with patch("urllib.request.urlopen", side_effect=[error, response]) as opener, patch("time.sleep") as sleeper:
            self.assertEqual({"ok": True}, subscribr.request("GET", "/api/v1/team"))

        self.assertEqual(2, opener.call_count)
        sleeper.assert_called_once_with(3.0)

    def test_http_date_retry_after_is_parsed_relative_to_the_current_time(self):
        current = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(
            4.0,
            subscribr.retry_after_seconds("Wed, 29 Jul 2026 12:00:04 GMT", current),
        )

    def test_retry_after_above_the_wait_threshold_returns_429_without_retrying(self):
        error = urllib.error.HTTPError(
            "https://example.test",
            429,
            "Rate limited",
            {"Retry-After": "6"},
            io.BytesIO(b'{"error":{"code":"rate_limited"}}'),
        )

        with patch("urllib.request.urlopen", side_effect=error) as opener, patch("time.sleep") as sleeper:
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.request("GET", "/api/v1/team")

        self.assertEqual(subscribr.EXIT_RATE_LIMIT, raised.exception.exit_code)
        self.assertEqual(1, opener.call_count)
        sleeper.assert_not_called()

    def test_http_errors_map_to_stable_exit_classes(self):
        error = urllib.error.HTTPError("https://example.test", 409, "Conflict", {}, io.BytesIO(b'{"error":{"code":"revision_conflict"}}'))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.request("PATCH", "/api/v1/projects/project%3Av1%3Aidea%3A1", {}, max_attempts=1)
        self.assertEqual(subscribr.EXIT_CONFLICT, raised.exception.exit_code)
        self.assertEqual("revision_conflict", raised.exception.detail["error"]["code"])

    def test_bare_404_on_a_review_and_fix_operation_is_not_reclassified(self):
        """The Review & Fix reads/writes shipped in 2.2.0 (and
        videoReplaceWithMedia in 2.3.0) are fully deployed as of 2026-08-31;
        a bare 404 there means plain not-found, same as any other operation."""
        error = urllib.error.HTTPError("https://example.test", 404, "Not Found", {}, io.BytesIO(b"Not Found"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.request(
                    "GET", "/api/v1/video/projects/proj_1", None, {}, max_attempts=1,
                    operation="video.get-project",
                )
        self.assertEqual("HTTP 404", str(raised.exception))

    def test_typed_404_on_a_video_operation_is_not_reclassified(self):
        """A genuine not-found is already typed — it passes through
        unchanged."""
        error = urllib.error.HTTPError(
            "https://example.test", 404, "Not Found", {}, io.BytesIO(b'{"error":{"code":"not_found"}}')
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.request(
                    "GET", "/api/v1/video/projects/proj_1", None, {}, max_attempts=1,
                    operation="video.get-project",
                )
        self.assertEqual(subscribr.EXIT_VALIDATION, raised.exception.exit_code)
        self.assertEqual("HTTP 404", str(raised.exception))
        self.assertEqual("not_found", raised.exception.detail["error"]["code"])

    def test_bare_404_on_a_long_standing_video_read_is_not_reclassified(self):
        """The original nine video reads predate 2.2.0 and have always been
        live; a bare 404 there still just means not-found."""
        error = urllib.error.HTTPError("https://example.test", 404, "Not Found", {}, io.BytesIO(b"Not Found"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.request(
                    "GET", "/api/v1/video/avatars/x", None, {}, max_attempts=1,
                    operation="video.get-avatar",
                )
        self.assertEqual("HTTP 404", str(raised.exception))

    def test_bare_404_on_a_non_video_operation_is_not_reclassified(self):
        error = urllib.error.HTTPError("https://example.test", 404, "Not Found", {}, io.BytesIO(b"Not Found"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.request(
                    "GET", "/api/v1/team", None, {}, max_attempts=1,
                    operation="team.get-team",
                )
        self.assertEqual("HTTP 404", str(raised.exception))

    def test_bare_404_without_an_operation_argument_is_not_reclassified(self):
        """`operation` is optional; callers that don't pass it (or a caller
        outside `run()`) get the original, unadorned message."""
        error = urllib.error.HTTPError("https://example.test", 404, "Not Found", {}, io.BytesIO(b"Not Found"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.request("GET", "/api/v1/video/projects/proj_1", None, {}, max_attempts=1)
        self.assertEqual("HTTP 404", str(raised.exception))

    def test_run_passes_the_resolved_operation_key_to_request(self):
        with patch.object(subscribr, "request", return_value={"data": {}}) as mock_request, \
                redirect_stdout(io.StringIO()):
            subscribr.run(["video", "get-project", "--project", "proj_1"])
        self.assertEqual("video.get-project", mock_request.call_args.kwargs.get("operation"))


class DownloadOutputTest(unittest.TestCase):
    """`--output` streams a response's `download_url` to disk instead of
    printing it, and never sends our bearer token to that third-party host."""

    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "SUBSCRIBR_API_TOKEN": "secret",
            "SUBSCRIBR_API_BASE_URL": "https://example.test",
        }, clear=True)
        self.environment.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        # `cdn.example` is a reserved, non-resolving RFC 2606 test domain, so
        # every test below stands in a real (public) DNS answer for it. Tests
        # that exercise `ensure_public_download_host` itself override this.
        self.dns = patch("socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
        ])
        self.dns.start()

    def tearDown(self):
        self.environment.stop()
        self.dns.stop()
        self.temp_dir.cleanup()

    def test_find_download_url_reads_the_top_level_or_the_data_envelope(self):
        self.assertEqual("https://cdn.example/x.mp4", subscribr.find_download_url({"download_url": "https://cdn.example/x.mp4"}))
        self.assertEqual(
            "https://cdn.example/y.mp4",
            subscribr.find_download_url({"data": {"download_url": "https://cdn.example/y.mp4"}}),
        )
        self.assertIsNone(subscribr.find_download_url({"data": {"id": "proj_1"}}))
        self.assertIsNone(subscribr.find_download_url(["not", "a", "dict"]))
        self.assertIsNone(subscribr.find_download_url(None))

    def test_download_to_file_streams_to_a_temp_file_and_renames_on_success(self):
        response = MagicMock()
        response.__enter__.return_value.read.side_effect = [b"chunk-one", b"chunk-two", b""]
        destination = Path(self.temp_dir.name) / "nested" / "final.mp4"

        with patch("urllib.request.urlopen", return_value=response) as opener:
            written = subscribr.download_to_file("https://cdn.example/final.mp4?sig=abc", destination)

        self.assertEqual(len(b"chunk-onechunk-two"), written)
        self.assertEqual(b"chunk-onechunk-two", destination.read_bytes())
        # No leftover partial file next to the finished download.
        self.assertEqual([destination.name], [entry.name for entry in destination.parent.iterdir()])

        sent_request = opener.call_args[0][0]
        self.assertNotIn("Authorization", sent_request.headers)
        self.assertNotIn("authorization", {key.lower() for key in sent_request.headers})

    def test_download_to_file_rejects_a_non_http_url_without_a_request(self):
        destination = Path(self.temp_dir.name) / "final.mp4"
        with patch("urllib.request.urlopen", side_effect=AssertionError("must not be called")):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.download_to_file("ftp://cdn.example/final.mp4", destination)
        self.assertEqual(subscribr.EXIT_VALIDATION, raised.exception.exit_code)

    def test_download_to_file_maps_http_errors_and_cleans_up_the_temp_file(self):
        destination = Path(self.temp_dir.name) / "final.mp4"
        error = urllib.error.HTTPError("https://cdn.example/final.mp4", 403, "Forbidden", {}, io.BytesIO(b""))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.download_to_file("https://cdn.example/final.mp4?sig=abc", destination)
        self.assertEqual(subscribr.EXIT_AUTH, raised.exception.exit_code)
        self.assertEqual([], list(destination.parent.iterdir()))
        # The signed URL itself must never appear in an error message.
        self.assertNotIn("sig=abc", str(raised.exception))

    def test_download_to_file_treats_url_errors_as_transient(self):
        destination = Path(self.temp_dir.name) / "final.mp4"
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection reset")):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.download_to_file("https://cdn.example/final.mp4?sig=abc", destination)
        self.assertEqual(subscribr.EXIT_TRANSIENT, raised.exception.exit_code)

    def test_is_public_download_address_classifies_private_loopback_link_local_and_public(self):
        self.assertTrue(subscribr.is_public_download_address("93.184.216.34"))
        self.assertTrue(subscribr.is_public_download_address("2606:2800:220:1:248:1893:25c8:1946"))
        self.assertFalse(subscribr.is_public_download_address("10.0.0.5"))
        self.assertFalse(subscribr.is_public_download_address("172.16.0.5"))
        self.assertFalse(subscribr.is_public_download_address("192.168.1.5"))
        self.assertFalse(subscribr.is_public_download_address("127.0.0.1"))
        self.assertFalse(subscribr.is_public_download_address("::1"))
        # The cloud metadata endpoint. 169.254.0.0/16 is link-local.
        self.assertFalse(subscribr.is_public_download_address("169.254.169.254"))
        self.assertFalse(subscribr.is_public_download_address("0.0.0.0"))

    def test_download_to_file_rejects_a_download_url_resolving_to_a_private_address(self):
        """A tampered/compromised backend response pointing download_url at an
        internal address (or the cloud metadata endpoint) must never reach
        urlopen — this is checked before any request is made."""
        destination = Path(self.temp_dir.name) / "final.mp4"
        self.dns.stop()
        with patch("socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", 0)),
        ]):
            with patch("urllib.request.urlopen", side_effect=AssertionError("must not be called")):
                with self.assertRaises(subscribr.CliError) as raised:
                    subscribr.download_to_file("https://internal.example/latest/meta-data/", destination)
        self.dns.start()
        self.assertEqual(subscribr.EXIT_VALIDATION, raised.exception.exit_code)
        self.assertIn("not a public address", str(raised.exception))

    def test_download_to_file_reports_dns_failure_as_transient(self):
        destination = Path(self.temp_dir.name) / "final.mp4"
        self.dns.stop()
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
            with patch("urllib.request.urlopen", side_effect=AssertionError("must not be called")):
                with self.assertRaises(subscribr.CliError) as raised:
                    subscribr.download_to_file("https://cdn.example/final.mp4?sig=abc", destination)
        self.dns.start()
        self.assertEqual(subscribr.EXIT_TRANSIENT, raised.exception.exit_code)

    def test_public_host_redirect_handler_blocks_a_redirect_to_a_private_address(self):
        handler = subscribr.PublicHostRedirectHandler()
        request = urllib.request.Request("https://cdn.example/final.mp4")
        with patch("socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.5", 0)),
        ]):
            with self.assertRaises(subscribr.CliError) as raised:
                handler.redirect_request(request, None, 302, "Found", {}, "https://internal.example/secret")
        self.assertEqual(subscribr.EXIT_VALIDATION, raised.exception.exit_code)
        self.assertIn("not a public address", str(raised.exception))

    def test_public_host_redirect_handler_blocks_a_scheme_downgrade(self):
        handler = subscribr.PublicHostRedirectHandler()
        request = urllib.request.Request("https://cdn.example/final.mp4")
        with self.assertRaises(subscribr.CliError) as raised:
            handler.redirect_request(request, None, 302, "Found", {}, "ftp://cdn.example/final.mp4")
        self.assertEqual(subscribr.EXIT_VALIDATION, raised.exception.exit_code)

    def test_public_host_redirect_handler_allows_a_redirect_to_another_public_host(self):
        handler = subscribr.PublicHostRedirectHandler()
        request = urllib.request.Request("https://cdn.example/final.mp4", headers={"User-Agent": "x"})
        redirected = handler.redirect_request(request, None, 302, "Found", {}, "https://other-cdn.example/final.mp4")
        self.assertEqual("https://other-cdn.example/final.mp4", redirected.full_url)

    def test_run_with_output_downloads_and_never_prints_the_signed_url(self):
        # `--output` is generic transport behavior, not Video-specific, so this
        # exercises it against `team get-team` rather than a Video route.
        payload = {"data": {"download_url": "https://cdn.example/final.mp4?sig=super-secret", "expires_at": "2026-08-27T13:00:00Z"}}
        destination = Path(self.temp_dir.name) / "final.mp4"

        stdout = io.StringIO()
        with patch.object(subscribr, "request", return_value=payload), \
                patch.object(subscribr, "download_to_file", return_value=1234) as downloader, \
                redirect_stdout(stdout):
            self.assertEqual(0, subscribr.run([
                "team", "get-team",
                "--output", str(destination),
            ]))

        downloader.assert_called_once_with("https://cdn.example/final.mp4?sig=super-secret", destination)
        output = stdout.getvalue()
        self.assertNotIn("sig=super-secret", output)
        self.assertNotIn("secret", output)  # the bearer token must not leak either
        result = json.loads(output)
        self.assertEqual({"downloaded": True, "path": str(destination), "bytes": 1234, "url_expires_at": "2026-08-27T13:00:00Z"}, result)

    def test_run_with_output_fails_closed_when_the_response_has_no_download_url(self):
        with patch.object(subscribr, "request", return_value={"data": {"id": "team_1"}}):
            with self.assertRaises(subscribr.CliError) as raised:
                subscribr.run([
                    "team", "get-team",
                    "--output", str(Path(self.temp_dir.name) / "final.mp4"),
                ])
        self.assertEqual(subscribr.EXIT_USAGE, raised.exception.exit_code)

    def test_split_transport_options_extracts_output_alongside_wait(self):
        remaining, wait, output = subscribr.split_transport_options(["--project", "p1", "--output", "./out.mp4", "--wait"])
        self.assertEqual(["--project", "p1"], remaining)
        self.assertTrue(wait)
        self.assertEqual("./out.mp4", output)

    def test_output_without_a_path_is_a_usage_error(self):
        with self.assertRaises(subscribr.CliError) as raised:
            subscribr.split_transport_options(["--output"])
        self.assertEqual(subscribr.EXIT_USAGE, raised.exception.exit_code)


if __name__ == "__main__":
    unittest.main()
