import json
from pathlib import Path
import numpy as np
import pytest
from denso_a3.bt4.metadata import create_registry, validate_registry
from denso_a3.bt5.store import RetrievalStore
from denso_a3.bt6.conflicts import detect_conflicts
from denso_a3.bt6.planner import plan_query
from denso_a3.bt7.answering import citations_for
from denso_a3.bt7.memory import ConversationStore
from denso_a3.contracts import RetrievedEvidence


def cell(value, status="user_verified"):
    return {"value": value, "review_status": status, "origin": "synthetic"}


def chunk(cid, source, text="Pressure: 5 MPa", family="F", entity="M1"):
    return {"chunk_id": cid, "content_key": cid, "document_id": source, "source_relative": source,
            "page_id": source, "page_number": None, "image_sha256": "sha", "kind": "prose", "text": text,
            "metadata": {"document_family_id": cell(family), "equipment_id": cell(entity)}}


def test_same_prefilter_lexical_and_dense(tmp_path):
    rows = [chunk("a", "allowed"), chunk("b", "excluded")]
    rows[1]["metadata"]["revision_id"] = cell("R2", "unreviewed")
    with RetrievalStore(tmp_path, create=True, dimension=2) as store:
        store.add(rows, np.array([[0., 1.], [1., 0.]], dtype=np.float32))
        allowed = store.allowed({"source_relative": "allowed"})
        assert [x[0] for x in store.lexical("Pressure", allowed, 5)] == ["a"]
        assert [x[0] for x in store.dense(np.array([1., 0.]), allowed, 5)] == ["a"]
        assert store.allowed({"revision_id": "R2"}) == []
        assert store.allowed({"source_relative": []}) == []


@pytest.mark.parametrize("other,expected", [
    ("Pressure: 6 MPa", True), ("Pressure: 5000 kPa", False), ("Voltage: 6 V", False)])
def test_synthetic_numeric_conflict_units(other, expected):
    assert bool(detect_conflicts([chunk("a", "v1"), chunk("b", "v2", other)])) == expected


def test_unrelated_entity_no_conflict():
    assert not detect_conflicts([chunk("a", "v1"), chunk("b", "v2", "Pressure: 6 MPa", entity="M2")])


def test_structured_table_conflict():
    a, b = chunk("a", "v1", "TABLE"), chunk("b", "v2", "TABLE")
    for c, number in [(a, "5"), (b, "6")]:
        c["table"] = {"cells": [{"row_index": 1, "column_index": i, "text_raw": value}
                                for i, value in enumerate(["Pressure", number, "MPa"])]}
    assert len(detect_conflicts([a, b])) == 1


def test_invalid_citation_and_physical_image(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/sha.png").write_bytes(b"fixture")
    evidence = [RetrievedEvidence(chunk("a", "image.png"), 1, evidence_id="E1")]
    with pytest.raises(ValueError):
        citations_for(["E99"], evidence, tmp_path)
    assert citations_for(["E1"], evidence, tmp_path)[0]["page_number"] is None


def test_metadata_hash_invalidation():
    documents = [{"document_id": "d", "source_relative": "a.pdf", "source_sha256": "old"}]
    registry = create_registry(documents)
    documents[0]["source_sha256"] = "new"
    with pytest.raises(ValueError):
        validate_registry(registry, documents)


def test_conversation_persistence_and_scope(tmp_path):
    path = tmp_path / "memory.sqlite"
    m = ConversationStore(path)
    one, two = m.create("one"), m.create("two")
    m.append(one, "scope1", "Q", {"answer": "A"})
    m.close()
    m = ConversationStore(path)
    try:
        assert m.history(one, "scope1")[0]["question"] == "Q"
        assert m.history(one, "scope2") == []
        assert m.history(two) == []
    finally:
        m.close()


def test_unknown_revision_clarifies():
    plan = plan_query("Thông số phiên bản R99 là gì?", {"documents": {}})
    assert plan.clarification


def test_current_year_is_not_a_version_request():
    plan = plan_query("What is the percentage in the current year?", {"documents": {}})
    assert "current" not in plan.intents
    assert "current" in plan_query("Which is the current revision?", {"documents": {}}).intents


def test_instant_never_opens_memory(tmp_path, monkeypatch):
    from denso_a3.pipeline import rag_runner
    from denso_a3.contracts import RagConfig
    (tmp_path / "manifest.json").write_text(json.dumps({"state": "ready", "schema_version": "1.0", "model_manifest": {}}))
    (tmp_path / "registry.json").write_text(json.dumps({"documents": {}}))
    def forbidden(*args, **kwargs):
        raise AssertionError("Instant mode accessed persistent memory")
    monkeypatch.setattr(rag_runner, "ConversationStore", forbidden)
    result = rag_runner.ask(tmp_path, "revision R99?", RagConfig(), memory_path=tmp_path / "never.sqlite")
    assert result["status"] == "clarification"
    assert not (tmp_path / "never.sqlite").exists()


def test_conflict_pipeline_abstains_without_generation(tmp_path):
    from denso_a3.pipeline.rag_runner import ask
    from denso_a3.contracts import RagConfig
    rows = [chunk("a", "r1"), chunk("b", "r2", "Pressure: 6 MPa")]
    with RetrievalStore(tmp_path, create=True, dimension=2) as store:
        store.add(rows, np.array([[1., 0.], [1., 0.]], dtype=np.float32))
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/sha.png").write_bytes(b"fixture")
    (tmp_path / "manifest.json").write_text(json.dumps({"state": "ready", "schema_version": "1.0", "model_manifest": {}}))
    (tmp_path / "registry.json").write_text(json.dumps({"documents": {c["document_id"]: {"fields": c["metadata"]} for c in rows}}))
    result = ask(tmp_path, "Pressure?", RagConfig(rerank=False), strategy="lexical")
    assert result["status"] == "conflict"
    assert len(result["citations"]) == 2


def test_metadata_snapshot_rebuilds_both_filter_payloads(tmp_path):
    from denso_a3.pipeline.index_builder import update_metadata
    source, target = tmp_path / "old", tmp_path / "new"
    source.mkdir()
    row = chunk("a", "doc")
    doc = {"document_id": "doc", "source_relative": "doc", "source_sha256": "sha"}
    registry = create_registry([doc])
    registry["documents"]["doc"]["fields"]["revision_id"] = cell("R2")
    with RetrievalStore(source, create=True, dimension=2) as store:
        store.add([row], np.array([[1., 0.]], dtype=np.float32))
    (source / "documents.json").write_text(json.dumps([doc]))
    (source / "manifest.json").write_text(json.dumps({"state": "ready"}))
    update_metadata(source, registry, target)
    with RetrievalStore(target) as store:
        allowed = store.allowed({"revision_id": "R2"})
        assert allowed == ["a"]
        assert store.dense(np.array([1., 0.]), allowed, 1)[0][0] == "a"
    with RetrievalStore(source) as store:
        assert store.allowed({"revision_id": "R2"}) == []


def test_invalid_model_citation_is_regenerated_not_rebound(tmp_path):
    from denso_a3.bt7.answering import grounded_answer
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/sha.png").write_bytes(b"fixture")
    class Stub:
        calls = 0
        def generate_json(self, system, prompt, images, schema):
            self.calls += 1
            assert schema["properties"]["citations"]["items"]["enum"] == ["E1"]
            return {"answer": "5 MPa", "status": "answered", "citations": ["E99" if self.calls == 1 else "E1"]}, {"cost_usd": 0, "total_tokens": 10}
    generator = Stub()
    result, citations, usage = grounded_answer("Pressure?", "en", [RetrievedEvidence(chunk("a", "d"), 1, evidence_id="E1")], tmp_path, generator)
    assert generator.calls == 2 and citations[0]["evidence_id"] == "E1"
    assert usage["total_tokens"] == 20
