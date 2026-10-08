"""Controlled before/after regressions with a real local verifier plus natural end-to-end cases.

Faulty drafts are intentionally injected to reproduce bugs deterministically; their
verification uses the actual local model. This is a demo, not a corpus accuracy benchmark.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from denso_a3.backends.artifacts import read_json
from denso_a3.backends.generation import Generator
from denso_a3.bt4.metadata import create_registry, verified_values
from denso_a3.bt4.metadata_enrichment import confirm_suggestions
from denso_a3.bt4.hierarchy import build_hierarchy
from denso_a3.bt5.store import RetrievalStore
from denso_a3.bt5.hierarchy_store import HierarchyStore
from denso_a3.io import write_json
from denso_a3.pipeline.rag_config import load_config, models
from denso_a3.pipeline import rag_runner
from denso_a3.pipeline.index_builder import suggest_index_metadata, update_metadata


def prepare(root, config):
    root.mkdir(parents=True)
    (root / "assets").mkdir()
    sources = {
        "voltage": "M1 rated voltage: 24 V",
        "target": "BAT revenue target for 2025: GBP120",
        "ceo": "BAT revenue for 2025: GBP120",
        "negation": "M1 does not require calibration.",
        "alpha": "Alpha Latest Estimate cost: GBP100",
        "beta": "Beta Latest Estimate cost: GBP200",
        "metadata": "Document number: SOP-001\nRevision: B\nEffective from: 2026-10-04\nEquipment ID: M1\nPart number: P-42\nIssued by: DENSO\nDocument type: Manual\nLanguage: English",
    }
    rows, docs = [], []
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 30)
    for did, text in sources.items():
        image = Image.new("RGB", (1400, 550), "white")
        ImageDraw.Draw(image).multiline_text((20, 25), text, font=font, fill="black", spacing=10)
        path = root / "assets" / (did + ".png")
        image.save(path)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        path.rename(root / "assets" / (sha + ".png"))
        docs.append({"document_id": did, "source_relative": did + ".png", "source_sha256": sha})
        rows.append({"chunk_id": did, "content_key": did, "document_id": did, "source_relative": did + ".png",
            "source_sha256": sha, "page_id": did + ":p1", "page_number": None, "image_sha256": sha,
            "kind": "text", "text": text, "metadata": {}, "bbox": [0, 0, 1, 1], "parent_id": did + ":p1",
            "quality_flags": ["synthetic_demo_source"]})
    nodes = build_hierarchy(rows, docs)
    with RetrievalStore(root, create=True, dimension=2) as store:
        store.add(rows, np.tile(np.array([[1., 0.]], dtype=np.float32), (len(rows), 1)))
        hierarchy = HierarchyStore(store, create=True)
        try:
            hierarchy.add(nodes, np.tile(np.array([[1., 0.]], dtype=np.float32), (len(nodes), 1)))
        finally:
            hierarchy.close()
    for name, data in [("documents", docs), ("chunks", rows), ("registry", create_registry(docs)), ("hierarchy", nodes),
        ("manifest", {"state": "ready", "schema_version": "1.1", "hierarchy_version": "1.0",
                      "model_manifest": models(config), "chunk_count": len(rows)})]:
        write_json(root / (name + ".json"), data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--production", action="store_true", help="Run natural queries against the existing real-document snapshot")
    parser.add_argument("--index", help="Existing snapshot for --production")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError("Use a new output directory")
    config = replace(load_config("configs/rag.local.json"), rerank=False, retrieval_strategy="lexical",
        visual_policy="off", orchestration_mode="baseline", query_max_escalations=0, max_answer_tokens=1500)
    if args.production:
        config = load_config("configs/rag.local.json")
        output.mkdir(parents=True)
        index = Path(args.index or ".local/indexes/hierarchical_20261003").resolve()
    else:
        index = output / "snapshot"
        prepare(index, config)
    cases = [
        {"id": "valid_voltage", "source": "voltage", "question": "What is the rated voltage of M1?",
         "draft": "M1 rated voltage is 24 V", "expected_after": "answered"},
        {"id": "target_as_actual", "source": "target", "question": "What is BAT actual revenue for 2025?",
         "draft": "BAT actual revenue for 2025 is GBP120", "expected_after": "abstain"},
        {"id": "unrelated_ceo", "source": "ceo", "question": "Who is the CEO of BAT?",
         "draft": "The CEO of BAT is Alice", "expected_after": "abstain"},
        {"id": "reversed_negation", "source": "negation", "question": "Does M1 require calibration?",
         "draft": "M1 requires calibration", "expected_after": "abstain"},
    ]
    if args.production:
        cases = []
    results = []
    local_outputs = []
    class RecordedGenerator(Generator):
        def generate_json(self, system, prompt, *a, **kwargs):
            value, usage = super().generate_json(system, prompt, *a, **kwargs)
            local_outputs.append({"stage": "verification" if "independent answer verifier" in system else "generation", "output": value})
            return value, usage
    for case in cases:
        if args.cases and case["id"] not in args.cases:
            continue
        class InjectedDraft(RecordedGenerator):
            def generate_json(self, system, prompt, *a, **kwargs):
                if "Return status, claims and coverage JSON" in system:
                    packet = json.loads(prompt.split("\nPrevious")[0])
                    source = packet["sources"][0]
                    refs = [{"source_id": source["source_id"], "quote": source["text"]}]
                    return {"status": "answered", "claims": [{"text": case["draft"], "support_refs": refs}],
                        "coverage": [{"requirement_id": r["requirement_id"], "state": "supported", "support_refs": refs}
                                     for r in packet["requirements"]]}, {"total_tokens": 0, "provider": "scripted_fault_injection"}
                return super().generate_json(system, prompt, *a, **kwargs)
        rag_runner.Generator = InjectedDraft
        for label, enabled in [("before", False), ("after", True)]:
            local_outputs.clear()
            started = time.perf_counter()
            print("RUN", case["id"], label, flush=True)
            answer = rag_runner.ask(index, case["question"], replace(config, verify_answers=enabled),
                                    filters={"source_relative": case["source"] + ".png"})
            row = {"case_id": case["id"], "phase": label, "regime": "injected_draft_real_local_verifier",
                "answer": answer, "elapsed_seconds": time.perf_counter() - started,
                "passed": answer["status"] == ("answered" if label == "before" else case["expected_after"]),
                "local_outputs": list(local_outputs)}
            results.append(row)
            write_json(output / (case["id"] + "_" + label + ".json"), row)
            print("RESULT", case["id"], label, answer["status"], answer["verification"]["state"], row["passed"], flush=True)
    rag_runner.Generator = RecordedGenerator
    natural_cases = [
        {"id": "natural_voltage", "question": "What is the rated voltage of M1?", "sources": ["voltage.png"], "status": "answered", "mode": "baseline"},
        {"id": "natural_calculation", "question": "Calculate the sum of the Latest Estimate costs of Alpha and Beta in GBP.",
         "sources": ["alpha.png", "beta.png"], "status": "answered", "mode": "hierarchical"},
    ]
    if args.production:
        natural_cases = [
            {"id": "corpus_budget", "question": "What is the Latest Estimate cost for Jessie in GBP?",
             "sources": ["nhwc0428.pdf"], "filters": {"source_relative": "nhwc0428.pdf", "page_number": 11, "kind": "table"},
             "status": "answered", "mode": "auto"},
            {"id": "corpus_visual", "question": "Read the university name from the image.",
             "sources": ["images/24580.png"], "status": "answered", "mode": "auto"},
        ]
    for case in natural_cases:
        if args.cases and case["id"] not in args.cases: continue
        print("RUN", case["id"], flush=True)
        local_outputs.clear()
        started = time.perf_counter()
        answer = rag_runner.ask(index, case["question"], replace(config, orchestration_mode=case["mode"]),
                                filters=case.get("filters", {"source_relative": case["sources"]}))
        row = {"case_id": case["id"], "regime": "natural_real_local_generation_and_verification", "answer": answer,
               "passed": answer["status"] == case["status"] and answer["verification"]["state"] == "verified",
               "elapsed_seconds": time.perf_counter() - started, "local_outputs": list(local_outputs)}
        results.append(row)
        write_json(output / (case["id"] + ".json"), row)
        print("RESULT", case["id"], answer["status"], row["passed"], flush=True)
    if not args.production and (not args.cases or "metadata" in args.cases):
        print("RUN metadata", flush=True)
        target = output / "metadata_draft_snapshot"
        suggest_index_metadata(index, target, config)
        suggestions, registry = read_json(target / "metadata_suggestions.json"), read_json(target / "registry.json")
        confirmed = confirm_suggestions(registry, suggestions, read_json(target / "documents.json"),
            read_json(target / "chunks.json"), "metadata", {"revision_id": 0, "equipment_id": 0})
        confirmed_target = output / "metadata_confirmed_snapshot"
        update_metadata(target, confirmed, confirmed_target)
        with RetrievalStore(target) as store: before_allowed = store.allowed({"revision_id": "B"})
        with RetrievalStore(confirmed_target) as store: after_allowed = store.allowed({"revision_id": "B"})
        row = {"case_id": "metadata", "regime": "real_local_extractor_plus_rules_and_explicit_confirmation",
            "draft_trusted_fields": verified_values(registry["documents"]["metadata"]["fields"]),
            "confirmed_fields": verified_values(confirmed["documents"]["metadata"]["fields"]),
            "before_allowed": before_allowed, "after_allowed": after_allowed,
            "passed": not before_allowed and after_allowed == ["metadata"]}
        results.append(row)
        write_json(output / "metadata_demo.json", row)
        print("RESULT metadata", row["passed"], flush=True)
    summary = {"passed": sum(r["passed"] for r in results), "count": len(results), "results": results,
        "limitations": ["Real source smoke checks" if args.production else "Synthetic sources; not a human-reviewed corporate holdout",
            "Before uses the structural pipeline with verification explicitly disabled, not an old checkout",
            "Controlled faults inject the draft only; the new verifier uses the real local model",
            "Production hybrid/visual config" if args.production else "Lexical retrieval isolates verification; this is not an architecture/latency benchmark"]}
    write_json(output / "summary.json", summary)
    print(json.dumps({"passed": summary["passed"], "count": summary["count"]}), flush=True)
    return 0 if summary["passed"] == summary["count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
