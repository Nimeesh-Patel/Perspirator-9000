"""Contract tests for the source records and provider envelopes readers share."""

import unittest

from contracts import provider_result, validate_source_record


def record(**fields):
    return {"id": "a", "text": "An exact passage.", "locator": "test://source/a",
            "provenance": {"provider": "test"}, "completeness": "complete",
            "withdrawal_state": "active"} | fields


class ProviderEnvelopeTests(unittest.TestCase):
    def test_provider_constructor_preserves_envelope_contract(self):
        fields = dict(provider="test", capability="read", status="complete",
                      scope={}, freshness={})
        for key, value in (("provider", None), ("provider", " "),
                           ("capability", 1), ("capability", ""),
                           ("status", None), ("status", " "),
                           ("status", "custom"), ("scope", []), ("freshness", None)):
            with self.subTest(field=key, value=value), self.assertRaises(ValueError):
                provider_result(**(fields | {key: value}))
        result = provider_result(**fields, records=iter([1]), errors=("error",),
                                 extension={"kept": True})
        self.assertEqual(result, fields | {"records": [1], "errors": ["error"],
                                         "extension": {"kept": True}})
        custom = provider_result(**(fields | {"status": "custom"}),
                                 status_explanation="Provider-specific observation")
        self.assertEqual(custom["status"], "custom")


class SourceRecordTests(unittest.TestCase):
    def test_locator_is_any_uri_scheme_but_must_be_one(self):
        for locator in ("https://youtu.be/abc?t=61", "calibre-pdf://lib/81/3",
                        "test://source/a"):
            self.assertEqual(validate_source_record(record(locator=locator))["locator"], locator)
        with self.assertRaises(ValueError):
            validate_source_record(record(locator="not-a-uri"))

    def test_source_text_is_preserved_exactly(self):
        text = "  exact source text  \n"
        self.assertEqual(validate_source_record(record(text=text))["text"], text)


if __name__ == "__main__":
    unittest.main()
