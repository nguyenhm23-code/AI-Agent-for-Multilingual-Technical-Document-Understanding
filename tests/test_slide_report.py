"""Report integrity/denominators/CLI integration; scripted responses, no model benchmark."""
import json
from pathlib import Path

import pytest

from denso_a3.contracts import RagConfig
from denso_a3.evaluation import slide_report as sr
from denso_a3.pipeline import rag_cli


@pytest.fixture
def setup(tmp_path, monkeypatch):
    index = tmp_path / "index"
    index.mkdir()
    (index / "manifest.json").write_text('{"state":"ready"}', encoding="utf-8")
    qa = tmp_path / "questions.json"
    output = tmp_path / "report.html"
    monkeypatch.setattr(sr, "environment_snapshot", lambda index, config: {"config": config.to_dict(), "index": str(index)})
    return index, qa, output, RagConfig(local_only=True)


def write_questions(path, rows):
    path.write_text(json.dumps({"title": "Thử tiếng Việt", "questions": rows}, ensure_ascii=False), encoding="utf-8-sig")


def question(identifier, **extra):
    return {"id": identifier, "question": f"Question {identifier}?", **extra}


def answer(status="answered", **extra):
    return {"status": status, "answer": "24 V", "citations": [], "trace": {}, **extra}


def test_report_keeps_errors_denominators_and_never_sends_gold(setup, monkeypatch):
    index, qa, output, config = setup
    write_questions(qa, [question("1", expected_answer="SECRET GOLD", reference="Gold page",
        expected_status="answered", filters={"source_relative": "a.pdf"}), question("2"), question("3"),
        {"id": "blank", "question": " "}])
    calls = []
    ticks = iter([0, 2, 10, 16, 20, 30])
    monkeypatch.setattr(sr.time, "perf_counter", lambda: next(ticks))
    def fake(**kwargs):
        # Each next question must see the previous completed results already on disk.
        assert len(sr.read_report(output)["records"]) == len(calls)
        calls.append(kwargs)
        assert "SECRET GOLD" not in str(kwargs)
        if len(calls) == 1:
            return answer(citations=[{"source_relative": "a.pdf", "page_number": 1}],
                verification={"state": "verified"}, usage={"total_tokens": 30})
        if len(calls) == 2:
            return answer("abstain")
        raise RuntimeError("model failed")
    monkeypatch.setattr(sr, "ask", fake)
    result = sr.evaluate_for_slides(index, qa, output, config)
    report = sr.read_report(output)
    assert result["state"] == "complete"
    assert result["planned"] == result["finished"] == 3
    assert result["answered_rate"] == sr.ratio(1, 3)
    assert result["runtime_error_rate"] == sr.ratio(1, 3)
    assert result["answered_with_citations"] == sr.ratio(1, 1)
    assert result["answered_verified_by_model"] == sr.ratio(1, 1)
    assert result["semantic_accuracy"] is None
    assert result["latency_seconds"]["finished_including_errors"]["mean"] == 6
    assert result["latency_seconds"]["finished_including_errors"]["p95"] == pytest.approx(9.6)
    assert result["latency_seconds"]["answered_only"]["p50"] == 2
    assert result["total_tokens"] == {"value": 30, "requests_measured": 1}
    assert report["records"][2]["error"] == "RuntimeError: model failed"
    assert report["blank_rows_skipped"] == 1
    assert calls[0]["filters"] == {"source_relative": "a.pdf"}
    assert set(calls[0]) == {"index", "question", "config", "filters"}
    assert report["records"][0]["test"]["expected_answer"] == "SECRET GOLD"


def test_interrupted_run_preserves_partial_report_and_stops(setup, monkeypatch):
    index, qa, output, config = setup
    write_questions(qa, [question(str(i)) for i in range(3)])
    calls = []
    def fake(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return answer()
    monkeypatch.setattr(sr, "ask", fake)
    result = sr.evaluate_for_slides(index, qa, output, config)
    report = sr.read_report(output)
    assert result["state"] == "interrupted"
    assert result["finished"] == 1 and result["attempted"] == 2 and result["not_attempted"] == 1
    assert result["status_counts"]["interrupted"] == 1
    assert report["in_progress"] is None
    assert len(calls) == 2


@pytest.mark.parametrize("rows,match", [
    ([{"id": "1", "question": ""}], "Chưa có câu hỏi"),
    ([question("1"), question("1")], "không trùng"),
    ([question("1", filters=[])], "filters"),
    ([question("1", expected_status="correct")], "expected_status"),
    ([question("1", assumptions={})], "assumptions"),
])
def test_invalid_input_rejected_before_output_or_inference(setup, monkeypatch, rows, match):
    index, qa, output, config = setup
    write_questions(qa, rows)
    monkeypatch.setattr(sr, "ask", lambda **_: pytest.fail("No inference on invalid input"))
    with pytest.raises(ValueError, match=match):
        sr.evaluate_for_slides(index, qa, output, config)
    assert not output.exists()


def test_existing_evidence_is_never_overwritten(setup, monkeypatch):
    index, qa, output, config = setup
    write_questions(qa, [question("1")])
    output.write_text("original", encoding="utf-8")
    monkeypatch.setattr(sr, "ask", lambda **_: pytest.fail("Do not overwrite"))
    with pytest.raises(ValueError, match="đã tồn tại"):
        sr.evaluate_for_slides(index, qa, output, config)
    assert output.read_text(encoding="utf-8") == "original"


def test_html_escapes_untrusted_content_and_roundtrips_full_json(setup, monkeypatch):
    index, qa, output, config = setup
    malicious = '</script><script>alert("x")</script><img src=x onerror=alert(1)>'
    write_questions(qa, [question("1", expected_answer=malicious)])
    monkeypatch.setattr(sr, "ask", lambda **_: answer(answer=malicious))
    sr.evaluate_for_slides(index, qa, output, config)
    raw = output.read_text(encoding="utf-8")
    assert malicious not in raw
    assert "&lt;script&gt;" in raw
    assert sr.read_report(output)["records"][0]["result"]["answer"] == malicious


def test_zero_metrics_and_missing_token_counts_are_not_fabricated():
    result = sr.summarize([], 25)
    assert result["answered_rate"]["percent"] is None
    assert result["latency_seconds"]["answered_only"]["p95"] is None
    assert result["total_tokens"] == {"value": None, "requests_measured": 0}


def test_cli_runs_limits_and_reports_nonzero_for_errors(setup, monkeypatch, capsys):
    index, qa, output, config = setup
    write_questions(qa, [question("1"), question("2")])
    monkeypatch.setattr(rag_cli, "load_config", lambda _: config)
    def broken(**kwargs):
        raise RuntimeError("missing model")
    monkeypatch.setattr(sr, "ask", broken)
    assert rag_cli.main(["rag-evaluate-slides", "--index", str(index), "--questions", str(qa),
        "--output", str(output), "--limit", "1"]) == 1
    report = sr.read_report(output)
    assert report["summary"]["planned"] == 1 and report["summary"]["status_counts"] == {"error": 1}
    assert "report" in capsys.readouterr().out


def test_cli_interrupt_returns_130(setup, monkeypatch):
    index, qa, output, config = setup
    write_questions(qa, [question("1")])
    monkeypatch.setattr(rag_cli, "load_config", lambda _: config)
    def stop(**kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(sr, "ask", stop)
    assert rag_cli.main(["rag-evaluate-slides", "--index", str(index), "--questions", str(qa),
        "--output", str(output)]) == 130


def test_cli_missing_saved_index_gives_actionable_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rag_cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(rag_cli, "load_config", lambda _: RagConfig())
    assert rag_cli.main(["rag-evaluate-slides"]) == 1
    assert "--index" in capsys.readouterr().err
