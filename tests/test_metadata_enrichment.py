"""Source provenance, suggestion-only trust policy and immutable snapshot tests."""
from copy import deepcopy
from dataclasses import replace
import json
import numpy as np
import pytest
from denso_a3.bt4.metadata import create_registry, verified_values
from denso_a3.bt4.metadata_enrichment import suggest_metadata, confirm_suggestions
from denso_a3.bt5.store import RetrievalStore
from denso_a3.contracts import RagConfig
from denso_a3.pipeline.index_builder import suggest_index_metadata, update_metadata


def fixture():
    docs = [{"document_id": "d", "source_relative": "manual.pdf", "source_sha256": "sha"}]
    text = "Document number: SOP-001\nRevision: B\nEffective from: 2026-10-04\nEquipment ID: M1\nPart number: P-42\nIssued by: DENSO\nDocument type: Manual\nLanguage: English"
    chunks = [{"chunk_id": "a", "content_key": "a", "kind": "text", "document_id": "d", "source_sha256": "sha",
               "source_relative": "manual.pdf", "page_id": "p1", "page_number": 1, "image_sha256": "image",
               "text": text, "metadata": {}, "bbox": [0, 0, 1, 1]}]
    return docs, chunks, create_registry(docs)


def drafts(docs, chunks, registry):
    return suggest_metadata(docs, chunks, registry, RagConfig(metadata_use_llm=False))


def test_extraction_never_changes_registry_or_filters():
    docs, chunks, registry = fixture()
    before = deepcopy(registry)
    output = drafts(docs, chunks, registry)
    assert registry == before and verified_values(registry["documents"]["d"]["fields"]) == {}
    fields = output["documents"]["d"]["fields"]
    assert fields["revision_id"][0]["value"] == "B"
    assert fields["document_family_id"][0]["value"] == "SOP-001"
    assert all(c["review_status"] == "unreviewed" for items in fields.values() for c in items)


def test_only_explicitly_selected_fields_are_confirmed():
    docs, chunks, registry = fixture()
    output = drafts(docs, chunks, registry)
    updated = confirm_suggestions(registry, output, docs, chunks, "d", {"revision_id": 0})
    trusted = verified_values(updated["documents"]["d"]["fields"])
    assert trusted == {"revision_id": "B"}
    assert registry["documents"]["d"]["fields"]["revision_id"]["value"] is None
    assert updated["documents"]["d"]["fields"]["revision_id"]["origin"] == "user_confirmed_auto_suggestion"
    with pytest.raises(ValueError, match="outside"):
        confirm_suggestions(registry, output, docs, chunks, "d", {"revision_id": 999})


@pytest.mark.parametrize("change", ["hash", "text", "registry", "quote"])
def test_stale_or_forged_proof_cannot_be_confirmed(change):
    docs, chunks, registry = fixture()
    output = drafts(docs, chunks, registry)
    if change == "hash": docs[0]["source_sha256"] = "changed"
    if change == "text": chunks[0]["text"] += " changed"
    if change == "registry": registry["documents"]["d"]["fields"]["revision_id"]["value"] = "A"
    if change == "quote": output["documents"]["d"]["fields"]["revision_id"][0]["evidence"]["quote"] = "Revision: C"
    with pytest.raises(ValueError): confirm_suggestions(registry, output, docs, chunks, "d", {"revision_id": 0})


def test_multiple_equipment_candidates_stay_separate_and_ambiguous_dates_are_not_guessed():
    docs, chunks, registry = fixture()
    chunks[0]["text"] += "\nEquipment ID: M2\nEffective to: 04/10/2027"
    output = drafts(docs, chunks, registry)["documents"]["d"]
    assert [s["value"] for s in output["fields"]["equipment_id"]] == ["M1", "M2"]
    assert "effective_to" not in output["fields"]
    assert any("ambiguous dates" in n for n in output["notes"])


def test_verified_user_value_is_preserved_until_explicit_replacement():
    docs, chunks, registry = fixture()
    registry["documents"]["d"]["fields"]["revision_id"] = {"value": "A", "origin": "user_form", "review_status": "user_verified"}
    output = drafts(docs, chunks, registry)
    assert verified_values(registry["documents"]["d"]["fields"])["revision_id"] == "A"
    assert any("preserved" in w for w in output["documents"]["d"]["fields"]["revision_id"][0]["warnings"])


def test_caption_and_changed_hash_are_not_metadata_sources():
    docs, chunks, registry = fixture()
    chunks[0]["kind"] = "page_visual"
    assert not drafts(docs, chunks, registry)["documents"]["d"]["fields"]
    chunks[0]["kind"], chunks[0]["source_sha256"] = "text", "wrong"
    assert not drafts(docs, chunks, registry)["documents"]["d"]["fields"]


def test_model_cannot_invent_values_sources_or_review_status():
    docs, chunks, registry = fixture()
    class Model:
        def count_tokens(self, *a): return 100
        def generate_json(self, *a, **kw):
            return {"candidates": [
                {"field": "revision_id", "value": "C", "chunk_id": "a", "quote": "Revision: B", "inferred": False},
                {"field": "effective_from", "value": "2027-01-01", "chunk_id": "foreign", "quote": "x", "inferred": False},
                {"field": "document_type", "value": "Procedure", "chunk_id": "a", "quote": "Document type: Manual",
                 "inferred": True, "review_status": "source_verified"}]}, {}
    output = suggest_metadata(docs, chunks, registry, RagConfig(), Model())["documents"]["d"]
    assert [s["value"] for s in output["fields"]["revision_id"]] == ["B"]
    assert all(s["review_status"] == "unreviewed" for items in output["fields"].values() for s in items)
    assert len(output["notes"]) >= 2


def test_model_failure_retains_rules_and_records_limitation():
    docs, chunks, registry = fixture()
    class Model:
        def count_tokens(self, *a): raise RuntimeError("Unavailable")
    output = suggest_metadata(docs, chunks, registry, RagConfig(), Model())["documents"]["d"]
    assert output["fields"]["revision_id"] and any("unavailable" in n for n in output["notes"])
    assert output["model_status"] == "error"


def test_unchanged_drafts_are_reused_but_changed_text_is_reextracted():
    docs, chunks, registry = fixture()
    config = RagConfig(metadata_use_llm=False)
    prior = suggest_metadata(docs, chunks, registry, config)
    output = suggest_metadata(docs, chunks, registry, config, previous=prior)
    assert output["documents"]["d"]["reused"]
    chunks[0]["text"] = chunks[0]["text"].replace("Revision: B", "Revision: C")
    changed = suggest_metadata(docs, chunks, registry, config, previous=prior)
    assert not changed["documents"]["d"]["reused"]
    assert changed["documents"]["d"]["fields"]["revision_id"][0]["value"] == "C"


def test_backfill_preserves_vectors_registry_and_source_snapshot(tmp_path):
    docs, chunks, registry = fixture()
    source, target, confirmed = tmp_path / "old", tmp_path / "draft", tmp_path / "confirmed"
    source.mkdir()
    for name, data in [("documents", docs), ("chunks", chunks), ("registry", registry),
                       ("manifest", {"state": "ready", "schema_version": "1.0"})]:
        (source / (name + ".json")).write_text(json.dumps(data), encoding="utf-8")
    with RetrievalStore(source, create=True, dimension=2) as store:
        store.add(chunks, np.array([[1., 0.]], dtype=np.float32))
    suggest_index_metadata(source, target, RagConfig(metadata_use_llm=False))
    output = json.loads((target / "metadata_suggestions.json").read_text())
    assert json.loads((target / "registry.json").read_text()) == registry
    assert not (source / "metadata_suggestions.json").exists()
    with RetrievalStore(target) as store:
        assert store.allowed({"revision_id": "B"}) == []
        assert store.dense(np.array([1., 0.]), store.allowed({}), 1)[0][0] == "a"
    updated = confirm_suggestions(registry, output, docs, chunks, "d", {"revision_id": 0})
    update_metadata(target, updated, confirmed)
    with RetrievalStore(confirmed) as store:
        assert store.allowed({"revision_id": "B"}) == ["a"]
    remaining = json.loads((confirmed / "metadata_suggestions.json").read_text())
    assert "revision_id" not in remaining["documents"]["d"]["fields"]
    # Confirmation of a second field does not require re-running extraction.
    confirm_suggestions(updated, remaining, docs, chunks, "d", {"equipment_id": 0})
    with pytest.raises(ValueError): suggest_index_metadata(source, target, RagConfig(metadata_use_llm=False))


def test_build_writes_suggestions_without_applying_metadata(tmp_path, monkeypatch):
    from denso_a3.pipeline import index_builder
    from transformers import AutoTokenizer
    docs, chunks, registry = fixture()
    docs[0]["source_path"] = str(tmp_path / "missing.pdf")
    config = RagConfig(metadata_use_llm=False, caption_images=False, build_hierarchy=False)
    monkeypatch.setattr(index_builder, "models", lambda c: {"models": {"embedding": {"path": "fixture", "repo": "fixture"}}})
    monkeypatch.setattr(index_builder, "merged_run", lambda paths: ({"documents": docs, "pages": [], "fingerprint": "fixture"}, {}))
    monkeypatch.setattr(index_builder, "build_chunks", lambda *a: (deepcopy(chunks), {}))
    class Tokenizer:
        def encode(self, *a, **kw): return [1]
    monkeypatch.setattr(AutoTokenizer, "from_pretrained", lambda *a, **kw: Tokenizer())
    class Encoder:
        dimension = 2
        def __init__(self, *a): pass
        def encode(self, texts, **kw): return np.tile(np.array([[1., 0.]], dtype=np.float32), (len(texts), 1))
        def close(self): pass
    monkeypatch.setattr(index_builder, "TextEncoder", Encoder)
    target = tmp_path / "built"
    manifest = index_builder.build_index(["scripted_extraction"], target, config)
    assert manifest["metadata_suggestions_policy"] == "user_confirmation_required"
    assert (target / "metadata_suggestions.json").is_file()
    assert json.loads((target / "registry.json").read_text()) == registry
    with RetrievalStore(target) as store:
        assert store.allowed({"revision_id": "B"}) == []
