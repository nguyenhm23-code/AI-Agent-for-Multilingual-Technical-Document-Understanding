"""Explicit provenance for metadata; unknown fields never become authority."""

from copy import deepcopy
from datetime import date

FIELDS = ("document_family_id", "revision_id", "effective_from", "effective_to",
          "document_type", "issuing_authority", "document_language", "equipment_id", "part_number")
TRUSTED = {"user_verified", "source_verified"}


def create_registry(documents: list[dict]) -> dict:
    return {"schema_version": "1.0", "documents": {
        d["document_id"]: {"source_relative": d["source_relative"],
                           "source_sha256": d.get("source_sha256"),
                           "fields": {key: {"value": None, "origin": "unknown",
                                            "review_status": "unreviewed", "evidence": None}
                                      for key in FIELDS}}
        for d in documents}}


def validate_registry(registry: dict, documents: list[dict]) -> dict:
    result = deepcopy(registry)
    if result.get("schema_version") != "1.0" or not isinstance(result.get("documents"), dict):
        raise ValueError("Metadata registry must contain schema_version=1.0 and documents")
    known = {d["document_id"]: d for d in documents}
    if set(result["documents"]) - set(known):
        raise ValueError("Metadata references a document outside this index")
    for doc_id, record in result["documents"].items():
        doc = known[doc_id]
        if record.get("source_sha256") != doc.get("source_sha256"):
            raise ValueError(f"Metadata source hash changed: {doc_id}")
        if record.get("source_relative") != doc["source_relative"]:
            raise ValueError(f"Metadata source identity changed: {doc_id}")
        if set(record.get("fields", {})) - set(FIELDS):
            raise ValueError(f"Unsupported metadata field: {doc_id}")
        for key, cell in record.get("fields", {}).items():
            if not isinstance(cell, dict) or cell.get("review_status") not in TRUSTED | {"unreviewed"}:
                raise ValueError(f"Invalid metadata provenance: {doc_id}/{key}")
            value = cell.get("value")
            if value is not None and (not isinstance(value, str) or len(value) > 1000):
                raise ValueError(f"Metadata value must be a string or null: {key}")
            if value and key in {"effective_from", "effective_to"}:
                date.fromisoformat(value)
        fields = record.get("fields", {})
        start, end = (fields.get(k, {}).get("value") for k in ("effective_from", "effective_to"))
        if start and end and start > end:
            raise ValueError("effective_from must not exceed effective_to")
    return result


def verified_values(metadata: dict) -> dict:
    return {k: v["value"] for k, v in metadata.items()
            if isinstance(v, dict) and v.get("review_status") in TRUSTED and v.get("value")}
