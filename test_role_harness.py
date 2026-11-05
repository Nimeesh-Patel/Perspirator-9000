"""Adversarial identity, boundedness and host-neutral role packet checks."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import role_harness as rh


class RoleHarnessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.vault = self.base / "vault"
        self.roles = self.vault / "memory/perspirator"
        self.roles.mkdir(parents=True)
        self.write(self.vault / rh.RUNTIME, "---\nstatus: active\n---\nFull runtime.\n")
        self.write(self.vault / rh.DELEGATION, "---\nstatus: active\n---\n## Contract\nExact role meaning lives here.\n")
        self.role_path = self.roles / "Critic role.md"
        self.write_role(self.role_path)
        self.policy = self.vault / "memory/policies/Chosen.md"
        self.write(self.policy, "---\nstatus: active\ntype: policy\n---\n## Problem\nA problem.\n")
        self.target = self.base / "draft.md"
        self.source = self.base / "outside-vault.md"
        self.write(self.target, "An exact draft.\r\nWith a fence: ```\n")
        self.write(self.source, "A complete source. £\n")
        self.request_path = self.base / "request.json"
        self.request = {
            "seed": {"text": "User asks for bounded criticism.",
                     "provenance": {"kind": "user-message", "locator": "this chat"}},
            "question": "Which supplied premise conflicts with the draft?",
            "reviewed_target": {"path": "draft.md", "provenance": {"kind": "draft"}},
            "sources": [{"path": str(self.source), "provenance": {"kind": "local-file"},
                         "completeness": "partial", "unobserved": ["the rest of the book"]}],
            "policies": ["memory/policies/Chosen.md"],
            "authority": {"work": "read-only criticism", "writes": [],
                          "provenance": "current user request"},
            "dependencies": [], "expected_return": "compact findings and unresolved problems",
            "extension": {"preserved": "request-specific metadata"},
        }
        self.save_request()
        self.output = self.base / "packet"

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def write_role(self, path, role="critic", status="active", problem="The draft has blind spots."):
        self.write(path, f"---\ntitle: Critic\ntype: configuration\nstatus: {status}\nrole: {role}\n---\n## Problem\n{problem}\n## Questions\n- C1 asks a question.\n")

    def save_request(self):
        self.write(self.request_path, json.dumps(self.request, ensure_ascii=False))

    def build(self, limit=1000000, output=None):
        return rh.build_packet(self.vault, "critic", self.request_path,
                               output or self.output, limit)

    def response(self, packet):
        indexed = {item["id"]: item for item in packet["artifacts"]}
        return {"packet_id": packet["packet_id"],
                "reviewed_target_sha256": indexed["reviewed_target"]["sha256"],
                "coverage": {"status": "partial", "inspected": ["reviewed_target", "sources/0"],
                             "limits": ["The source is partial."]},
                "findings": [{"role_question": "C1", "argument": "A connecting argument."}],
                "unresolved": [], "extension": {"keep": True}}

    def rewrite_packet(self, packet, *, recompute=True):
        if recompute:
            packet["packet_id"] = rh.digest(rh.canonical(rh.packet_body(packet)))
        (self.output / "packet.json").write_bytes(rh.canonical(packet) + b"\n")
        (self.output / "packet.md").write_bytes(rh.markdown(packet))

    def test_discovery_has_current_identity_and_rejects_duplicate_or_malformed_roles(self):
        roles = rh.list_roles(self.vault)
        self.assertEqual([item["id"] for item in roles], ["critic"])
        self.assertEqual(roles[0]["problem"], "The draft has blind spots.")
        self.assertEqual(roles[0]["sha256"], rh.digest(self.role_path.read_bytes()))
        other = self.roles / "nested/Other.md"
        self.write_role(other, status="draft")
        self.assertEqual(len(rh.list_roles(self.vault)), 1)
        self.write_role(other)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            rh.list_roles(self.vault)
        self.write_role(other, role="bad/id")
        with self.assertRaisesRegex(ValueError, "malformed"):
            rh.list_roles(self.vault)
        self.write_role(other, role="other", problem="")
        with self.assertRaisesRegex(ValueError, "role problem"):
            rh.list_roles(self.vault)

    def test_snapshots_are_lossless_deterministic_and_keep_extensions(self):
        packet = self.build()
        other = self.build(output=self.base / "another-packet")
        self.assertEqual(packet["packet_id"], other["packet_id"])
        indexed = rh.validate_packet(packet)
        self.assertEqual(indexed["reviewed_target"]["text"].encode("utf-8"), self.target.read_bytes())
        self.assertEqual(indexed["sources/0"]["text"].encode("utf-8"), self.source.read_bytes())
        self.assertEqual(packet["request"]["extension"], self.request["extension"])
        self.assertEqual(packet["request"]["sources"][0]["completeness"], "partial")
        response_path = self.base / "response.json"
        self.write(response_path, json.dumps(self.response(packet)))
        checked = rh.check_packet(self.output / "packet.json", response_path)
        self.assertTrue(checked["current"])
        self.assertEqual(checked["response"]["extension"], {"keep": True})
        self.assertIn("no semantic pass or authorization", checked["boundary"])

    def test_failed_role_directory_scan_cannot_claim_complete_discovery(self):
        def denied_walk(root, *, followlinks, onerror):
            yield str(root), [], []
            onerror(PermissionError(13, "access denied", str(root / "unobserved")))

        with patch.object(rh.os, "walk", side_effect=denied_walk):
            with self.assertRaisesRegex(ValueError, "role directory scan unavailable"):
                rh.list_roles(self.vault)

    def test_each_input_revision_invalidates_freshness_including_request_and_contract(self):
        packet = self.build()
        for identity, path in [(item["id"], Path(item["path"])) for item in packet["artifacts"]]:
            with self.subTest(identity=identity):
                original = path.read_bytes()
                path.write_bytes(original + b"\nchanged")
                checked = rh.check_packet(self.output / "packet.json")
                states = {item["id"]: item["status"] for item in checked["freshness"]}
                self.assertFalse(checked["current"])
                self.assertEqual(states[identity], "changed")
                path.write_bytes(original)
        self.target.unlink()
        states = rh.check_packet(self.output / "packet.json")["freshness"]
        self.assertEqual(next(item["status"] for item in states if item["id"] == "reviewed_target"), "unavailable")

    def test_tampering_is_detected_even_when_packet_id_is_recomputed(self):
        packet = self.build()
        packet["artifacts"][0]["text"] += "tampered"
        self.rewrite_packet(packet, recompute=False)
        with self.assertRaisesRegex(ValueError, "packet identity"):
            rh.check_packet(self.output / "packet.json")
        self.rewrite_packet(packet)
        with self.assertRaisesRegex(ValueError, "snapshot identity"):
            rh.check_packet(self.output / "packet.json")

    def test_path_substitution_cannot_expand_the_explicit_input_set(self):
        packet = self.build()
        item = next(value for value in packet["artifacts"] if value["id"] == "reviewed_target")
        item["path"] = str(self.source)
        self.rewrite_packet(packet)
        with self.assertRaisesRegex(ValueError, "path differs"):
            rh.check_packet(self.output / "packet.json")
        self.request["reviewed_target"]["path"] = "../outside.md"
        self.save_request()
        with self.assertRaisesRegex(ValueError, "parent traversal"):
            self.build(output=self.base / "rejected")
        self.request["reviewed_target"]["path"] = str(self.base)
        self.save_request()
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.build(output=self.base / "rejected")

    def test_symlink_inputs_are_refused(self):
        link = self.base / "linked-source.md"
        try:
            link.symlink_to(self.source)
        except OSError:
            self.skipTest("host cannot create symlinks")
        self.request["sources"][0]["path"] = str(link)
        self.save_request()
        with self.assertRaisesRegex(ValueError, "reparse"):
            self.build()

    def test_byte_limit_is_exact_and_no_failed_or_existing_output_is_overwritten(self):
        packet = self.build()
        size = max(len(rh.canonical(packet)) + 1, len(rh.markdown(packet)))
        # Changing the caller limit changes a few encoded digits, so establish
        # the exact final size at this boundary before rebuilding.
        packet["byte_limit"] = size
        packet["packet_id"] = rh.digest(rh.canonical(rh.packet_body(packet)))
        exact = max(len(rh.canonical(packet)) + 1, len(rh.markdown(packet)))
        self.build(exact, self.base / "exact")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            self.build(exact - 1, self.base / "too-small")
        self.assertFalse((self.base / "too-small").exists())
        original = (self.output / "packet.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "must be new"):
            self.build()
        self.assertEqual((self.output / "packet.json").read_bytes(), original)

    def test_invalid_utf8_and_duplicate_json_keys_are_refused(self):
        self.source.write_bytes(b"broken \xff")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            self.build()
        self.assertFalse(self.output.exists())
        self.write(self.source, "Valid again.")
        self.write(self.request_path, '{"seed":{},"seed":{}}')
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            self.build()

    def test_json_number_conversion_cannot_introduce_nonfinite_values(self):
        for token in ("NaN", "Infinity", "-Infinity", "1e400", "-1e400"):
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, "non-finite JSON number"):
                rh.json_text('{"extension":{"nested":[' + token + ']}}')
        self.assertEqual(rh.json_text('{"finite":[1e308,-0.125,42]}'),
                         {"finite": [1e308, -0.125, 42]})

    def test_source_completeness_is_preserved_without_claiming_total_coverage(self):
        self.request["sources"][0]["completeness"] = "reader-specific"
        self.save_request()
        with self.assertRaisesRegex(ValueError, "completeness_explanation"):
            self.build()
        self.request["sources"][0]["completeness_explanation"] = "Only the provider's selected passage."
        self.save_request()
        packet = self.build()
        self.assertEqual(packet["request"]["sources"][0]["completeness"], "reader-specific")

    def test_changed_or_wrong_reviewed_target_and_bad_coverage_are_not_accepted(self):
        packet = self.build()
        response = self.response(packet)
        indexed = rh.validate_packet(packet)
        response["reviewed_target_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "target identity"):
            rh.validate_response(response, packet, indexed)
        response = self.response(packet)
        response["packet_id"] = "different"
        with self.assertRaisesRegex(ValueError, "another packet"):
            rh.validate_response(response, packet, indexed)
        response = self.response(packet)
        response["coverage"]["inspected"] = ["invented-source"]
        with self.assertRaisesRegex(ValueError, "not in packet"):
            rh.validate_response(response, packet, indexed)
        response = self.response(packet)
        response["coverage"]["status"] = "semantic-pass"
        with self.assertRaisesRegex(ValueError, "coverage requires"):
            rh.validate_response(response, packet, indexed)

    def test_source_mutation_during_output_withdraws_own_generated_files(self):
        original = rh.freshness
        calls = 0
        def observing(artifacts):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.source.write_bytes(b"Changed during output")
            return original(artifacts)
        with patch.object(rh, "freshness", side_effect=observing):
            with self.assertRaisesRegex(ValueError, "during packet output"):
                self.build()
        self.assertFalse(self.output.exists())

    def test_markdown_is_bound_to_json_and_cli_smoke_and_stale_exit(self):
        script = Path(rh.__file__)
        listed = subprocess.run([sys.executable, str(script), "list", "--vault", str(self.vault)],
                                capture_output=True, encoding="utf-8")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout)["roles"][0]["id"], "critic")
        made = subprocess.run([sys.executable, str(script), "packet", "--vault", str(self.vault),
                               "--role", "critic", "--request", str(self.request_path),
                               "--out", str(self.output), "--max-bytes", "1000000"],
                              capture_output=True, encoding="utf-8")
        self.assertEqual(made.returncode, 0, made.stderr)
        self.target.write_bytes(b"New revision")
        checked = subprocess.run([sys.executable, str(script), "check", "--packet",
                                   str(self.output / "packet.json")], capture_output=True, encoding="utf-8")
        self.assertEqual(checked.returncode, 1, checked.stderr)
        self.assertFalse(json.loads(checked.stdout)["current"])
        self.write(self.output / "packet.md", "A substituted worker view.")
        with self.assertRaisesRegex(ValueError, "Markdown view"):
            rh.check_packet(self.output / "packet.json")


if __name__ == "__main__":
    unittest.main()
