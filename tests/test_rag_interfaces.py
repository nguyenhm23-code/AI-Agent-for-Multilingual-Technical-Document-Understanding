import json

import pytest

from denso_a3.contracts import RagConfig
from denso_a3.pipeline import rag_cli, rag_runner
from denso_a3.pipeline.rag_config import load_config
from denso_a3.evaluation import multidoc_evaluator
from denso_a3.evaluation.routing_metrics import trace_metrics, summarize_trace_metrics


@pytest.mark.parametrize("mode", ["auto", "baseline", "hierarchical"])
def test_cli_passes_keyword_arguments_and_preserves_forced_modes(monkeypatch, mode, capsys):
    seen = []
    def ask(**kwargs):
        seen.append(kwargs)
        return {"status": "abstain", "answer": "No evidence"}
    monkeypatch.setattr(rag_runner, "ask", ask)
    assert rag_cli.main(["rag-ask", "--index", "fixture", "--question", "q", "--mode", mode]) == 0
    assert seen[0]["config"].orchestration_mode == mode
    assert seen[0]["strategy"] is None
    assert "assumptions" not in seen[0]


@pytest.mark.parametrize("argument", ["--assumptions", "--assumptions=old.json"])
def test_cli_legacy_assumptions_has_migration_error(argument, capsys):
    with pytest.raises(SystemExit) as exc:
        rag_cli.main(["rag-ask", "--index", "fixture", "--question", "q", argument])
    assert exc.value.code == 2
    assert "--assumptions was removed" in capsys.readouterr().err


def test_production_and_benchmark_configs_are_separate():
    local = load_config("configs/rag.local.json")
    assert (local.orchestration_mode, local.retrieval_strategy, local.visual_policy, local.local_only) == ("auto", "hybrid", "auto", True)
    assert load_config("configs/rag.baseline.json").orchestration_mode == "baseline"
    assert load_config("configs/rag.hierarchical.json").orchestration_mode == "hierarchical"


def test_evaluator_keeps_auto_mode_and_reports_routing_budget(tmp_path, monkeypatch):
    gold = {"review_status": "human_reviewed", "questions": [{"id": "q", "question": "Orion voltage?",
        "status": "abstain", "acceptable_routes": ["baseline"]}]}
    qa = tmp_path / "qa.json"
    qa.write_text(json.dumps(gold), encoding="utf-8")
    seen = []
    def ask(**kwargs):
        seen.append(kwargs)
        return {"status": "abstain", "evidence": [], "citations": [], "trace": {
            "routing": {"route": "baseline", "source": "rules"}, "attempts": [{"route": "baseline"}],
            "stop_reason": "no_evidence", "budget": {"retrieval_calls": 1, "router_calls": 0,
                "planner_calls": 0, "generation_attempts": 0, "visual_calls": 0, "escalations": 0}},
            "timings_ms": {"routing": 2, "total": 10}, "usage": {}}
    monkeypatch.setattr(multidoc_evaluator, "ask", ask)
    summary = multidoc_evaluator.evaluate_multidoc("fixture", qa, tmp_path / "out", RagConfig())
    assert seen[0]["config"].orchestration_mode == "auto"
    assert "assumptions" not in seen[0]
    assert summary["routing_agreement"] == {"value": 1, "evaluated": 1}
    assert summary["model_and_retrieval_calls"]["retrieval_calls"]["total"] == 1
    assert summary["stage_latency_ms"]["routing"]["p50"] == 2
    assert "latency_seconds" in summary


def test_evaluator_rejects_legacy_fixture_before_creating_output(tmp_path):
    qa = tmp_path / "qa.json"
    qa.write_text(json.dumps({"review_status": "human_reviewed", "questions": [{"id": "q", "assumptions": {}}]}), encoding="utf-8")
    target = tmp_path / "out"
    with pytest.raises(ValueError, match="Legacy assumptions"):
        multidoc_evaluator.evaluate_multidoc("fixture", qa, target, RagConfig())
    assert not target.exists()


def test_missing_trace_metrics_are_not_claimed_as_measured_calls():
    record = {**trace_metrics({"trace": {}, "status": "abstain"}), "wall_seconds": 1}
    summary = summarize_trace_metrics([record])
    assert summary["routing_agreement"]["evaluated"] == 0
    assert summary["model_and_retrieval_calls"]["generation_attempts"]["requests_measured"] == 0


def test_evaluator_reads_dispatcher_initial_final_route_and_all_attempt_timings():
    answer = {"trace": {"routing": {"initial_route": "baseline", "final_route": "hierarchical", "method": "rules"},
        "attempts": [{"route": "baseline", "timings_ms": {"retrieval": 10}},
            {"route": "hierarchical", "timings_ms": {"retrieval": 30}}],
        "budget": {"escalations": 1}, "stop_reason": "retrieval_calls_budget"}, "timings_ms": {"total": 50}}
    metrics = trace_metrics(answer, {"acceptable_routes": ["hierarchical"]})
    assert metrics["routing_agreement"] and not metrics["initial_routing_agreement"]
    assert metrics["route_source"] == "rules" and metrics["escalated"]
    assert metrics["timings_ms"]["attempt_1_baseline_retrieval"] == 10
    assert metrics["timings_ms"]["attempt_2_hierarchical_retrieval"] == 30
    assert summarize_trace_metrics([{**metrics, "wall_seconds": .05}])["budget_exits"] == 1


def test_evaluator_condition_provenance_rejects_forged_user_input():
    question = "Assume 100% transfer to sales."
    condition = {"condition_id": "U1", "kind": "ratio", "raw_text": "100% transfer to sales",
        "origin": "user_question", "applies_to": "transfer to sales"}
    answer = {"status": "abstain", "trace": {"question_conditions": [condition]}, "citations": []}
    gold = {"status": "abstain", "question": question, "expected_condition": {"value": "1", "origin": "user_question"}}
    assert multidoc_evaluator.score_record(answer, gold)["condition_provenance_valid"]
    forged = {**answer, "trace": {"question_conditions": [{**condition, "raw_text": "50% transfer to sales"}]}}
    assert not multidoc_evaluator.score_record(forged, gold)["condition_provenance_valid"]
    fake_citation = {**answer, "citations": [{"evidence_id": "U1", "chunk_id": "a"}]}
    assert not multidoc_evaluator.score_record(fake_citation, gold)["condition_provenance_valid"]
# Both --source and --filters are user scope, so neither may overwrite the other.
def test_cli_source_intersects_filter_scope(tmp_path,monkeypatch):
    import json
    from denso_a3.pipeline import rag_cli, rag_runner
    filters=tmp_path/"filters.json"
    filters.write_text(json.dumps({"source_relative":["A.pdf"],"page_number":1}),encoding="utf-8")
    received={}
    def capture(**kwargs):
        received.update(kwargs)
        return {"status":"abstain","answer":"No source in intersection"}
    monkeypatch.setattr(rag_runner,"ask",capture)
    assert rag_cli.main(["rag-ask","--index",str(tmp_path),"--question","Pressure?","--filters",str(filters),"--source","B.pdf"])==0
    assert received["filters"]=={"source_relative":[],"page_number":1}
