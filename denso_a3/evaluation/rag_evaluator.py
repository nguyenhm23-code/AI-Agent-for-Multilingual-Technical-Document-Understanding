"""Development-set evaluation; draft labels are not human-reviewed ground truth."""
import json
import re
import time
from pathlib import Path
import numpy as np
from ..backends.artifacts import read_json
from ..io import write_json
from ..pipeline.rag_runner import ask
from .routing_metrics import trace_metrics, summarize_trace_metrics


def normalize(text):
    return re.sub(r"\s+", " ", str(text).casefold()).strip()


def draft_labels(index, qa):
    chunks = read_json(Path(index) / "chunks.json")
    labels = []
    for row in qa:
        matches = [c for c in chunks if c["source_relative"] == row["image_path"] and c["kind"] != "page_visual"
                   and any(normalize(a) in normalize(c["text"]) for a in row["answers"] if normalize(a))]
        labels.append({"id": row["id"], "source": row["image_path"], "review_status": "unreviewed",
            "method": "casefold_whitespace_answer_substring", "status": "located" if matches else "not_located",
            "evidence": [{"chunk_id": c["chunk_id"], "content_key": c["content_key"], "page_id": c["page_id"],
                          "bbox": c.get("bbox"), "block_ids": c.get("block_ids", []), "kind": c["kind"]} for c in matches]})
    return labels


def evaluate_rag(index, qa_path, output, config, strategy=None, limit=None,
                 retrieval_only=False, scope="source", language="en"):
    target = Path(output)
    if target.exists():
        raise ValueError("Use a new evaluation output directory")
    qa = read_json(Path(qa_path))
    if any("assumptions" in row for row in qa):
        raise ValueError("Legacy assumptions fixture is unsupported. Put explicit conditions in each question/clarification.")
    target.mkdir(parents=True)
    if limit:
        qa = qa[:limit]
    labels = draft_labels(index, qa)
    write_json(target / "evidence_draft.json", labels)
    manifest = read_json(Path(index) / "manifest.json")
    registry = read_json(Path(index) / "registry.json")
    translated = {}
    if language == "vi":
        from ..backends.generation import Generator
        generator = Generator(config, manifest["model_manifest"])
        try:
            for row in qa:
                result, _ = generator.generate_json("Translate the question into Vietnamese. Preserve names, identifiers, numbers and units. Return JSON {question: string}.", row["question"], max_tokens=180,
                    schema={"type": "object", "properties": {"question": {"type": "string"}},
                            "required": ["question"], "additionalProperties": False})
                if not isinstance(result.get("question"), str) or not result["question"].strip():
                    raise ValueError("Translation failed")
                translated[row["id"]] = result["question"]
        finally:
            generator.close()
        write_json(target / "translations.json", {"review_status": "auto_translated_unreviewed", "questions": translated})
    records = []
    for i, (row, label) in enumerate(zip(qa, labels), 1):
        record = {"id": row["id"], "question_type": row.get("question_type", []), "scope": scope, "language": language}
        question = translated.get(row["id"], row["question"])
        filters = {"source_relative": row["image_path"]} if scope == "source" else {}
        if scope == "global":
            question += " (" + row["image_path"] + ")"
            record["global_variant"] = "source filename appended as retrieval hint, not a hard filter"
        tick = time.perf_counter()
        try:
            if retrieval_only:
                from ..bt5.store import RetrievalStore
                from ..bt6.planner import plan_query
                from ..bt6.retrieval import retrieve
                plan = plan_query(question, registry, strategy or config.retrieval_strategy, filters,
                    visual_policy=config.visual_policy)
                with RetrievalStore(index) as store:
                    evidence, trace = retrieve(store, plan, config, manifest)
                answer = {"answer": "", "status": "retrieval_only", "evidence": [e.to_dict() for e in evidence], "citations": [], "trace": trace}
            else:
                answer = ask(index=index, question=question, config=config, strategy=strategy, filters=filters)
            record.update(trace_metrics(answer, row))
            retrieved = [e["chunk"]["content_key"] for e in answer["evidence"]]
            expected = {e["content_key"] for e in label["evidence"]}
            ranks = [j+1 for j, key in enumerate(retrieved) if key in expected]
            record.update(answer=answer, answer_substring_proxy=any(normalize(a) in normalize(answer["answer"]) for a in row["answers"]),
                evidence_hit5_draft=bool(ranks and min(ranks) <= 5) if expected else None,
                reciprocal_rank_draft=1/min(ranks) if ranks else (0 if expected else None),
                scope_valid=all(e["chunk"]["source_relative"] == row["image_path"] for e in answer["evidence"]) if scope == "source" else None,
                citation_pointer_valid=all((Path(index)/c["image_asset"]).is_file() for c in answer["citations"]))
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        record["wall_seconds"] = time.perf_counter()-tick
        records.append(record)
        write_json(target / "records.json", records)
        print(f"QA {i}/{len(qa)}: {record.get('error') or record['answer']['status']}", flush=True)
    hits = [r["evidence_hit5_draft"] for r in records if r.get("evidence_hit5_draft") is not None]
    timings = [r["wall_seconds"] for r in records if "error" not in r]
    summary = {"questions": len(qa), "errors": sum("error" in r for r in records), "strategy": strategy,
        "config": config.to_dict(), "index": str(Path(index).resolve()), "retrieval_only": retrieval_only, "scope": scope, "language": language,
        "orchestration_mode": config.orchestration_mode, "visual_policy": config.visual_policy,
        "labels_located": sum(x["status"] == "located" for x in labels), "labels_reviewed": False,
        "evidence_hit5_draft": sum(hits)/len(hits) if hits else None,
        "answer_substring_proxy": sum(r.get("answer_substring_proxy", False) for r in records)/len(records) if records and not retrieval_only else None,
        "latency_seconds": {"p50": float(np.percentile(timings, 50)), "p95": float(np.percentile(timings, 95))} if timings else {},
        "timing_regime": "models reloaded each request; OS disk cache may be warm; no steady-state inference claim",
        "limitations": ["Development set only", "Evidence labels auto-generated, unreviewed", "Substring match is not semantic/groundedness accuracy", "Citation pointer validity is not citation correctness", "Semantic answer and visual observation correctness require source review"],
        **summarize_trace_metrics(records)}
    write_json(target / "summary.json", summary)
    return summary
