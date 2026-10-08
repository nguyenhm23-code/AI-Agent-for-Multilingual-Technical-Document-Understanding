"""Streamlit interaction demos for the production auto UI, with scripted answers."""
import json
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from denso_a3.contracts import RagConfig
from denso_a3.ui import app


def element(elements, label):
    return next(item for item in elements if item.label == label)


@pytest.fixture
def ui(tmp_path, monkeypatch):
    index = tmp_path / "index"
    index.mkdir()
    (index / "assets").mkdir()
    Image.new("RGB", (16, 16), "white").save(index / "assets/source.png")
    documents = [{"document_id": "A", "source_relative": "orion.pdf"},
        {"document_id": "B", "source_relative": "vega.pdf"}]
    for name, value in {"manifest.json": {"state": "ready", "schema_version": "1.1", "hierarchy_version": "1.0"},
        "documents.json": documents, "chunks.json": [],
        "registry.json": {"documents": {doc["document_id"]: {**doc, "fields": {}} for doc in documents}}}.items():
        (index / name).write_text(json.dumps(value), encoding="utf-8")
    state = tmp_path / "workspace.json"
    state.write_text(json.dumps({"index": str(index)}), encoding="utf-8")
    monkeypatch.setattr(app, "STATE", state)
    monkeypatch.setattr(app, "LOCAL", tmp_path)
    monkeypatch.setattr(app, "MEMORY", tmp_path / "conversations.sqlite")
    configured = RagConfig(orchestration_mode="baseline", retrieval_strategy="lexical", visual_policy="off")
    monkeypatch.setattr(app, "load_config", lambda _: configured)
    original_read = app.read_json
    def read(path):
        assert Path(path).name != "bat.assumptions.json", "UI must not read legacy BAT assumptions"
        return original_read(path)
    monkeypatch.setattr(app, "read_json", read)
    calls = []
    outputs = []
    def ask(**kwargs):
        calls.append(kwargs)
        if outputs:
            return outputs.pop(0)
        return {"question": kwargs["question"], "answer": "Orion voltage is 24 V [E1]", "status": "answered",
            "citations": [{"evidence_id": "E1", "chunk_id": "a", "source_relative": "orion.pdf",
                "page_number": 1, "image_asset": "assets/source.png"}], "evidence": [],
            "trace": {"routing": {"route": "baseline", "reason": "Direct lookup"}, "budget": {"retrieval_calls": 1}},
            "timings_ms": {"total": 12}}
    monkeypatch.setattr(app, "ask", ask)
    at = AppTest.from_string("from denso_a3.ui.app import main\nmain()", default_timeout=10).run()
    assert not at.exception
    return at, calls, outputs, index, configured


def test_ui_has_scope_memory_and_citations_but_no_technical_selectors(ui):
    at, calls, _, _, configured = ui
    labels = [item.label for items in (at.selectbox, at.radio, at.text_area) for item in items]
    assert "Phạm vi" in labels and "Hội thoại" in labels
    assert not any("Luồng xử lý" in label or "Kiến trúc" in label or "Giả định" in label for label in labels)
    element(at.text_area, "Câu hỏi (English / Tiếng Việt)").set_value("Orion voltage?").run()
    element(at.button, "Trả lời").click().run()
    assert not at.exception
    actual = calls[0]["config"]
    assert (actual.orchestration_mode, actual.retrieval_strategy, actual.visual_policy, actual.local_only) == ("auto", "hybrid", "auto", True)
    assert configured.orchestration_mode == "baseline" and configured.retrieval_strategy == "lexical"
    assert any("[E1] orion.pdf" in item.label for item in at.expander)


def test_ui_auto_clarification_pending_survives_without_mode_selection(ui):
    at, calls, outputs, _, _ = ui
    pending = {"schema_version": "2.0", "original_question": "Orion cost?", "scope_fingerprint": "fixture"}
    outputs.append({"answer": "Which budget period?", "status": "clarification", "citations": [],
        "trace": {"pending": pending}, "timings_ms": {}})
    element(at.text_area, "Câu hỏi (English / Tiếng Việt)").set_value("Orion cost?").run()
    element(at.button, "Trả lời").click().run()
    element(at.text_area, "Câu hỏi (English / Tiếng Việt)").set_value("FY2026").run()
    element(at.button, "Trả lời").click().run()
    assert not at.exception
    assert calls[1]["pending"] == pending
    assert calls[1]["config"].orchestration_mode == "auto"


def test_ui_corpus_change_invalidates_result_and_pending(ui):
    at, calls, outputs, index, _ = ui
    outputs.append({"answer": "Which period?", "status": "clarification", "citations": [],
        "trace": {"pending": {"schema_version": "2.0"}}, "timings_ms": {}})
    element(at.text_area, "Câu hỏi (English / Tiếng Việt)").set_value("Orion cost?").run()
    element(at.button, "Trả lời").click().run()
    assert any(item.value == "Which period?" for item in at.warning)
    (index / "chunks.json").write_text('[{"changed": true}]', encoding="utf-8")
    at.run()
    assert not any(item.value == "Which period?" for item in at.warning)
    element(at.text_area, "Câu hỏi (English / Tiếng Việt)").set_value("FY2026").run()
    element(at.button, "Trả lời").click().run()
    assert calls[1]["pending"] is None


def test_ui_scope_intersects_filters_and_invalidates_pending(ui):
    at, calls, outputs, _, _ = ui
    outputs.append({"answer": "Choose period", "status": "clarification", "citations": [],
        "trace": {"pending": {"schema_version": "2.0"}}, "timings_ms": {}})
    element(at.text_area, "Câu hỏi (English / Tiếng Việt)").set_value("Orion cost?").run()
    element(at.button, "Trả lời").click().run()
    element(at.radio, "Phạm vi").set_value("Chọn tài liệu").run()
    element(at.multiselect, "Tài liệu").set_value(["orion.pdf", "vega.pdf"])
    element(at.text_area, "Hard filters JSON (chỉ metadata đã xác minh)").set_value('{"source_relative":"orion.pdf","page_number":1}').run()
    element(at.button, "Trả lời").click().run()
    assert not at.exception
    assert calls[-1]["filters"] == {"source_relative": ["orion.pdf"], "page_number": 1}
    assert calls[-1]["pending"] is None


def test_ui_long_term_conversation_passes_memory_keywords(ui):
    at, calls, _, _, _ = ui
    element(at.radio, "Hội thoại").set_value("Dài hạn — lưu và mở lại").run()
    element(at.button, "Tạo hội thoại").click().run()
    element(at.text_area, "Câu hỏi (English / Tiếng Việt)").set_value("Orion voltage?").run()
    element(at.button, "Trả lời").click().run()
    assert not at.exception
    assert calls[0]["conversation_id"]
    assert calls[0]["memory_path"] == app.MEMORY
    assert calls[0]["pending"] is None


def test_ui_filters_reject_empty_intersection():
    with pytest.raises(ValueError, match="phần giao"):
        app.scope_filters('{"source_relative":"orion.pdf"}', ["vega.pdf"])


def test_ui_suggestions_require_explicit_field_selection(ui, monkeypatch):
    from denso_a3.bt4.metadata import create_registry, verified_values
    from denso_a3.bt4.metadata_enrichment import suggest_metadata
    at, _, _, index, _ = ui
    docs = [{"document_id": "A", "source_relative": "orion.pdf", "source_sha256": "sha"},
            {"document_id": "B", "source_relative": "vega.pdf", "source_sha256": "sha2"}]
    chunks = [{"chunk_id": "a", "document_id": "A", "source_sha256": "sha", "kind": "text",
               "text": "Revision: B\nEquipment ID: M1", "page_id": "p", "page_number": 1}]
    registry = create_registry(docs)
    suggestions = suggest_metadata(docs, chunks, registry, RagConfig(metadata_use_llm=False))
    for name, data in [("documents", docs), ("chunks", chunks), ("registry", registry), ("metadata_suggestions", suggestions)]:
        (index / (name + ".json")).write_text(json.dumps(data), encoding="utf-8")
    saved = []
    monkeypatch.setattr(app, "update_metadata", lambda source, value, target: saved.append(value))
    at.run()
    assert not at.exception
    assert not element(at.checkbox, "Xác nhận revision_id").value
    assert not element(at.checkbox, "Xác nhận equipment_id").value
    element(at.button, "Xác nhận các gợi ý đã chọn").click().run()
    assert not saved
    element(at.checkbox, "Xác nhận revision_id").check().run()
    element(at.button, "Xác nhận các gợi ý đã chọn").click().run()
    assert not at.exception and len(saved) == 1
    assert verified_values(saved[0]["documents"]["A"]["fields"]) == {"revision_id": "B"}
    with pytest.raises(ValueError, match="object"):
        app.scope_filters("[]", None)

