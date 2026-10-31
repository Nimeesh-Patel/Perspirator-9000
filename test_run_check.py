"""Contract tests for the advisory, Markdown-owned run-report checker."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_check


class RunCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.vault = Path(temporary.name)
        self.readme = self.vault / "memory/perspirator/runs/README.md"
        self.readme.parent.mkdir(parents=True)

    def write_fields(self, *fields):
        self.readme.write_text(
            "# Run reports\n\n## Required contents\n\n"
            + "\n".join(f"- **{field}:** explanation" for field in fields)
            + "\n\n## Other section\n\n- **Not a required field:** ignored\n",
            encoding="utf-8",
        )

    def run_main(self, *reports, json_output=True):
        output, errors = io.StringIO(), io.StringIO()
        argv = ["--vault", str(self.vault), *map(str, reports)]
        if json_output:
            argv.append("--json")
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = run_check.main(argv)
        return result, output.getvalue(), errors.getvalue()

    def test_editing_readme_changes_field_names_without_code_changes(self):
        report = self.vault / "chosen.md"
        report.write_text("## New field\nAn explanation.\n", encoding="utf-8")
        self.write_fields("Original field")
        result, output, errors = self.run_main(report)
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["reports"][0]["fields"],
                         {"Original field": "absent"})
        self.write_fields("New field")
        result, output, errors = self.run_main(report)
        payload = json.loads(output)
        self.assertEqual(result, 0)
        self.assertEqual(errors, "")
        self.assertEqual(payload["readme"], str(self.readme.resolve()))
        self.assertEqual(payload["fields"], ["New field"])
        self.assertEqual(payload["reports"][0]["fields"], {"New field": "present"})

    def test_heading_levels_bold_labels_case_and_trailing_colons(self):
        fields = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth",
                  "Bold inside", "Bold outside", "Setext", "Absent"]
        self.write_fields(*fields)
        report = self.vault / "labels.md"
        report.write_text(
            "# FIRST:\n## **second**:\n### Third: ###\n#### fourth\n##### Fifth:\n"
            "###### sixth\n**BOLD INSIDE:** explanation\n"
            "**Bold outside**: explanation\nSetext:\n---\n"
            "## Absent extra words\nOrdinary Absent prose.\n",
            encoding="utf-8",
        )
        result = run_check.inspect_report(report, fields)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["fields"],
                         {field: "absent" if field == "Absent" else "present"
                          for field in fields})

    def test_absent_fields_are_advisory_in_text_output(self):
        self.write_fields("Required label")
        report = self.vault / "short.md"
        report.write_text("A short report.\n", encoding="utf-8")
        result, output, errors = self.run_main(report, json_output=False)
        self.assertEqual(result, 0)
        self.assertIn("Required label: absent", output)
        self.assertNotIn("bad", output.casefold())
        self.assertEqual(errors, "")

    def test_fenced_examples_and_indented_code_do_not_supply_headings(self):
        self.write_fields("Example", "Indented", "Actual")
        report = self.vault / "examples.md"
        report.write_text(
            "````markdown\n## Example\n**Example:**\n```\n## Example\n````\n"
            "~~~\n## Example\n~~~\n    ## Indented\n## Actual\n",
            encoding="utf-8",
        )
        result = run_check.inspect_report(report, ["Example", "Indented", "Actual"])
        self.assertEqual(result["fields"],
                         {"Example": "absent", "Indented": "absent", "Actual": "present"})

    def test_unreadable_or_non_utf8_report_is_not_an_absence_claim(self):
        self.write_fields("Task")
        invalid = self.vault / "invalid.md"
        invalid.write_bytes(b"\xff\xfe")
        directory = self.vault / "directory.md"
        directory.mkdir()
        for report in (invalid, directory, self.vault / "missing.md"):
            with self.subTest(report=report.name):
                result, output, errors = self.run_main(report)
                payload = json.loads(output)
                self.assertEqual(result, 2)
                self.assertEqual(payload["reports"], [{
                    "path": str(report.resolve()), "status": "unreadable", "fields": {},
                }])

    def test_missing_empty_or_fieldless_required_section_exits_two(self):
        report = self.vault / "chosen.md"
        report.write_text("## Task\n", encoding="utf-8")
        for text in ("# No section\n", "## Required contents\n\n## Later\n",
                     "## Required contents\n\nPlain prose only.\n"):
            with self.subTest(readme=text):
                self.readme.write_text(text, encoding="utf-8")
                result, output, errors = self.run_main(report)
                self.assertEqual(result, 2)
                self.assertEqual(output, "")
                self.assertIn("error:", errors)
                self.assertIn("Required contents", errors)

    def test_missing_or_non_utf8_readme_exits_two_with_message(self):
        report = self.vault / "chosen.md"
        report.write_text("## Task\n", encoding="utf-8")
        result, output, errors = self.run_main(report)
        self.assertEqual(result, 2)
        self.assertIn("cannot read run-report README", errors)
        self.readme.write_bytes(b"\xff")
        result, output, errors = self.run_main(report)
        self.assertEqual(result, 2)
        self.assertIn("cannot read run-report README", errors)

    def test_only_explicit_reports_and_required_readme_are_read(self):
        self.write_fields("Task")
        first, second = self.vault / "one.md", self.vault / "two.md"
        first.write_text("## Task\n", encoding="utf-8")
        second.write_text("An omitted field.\n", encoding="utf-8")
        (self.readme.parent / "other-bot-record.md").write_bytes(b"\xff")
        observed = []
        original = Path.read_text

        def track_read(path, *args, **kwargs):
            observed.append(path.resolve())
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", track_read):
            result, output, errors = self.run_main(first, second)
        self.assertEqual(result, 0)
        self.assertEqual(observed, [self.readme.resolve(), first.resolve(), second.resolve()])
        self.assertEqual(len(json.loads(output)["reports"]), 2)

    def test_explicit_contract_and_section_share_the_existing_checker(self):
        contract = self.vault / "memory/policies/Exchange.md"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "## Handoff inputs\n- **Problem:** explanation\n"
            "## Handoff returns\n- **Reviewed revision:** identity\n"
            "- **Remaining criticism:** unresolved content\n", encoding="utf-8")
        record = self.vault / "reply.md"
        record.write_text("## Reviewed revision\nExact hash.\n", encoding="utf-8")
        # No default README exists: an explicit contract must not depend on it.
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = run_check.main([
                "--vault", str(self.vault), "--contract", "memory/policies/Exchange.md",
                "--section", "## Handoff returns", "--json", str(record)])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)  # Missing content remains advisory.
        self.assertEqual(payload["contract"], str(contract.resolve()))
        self.assertEqual(payload["fields"], ["Reviewed revision", "Remaining criticism"])
        self.assertEqual(payload["reports"][0]["fields"], {
            "Reviewed revision": "present", "Remaining criticism": "absent"})
        contract.write_text("## Handoff returns\n- **Changed field:** new meaning\n",
                            encoding="utf-8")
        self.assertEqual(run_check.required_fields(
            self.vault, contract, "## Handoff returns")[1], ["Changed field"])

    def test_unusable_explicit_contract_never_falls_back_to_default(self):
        self.write_fields("Default field")
        contract = self.vault / "invalid.md"
        for content in (b"\xff", b"## Other\n- **Wrong:** field\n"):
            contract.write_bytes(content)
            with self.assertRaises(ValueError):
                run_check.required_fields(self.vault, contract, "## Handoff inputs")
        with self.assertRaisesRegex(ValueError, "must be a ## heading"):
            run_check.required_fields(self.vault, contract, "### Handoff inputs")

    def test_contract_examples_cannot_add_fields_or_end_the_real_section(self):
        contract = self.vault / "exchange.md"
        contract.write_text(
            "## Handoff inputs\n- **Real:** field\n"
            "````markdown\n## Other\n- **Example only:** ignored\n```\n"
            "- **Still an example:** ignored\n````\n"
            "    - **Indented code:** ignored\n- **Also real:** field\n"
            "## Handoff returns\n- **Return:** separate\n", encoding="utf-8")
        self.assertEqual(run_check.required_fields(
            self.vault, contract, "## Handoff inputs")[1], ["Real", "Also real"])


if __name__ == "__main__":
    unittest.main()
