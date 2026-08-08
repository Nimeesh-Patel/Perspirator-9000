#!/usr/bin/env python3
"""Stable contracts shared by source and evidence-provider adapters."""

import re


LOCATOR = re.compile(r"^[a-z][a-z0-9+.\-]*://\S+$", re.IGNORECASE)
SOURCE_COMPLETENESS = {"complete", "partial", "indeterminate"}
WITHDRAWAL_STATES = {"active", "withdrawn"}
PROVIDER_STATUSES = {"complete", "partial", "unavailable", "stale",
                     "indeterminate"}


def source_record(source_id, text, locator, *, provider, completeness="complete",
                  withdrawal_state="active", provenance=None, **fields):
    record = {
        "id": str(source_id).strip(),
        "text": text.strip() if isinstance(text, str) else text,
        "locator": locator.strip() if isinstance(locator, str) else locator,
        "provenance": provenance or {"provider": provider},
        "completeness": completeness,
        "withdrawal_state": withdrawal_state,
    }
    record.update(fields)
    return validate_source_record(record)


def validate_source_record(record):
    if not isinstance(record, dict):
        raise ValueError("source record is not an object")
    missing = [key for key in ("id", "text", "locator", "provenance",
                                "completeness", "withdrawal_state")
               if key not in record]
    if missing:
        raise ValueError("source record missing: " + ", ".join(missing))
    if "url" in record:
        raise ValueError("source record uses retired 'url'; use 'locator'")
    if not isinstance(record["id"], str) or not record["id"].strip():
        raise ValueError("source record id is empty")
    if not isinstance(record["text"], str) or not record["text"].strip():
        raise ValueError(f"source {record['id']} text is empty")
    if not isinstance(record["locator"], str) or not LOCATOR.match(record["locator"]):
        raise ValueError(f"source {record['id']} needs a locator URI")
    provenance = record["provenance"]
    if (not isinstance(provenance, dict)
            or not isinstance(provenance.get("provider"), str)
            or not provenance["provider"].strip()):
        raise ValueError(f"source {record['id']} needs provenance.provider")
    if (record["completeness"] not in SOURCE_COMPLETENESS
            and not record.get("completeness_explanation")):
        raise ValueError(
            f"source {record['id']} has nonstandard completeness without explanation")
    if (record["withdrawal_state"] not in WITHDRAWAL_STATES
            and not record.get("withdrawal_explanation")):
        raise ValueError(
            f"source {record['id']} has nonstandard withdrawal_state without explanation")
    return record


def provider_result(provider, capability, status, *, scope, freshness,
                    records=None, errors=None, status_explanation=None, **fields):
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("provider must be non-empty text")
    if not isinstance(capability, str) or not capability.strip():
        raise ValueError("provider capability must be non-empty text")
    if (not isinstance(status, str) or not status.strip()
            or (status not in PROVIDER_STATUSES and not status_explanation)):
        raise ValueError(
            "provider status must be standard or carry status_explanation")
    if not isinstance(scope, dict):
        raise ValueError("provider scope must be an object")
    if not isinstance(freshness, dict):
        raise ValueError("provider freshness must be an object")
    result = {
        "provider": provider,
        "capability": capability,
        "status": status,
        "scope": scope,
        "freshness": freshness,
        "records": list(records or []),
        "errors": list(errors or []),
    }
    if status_explanation:
        result["status_explanation"] = status_explanation
    result.update(fields)
    return result


def validate_provider_result(result):
    if not isinstance(result, dict):
        raise ValueError("provider result is not an object")
    for key in ("provider", "capability", "status", "scope", "freshness",
                "records", "errors"):
        if key not in result:
            raise ValueError(f"provider result missing: {key}")
    if (not isinstance(result["provider"], str)
            or not result["provider"].strip()
            or not isinstance(result["capability"], str)
            or not result["capability"].strip()):
        raise ValueError("provider and capability must be non-empty text")
    if (not isinstance(result["status"], str)
            or not result["status"].strip()):
        raise ValueError("provider status must be non-empty text")
    if (result["status"] not in PROVIDER_STATUSES
            and not result.get("status_explanation")):
        raise ValueError("nonstandard provider status needs status_explanation")
    if not isinstance(result["scope"], dict) or not isinstance(result["freshness"], dict):
        raise ValueError("provider scope and freshness must be objects")
    if not isinstance(result["records"], list) or not isinstance(result["errors"], list):
        raise ValueError("provider records and errors must be lists")
    return result


def status_for(records, errors, incomplete=False):
    if errors or incomplete:
        return "partial" if records else "unavailable"
    return "complete"
