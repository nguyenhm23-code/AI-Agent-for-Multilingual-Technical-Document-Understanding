"""Shared phase demos: real source pixels with scripted local-model readings."""
import base64
import hashlib
import io
import json
from dataclasses import asdict, replace

import pytest
from PIL import Image

from denso_a3.bt7.answering import grounded_answer
from denso_a3.bt7.fact_extraction import extract_fact, extract_visual_fact
from denso_a3.bt7.visual_evidence import run_visual_phase
from denso_a3.contracts import QueryPlan, QueryRequirement, RagConfig, RetrievedEvidence, QueryBudgetExceeded
from denso_a3.contracts import ContextBudgetExceeded
from denso_a3.pipeline.query_budget import QueryBudget


class ScriptedLocal:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def count_tokens(self, system, prompt):
        return 100

    def close(self):
        pass

    def generate_json(self, system, prompt, images=None, **kwargs):
        self.calls.append({"prompt": json.loads(prompt.split("\nPrevious")[0]), "images": images or [], "schema": kwargs.get("schema")})
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result, {"provider": "local", "total_tokens": 10, "cost_usd": 0}


def source(tmp_path, kind="page_visual", text="UNVERIFIED caption", bbox=None):
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    image = Image.new("RGB", (100, 50), "red")
    image.paste("blue", (50, 0, 100, 50))
    data = io.BytesIO()
    image.save(data, format="PNG")
    sha = hashlib.sha256(data.getvalue()).hexdigest()
    (assets / (sha + ".png")).write_bytes(data.getvalue())
    chunk = {"chunk_id": "c1", "document_id": "d1", "source_relative": "diagram.pdf", "page_id": "p1",
             "page_number": 1, "image_sha256": sha, "kind": kind, "text": text, "bbox": bbox, "metadata": {}}
    return RetrievedEvidence(chunk, 1, evidence_id="E1")


def observation(raw="5 MPa", state="observed", image_id="I1", requirement_id="R1"):
    return {"observations": [{"image_id": image_id, "requirement_id": requirement_id, "state": state,
        "observed_text": "M1 pressure " + raw if raw else "Unclear pressure label", "raw_value": raw,
        "unit_text": "MPa" if raw else "", "entity_label": "M1", "row_label": "pressure", "column_label": "rating"}]}


def requirements(rid="R1"):
    return [QueryRequirement(rid, "Pressure", entity="M1", attribute="pressure", wording="pressure in image")]


def test_text_only_does_not_load_vlm(tmp_path):
    hit = source(tmp_path, "prose", "M1 pressure 5 MPa")
    generator = ScriptedLocal([])
    result = run_visual_phase(tmp_path, QueryPlan("Pressure?", "Pressure?", "en"), [hit], RagConfig(), {}, generator,
                              QueryBudget(RagConfig()), requirements())
    assert result.trace["skipped"] and generator.calls == []


def test_partial_page_sends_only_allowed_pixels(tmp_path):
    hit = source(tmp_path, "figure", bbox=[0, 0, .5, 1])
    plan = QueryPlan("Read pressure image", "Read pressure image", "en", hard_filters={"kind": "figure"}, intents=["visual"])
    generator = ScriptedLocal([observation()])
    result = run_visual_phase(tmp_path, plan, [hit], RagConfig(), {}, generator, QueryBudget(RagConfig()), requirements())
    sent = Image.open(io.BytesIO(base64.b64decode(generator.calls[0]["images"][0].split(",")[1])))
    assert sent.size == (50, 50) and sent.getpixel((49, 25)) == (255, 0, 0)
    assert result.observations[0].provenance["source_bbox"] == [0, 0, .5, 1]


def test_partial_page_without_crop_is_missing(tmp_path):
    hit = source(tmp_path, "figure")
    plan = QueryPlan("Image?", "Image?", "en", hard_filters={"kind": "figure"}, intents=["visual"])
    generator = ScriptedLocal([])
    result = run_visual_phase(tmp_path, plan, [hit], RagConfig(), {}, generator, QueryBudget(RagConfig()), requirements())
    assert result.missing_requirement_ids == ["R1"] and not generator.calls
    assert result.trace["stop_reason"] == "no_allowed_source_pixels"


def test_source_hash_change_and_scope_are_rejected(tmp_path):
    hit = source(tmp_path)
    (tmp_path / "assets" / (hit.chunk["image_sha256"] + ".png")).write_bytes(b"changed")
    generator = ScriptedLocal([])
    plan = QueryPlan("Image?", "Image?", "en", intents=["visual"])
    result = run_visual_phase(tmp_path, plan, [hit], RagConfig(), {}, generator, QueryBudget(RagConfig()), requirements())
    assert not result.selected_images and "missing_or_changed_image:c1" in result.trace["validation_errors"]
    hit = source(tmp_path)
    result = run_visual_phase(tmp_path, replace(plan, hard_filters={"document_id": "excluded"}), [hit], RagConfig(), {}, generator,
                              QueryBudget(RagConfig()), requirements())
    assert not generator.calls and "outside_scope:c1" in result.trace["validation_errors"]


@pytest.mark.parametrize("state", ["unreadable", "ambiguous"])
def test_unreadable_never_becomes_quantity_fact(tmp_path, state):
    hit = source(tmp_path)
    task = {"task_id": "T1", "query": "M1 pressure in image", "entity": "M1", "kind": "quantity", "expected_unit": "Pa"}
    result = run_visual_phase(tmp_path, QueryPlan("Image?", "Image?", "en", intents=["visual"]), [hit], RagConfig(), {},
        ScriptedLocal([observation("", state, requirement_id="T1")]), QueryBudget(RagConfig()), requirements("T1"), task)
    fact, _, _, error = extract_visual_fact(task, result, [hit])
    assert fact is None and error == "no_readable_visual_fact"


def test_visual_fact_has_image_provenance_and_units_without_ocr_quote(tmp_path):
    hit = source(tmp_path)
    task = {"task_id": "T1", "query": "M1 pressure in image", "entity": "M1", "kind": "quantity", "expected_unit": "Pa"}
    generator = ScriptedLocal([observation(requirement_id="T1")])
    result = run_visual_phase(tmp_path, QueryPlan("Image?", "Image?", "en", intents=["visual"]), [hit], RagConfig(), {}, generator,
                              QueryBudget(RagConfig()), requirements("T1"), task)
    fact, _, _, error = extract_fact(task, [hit], generator, RagConfig(), visual_result=result)
    assert not error and fact["value"] == "5000000" and fact["dimensions"] == {"Pa": 1}
    assert fact["quote"] == "" and fact["support_kind"] == "image" and fact["origin"] == "source_visual"
    assert fact["visual_provenance"]["source_relative"] == "diagram.pdf"
    assert len(generator.calls) == 1


def test_visual_repair_counts_against_visual_budget(tmp_path):
    hit = source(tmp_path)
    config = RagConfig(query_max_visual_calls=1)
    budget = QueryBudget(config)
    generator = ScriptedLocal([observation(image_id="invented"), observation()])
    with pytest.raises(QueryBudgetExceeded, match="visual_calls_budget"):
        run_visual_phase(tmp_path, QueryPlan("Image?", "Image?", "en", intents=["visual"]), [hit], config, {}, generator,
                         budget, requirements())
    assert budget.visual_calls == 1 and len(generator.calls) == 1


def test_visual_cache_has_task_scope_model_and_request_boundaries(tmp_path):
    hit = source(tmp_path)
    generator = ScriptedLocal([observation(), observation(), observation(), observation(), observation()])
    plan = QueryPlan("Image?", "Image?", "en", intents=["visual"])
    config = RagConfig()
    budget = QueryBudget(config)
    one = run_visual_phase(tmp_path, plan, [hit], config, {}, generator, budget, requirements())
    cached = run_visual_phase(tmp_path, plan, [hit], config, {}, generator, budget, requirements())
    assert cached.trace["cache_reused"] and cached.usage == {} and len(generator.calls) == 1
    one.observations[0].raw_value = "modified by caller"
    assert run_visual_phase(tmp_path, plan, [hit], config, {}, generator, budget, requirements()).observations[0].raw_value == "5 MPa"
    run_visual_phase(tmp_path, replace(plan, hard_filters={"document_id": "d1"}), [hit], config, {}, generator, budget, requirements())
    run_visual_phase(tmp_path, plan, [hit], config, {"models": {"generation": {"model": "different"}}}, generator, budget, requirements())
    run_visual_phase(tmp_path, plan, [hit], config, {}, generator, QueryBudget(config), requirements())
    run_visual_phase(tmp_path, plan, [hit], config, {}, generator, budget, requirements(),
        task={"task_id": "T1", "query": "M1 pressure", "entity": "M1", "kind": "quantity", "expected_unit": "Pa"})
    assert len(generator.calls) == 5


def test_visual_ocr_disagreement_is_explicit_conflict(tmp_path):
    hit = source(tmp_path, "figure", "M1 pressure 6 MPa")
    result = run_visual_phase(tmp_path, QueryPlan("Image?", "Image?", "en", intents=["visual"]), [hit], RagConfig(), {},
        ScriptedLocal([observation()]), QueryBudget(RagConfig()), requirements())
    assert result.conflicts[0]["values"] == ["6 MPa", "5 MPa"]


def answer_result(ref="E1", quote="M1 pressure 5 MPa", second_state=None):
    result = {"status": "answered", "claims": [{"text": "Pressure is 5 MPa", "support_refs": [{"source_id": ref, "quote": quote}]}],
        "coverage": [{"requirement_id": "R1", "state": "supported", "support_refs": [{"source_id": ref, "quote": quote}]}]}
    if second_state:
        result["coverage"].append({"requirement_id": "R2", "state": second_state, "support_refs": []})
    return result


def test_answer_citation_is_not_enough_when_requirement_missing(tmp_path):
    hit = source(tmp_path, "prose", "M1 pressure 5 MPa")
    result, citations, _ = grounded_answer("Pressure and voltage?", "en", [hit], tmp_path,
        ScriptedLocal([answer_result(second_state="missing")]), requirements=requirements() + [QueryRequirement("R2", "Voltage")])
    assert result["status"] == "abstain" and result["coverage"][1]["state"] == "missing"
    assert citations[0]["evidence_id"] == "E1" and "Voltage" in result["answer"]


def test_answer_visual_support_has_source_crop_and_no_final_images(tmp_path):
    hit = source(tmp_path)
    visual_gen = ScriptedLocal([observation()])
    visual = run_visual_phase(tmp_path, QueryPlan("Image?", "Image?", "en", intents=["visual"]), [hit], RagConfig(), {}, visual_gen,
                              QueryBudget(RagConfig()), requirements())
    final = ScriptedLocal([answer_result("V1")])
    result, citations, _ = grounded_answer("Pressure?", "en", [hit], tmp_path, final, requirements=requirements(), visual_result=visual)
    assert final.calls[0]["images"] == [] and result["answer"] == "Pressure is 5 MPa [V1]"
    assert citations[0]["support_kind"] == "image" and citations[0]["citation_precision"] == "page"


def test_invalid_quote_repairs_once_and_does_not_rebind_source(tmp_path):
    hit = source(tmp_path, "prose", "M1 pressure 5 MPa")
    generator = ScriptedLocal([answer_result(quote="invented"), answer_result()])
    result, _, usage = grounded_answer("Pressure?", "en", [hit], tmp_path, generator, requirements=requirements())
    assert result["status"] == "answered" and len(generator.calls) == 2 and usage["total_tokens"] == 20


def test_sourced_ratio_requires_explicit_task_semantics():
    from denso_a3.bt7.fact_extraction import validate_fact
    task = {"task_id": "T1", "query": "M1 sales transfer ratio", "entity": "M1", "kind": "quantity", "expected_unit": "ratio"}
    data = {"found": True, "chunk_id": "c1", "quote": "M1 sales transfer ratio 80%", "raw_value": "80%"}
    records = {"c1": {"kind": "prose", "text": data["quote"]}}
    fact = validate_fact(task, data, records)
    assert fact["value"] == "0.8" and fact["raw_value"] == "80%" and fact["dimensions"] == {}
    assert fact["conversion_trace"][0]["divisor"] == "100"
    with pytest.raises(ValueError, match="requested unit"):
        validate_fact({**task, "expected_unit": "number"}, data, records)


def test_visual_call_has_at_most_two_real_images(tmp_path):
    hit = source(tmp_path)
    evidence = [replace(hit, chunk={**hit.chunk, "chunk_id": f"c{i}", "page_id": f"p{i}"}) for i in range(3)]
    generator = ScriptedLocal([observation()])
    result = run_visual_phase(tmp_path, QueryPlan("Images?", "Images?", "en", intents=["visual"]), evidence,
        RagConfig(), {}, generator, QueryBudget(RagConfig()), requirements())
    assert len(result.selected_images) == len(generator.calls[0]["images"]) == 2


def test_malformed_visual_json_repairs_once(tmp_path):
    hit = source(tmp_path)
    generator = ScriptedLocal([ValueError("Model did not return a JSON object"), observation()])
    budget = QueryBudget(RagConfig())
    result = run_visual_phase(tmp_path, QueryPlan("Image?", "Image?", "en", intents=["visual"]), [hit],
        RagConfig(), {}, generator, budget, requirements())
    assert budget.visual_calls == 2 and len(generator.calls) == 2 and result.observations[0].state == "observed"


def test_wrong_visual_unit_repaired_before_fact_is_usable(tmp_path):
    hit = source(tmp_path)
    bad = observation(requirement_id="T1")
    bad["observations"][0].update(raw_value="5 V", observed_text="M1 pressure 5 V", unit_text="V")
    generator = ScriptedLocal([bad, observation(requirement_id="T1")])
    task = {"task_id": "T1", "query": "M1 pressure", "entity": "M1", "kind": "quantity", "expected_unit": "Pa"}
    result = run_visual_phase(tmp_path, QueryPlan("Image?", "Image?", "en", intents=["visual"]), [hit],
        RagConfig(), {}, generator, QueryBudget(RagConfig()), requirements("T1"), task)
    fact, _, _, _ = extract_visual_fact(task, result, [hit])
    assert len(generator.calls) == 2 and fact["dimensions"] == {"Pa": 1}
    assert "requested unit/denominator" in result.trace["validation_errors"][0]


def test_research_mixed_image_text_fact_calculator_demo(tmp_path, monkeypatch):
    import numpy as np
    from denso_a3.bt4.hierarchy import build_hierarchy
    from denso_a3.bt5.store import RetrievalStore
    from denso_a3.bt5.hierarchy_store import HierarchyStore
    from denso_a3.pipeline.research_runner import run_research
    hit = source(tmp_path)
    image_chunk = {**hit.chunk, "content_key": "c1", "parent_id": "p1", "source_sha256": "source"}
    text_chunk = {**image_chunk, "chunk_id": "c2", "content_key": "c2", "page_id": "p2", "parent_id": "p2",
                  "page_number": 2, "kind": "text", "text": "M2 voltage 2 V"}
    rows = [image_chunk, text_chunk]
    with RetrievalStore(tmp_path, create=True, dimension=2) as store:
        vectors = np.array([[1., 0.], [1., 0.]], dtype=np.float32)
        store.add(rows, vectors)
        hierarchy = HierarchyStore(store, create=True)
        try:
            nodes = build_hierarchy(rows, [{"document_id": "d1", "source_relative": "diagram.pdf"}])
            hierarchy.add(nodes, np.tile(vectors[0], (len(nodes), 1)))
        finally:
            hierarchy.close()
    def retrieve(store, hierarchy, plan, config, manifest):
        row = store.rows["c1" if "M1" in plan.search_question else "c2"]
        return [RetrievedEvidence(row, 1, evidence_id="E1")], {"escape": "scripted task hits"}
    monkeypatch.setattr("denso_a3.pipeline.research_runner.retrieve_hierarchical", retrieve)
    image_reading = observation("5 V", requirement_id="T1")
    image_reading["observations"][0].update(observed_text="M1 voltage 5 V", unit_text="V", row_label="voltage")
    text_reading = {"found": True, "chunk_id": "c2", "quote": "M2 voltage 2 V", "raw_value": "2 V",
                    "period": "", "scenario": "", "missing_query": ""}
    generator = ScriptedLocal([image_reading, text_reading])
    tasks = [{"task_id": tid, "query": query, "label": query, "entity": entity,
              "kind": "quantity", "expected_unit": "V", "depends_on": []} for tid, query, entity in
             [("T1", "M1 voltage in image", "M1"), ("T2", "M2 voltage from text", "M2")]]
    research_plan = {"tasks": tasks, "calculations": [{"id": "C1", "label": "Voltage difference", "operation": "subtract", "inputs": ["T1", "T2"]}],
                     "question_conditions": [], "clarification": ""}
    question = "Calculate difference of voltages using the M1 image and M2 text."
    result = run_research(tmp_path, question, QueryPlan(question, question, "en", intents=["visual"]),
        RagConfig(orchestration_mode="hierarchical"), {"model_manifest": {}}, generator, research_plan=research_plan)
    assert result.status == "answered" and result.trace["coverage"]["ready"]
    assert result.trace["calculations"][0]["value"] == "3" and result.trace["calculations"][0]["dimensions"] == {"V": 1}
    assert result.trace["facts"]["T1"]["support_kind"] == "image" and result.trace["facts"]["T2"]["support_kind"] == "text"
    assert sum(bool(call["images"]) for call in generator.calls) == 1
    assert result.citations[0]["support_kind"] == "image" and result.citations[0]["visual_observation_id"] == "V1"


def test_visual_context_guard_reserves_image_and_answer_tokens(tmp_path):
    hit = source(tmp_path)
    config = RagConfig(context_tokens=2048, max_answer_tokens=600)
    generator = ScriptedLocal([])
    with pytest.raises(ContextBudgetExceeded, match="visual evidence"):
        run_visual_phase(tmp_path, QueryPlan("Image?", "Image?", "en", intents=["visual"]), [hit], config, {},
                         generator, QueryBudget(config), requirements())
    assert generator.calls == []


def test_named_field_is_observed_even_without_literal_word_name(tmp_path):
    hit = source(tmp_path)
    reading = {"observations": [{"image_id": "I1", "requirement_id": "R1", "state": "observed",
        "observed_text": "University of California, San Diego", "raw_value": "University of California, San Diego",
        "unit_text": "", "entity_label": "", "row_label": "University", "column_label": ""}]}
    reqs = [QueryRequirement("R1", "university name", attribute="name", wording="university name")]
    result = run_visual_phase(tmp_path, QueryPlan("Name from image?", "Name from image?", "en", intents=["visual"]), [hit],
        RagConfig(), {}, ScriptedLocal([reading]), QueryBudget(RagConfig()), reqs)
    assert result.observations[0].state == "observed" and result.missing_requirement_ids == []


def test_fullpage_and_contained_ocr_region_send_one_source_image(tmp_path):
    full = source(tmp_path)
    crop = replace(full, chunk={**full.chunk, "chunk_id": "c2", "kind": "text", "bbox": [0, 0, .5, 1]})
    generator = ScriptedLocal([observation()])
    result = run_visual_phase(tmp_path, QueryPlan("Read image", "Read image", "en", intents=["visual"]), [crop, full],
        RagConfig(), {}, generator, QueryBudget(RagConfig()), requirements())
    assert len(result.selected_images) == len(generator.calls[0]["images"]) == 1
    assert result.selected_images[0].source_bbox == [0, 0, 1, 1]
    assert result.selected_images[0].chunk_ids == ["c1", "c2"]


def test_unreadable_with_model_value_never_becomes_observed(tmp_path):
    hit = source(tmp_path)
    bad = observation(state="unreadable")
    generator = ScriptedLocal([bad, observation("", state="unreadable")])
    result = run_visual_phase(tmp_path, QueryPlan("Read image", "Read image", "en", intents=["visual"]), [hit],
        RagConfig(), {}, generator, QueryBudget(RagConfig()), requirements())
    assert len(generator.calls) == 2 and result.observations[0].state == "unreadable"
    assert result.observations[0].raw_value == "" and result.missing_requirement_ids == ["R1"]


def test_fractional_source_crop_cannot_expand_outside_allowed_pixels(tmp_path):
    hit = source(tmp_path, "figure", bbox=[.495, 0, 1, 1])
    plan = QueryPlan("Read image", "Read image", "en", hard_filters={"kind": "figure"}, intents=["visual"])
    generator = ScriptedLocal([observation()])
    run_visual_phase(tmp_path, plan, [hit], RagConfig(), {}, generator, QueryBudget(RagConfig()), requirements())
    sent = Image.open(io.BytesIO(base64.b64decode(generator.calls[0]["images"][0].split(",")[1])))
    assert sent.size == (50, 50) and sent.getpixel((0, 25)) == (0, 0, 255)


def test_less_than_one_allowed_pixel_is_missing_without_model_call(tmp_path):
    hit = source(tmp_path, "figure", bbox=[.5, 0, .5001, 1])
    generator = ScriptedLocal([])
    result = run_visual_phase(tmp_path, QueryPlan("Read image", "Read image", "en", intents=["visual"]), [hit],
        RagConfig(), {}, generator, QueryBudget(RagConfig()), requirements())
    assert not generator.calls and result.missing_requirement_ids == ["R1"]
    assert result.trace["validation_errors"] == ["empty_source_crop:c1"]
