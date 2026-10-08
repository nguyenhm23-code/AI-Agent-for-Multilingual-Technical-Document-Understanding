"""Load references only AFTER prediction artifacts have been committed."""

from pathlib import Path
import json

from ..io import sha256_file, write_json
from .text import compare, aggregate
from .structural import evaluate_tables, evaluate_layout


def split_reference(text: str, page_count: int) -> list[str]:
    pieces = text.replace("\r\n", "\n").replace("\r", "\n").split("\f")
    if len(pieces) == page_count + 1 and pieces[-1] == "":
        pieces.pop()  # Only a literal terminal separator; keep interior blank pages.
    if len(pieces) != page_count:
        raise ValueError(f"reference_page_count_mismatch: {len(pieces)} != {page_count}")
    return pieces


def evaluate(run_dir: Path, data_root: Path, pilot_path: Path | None = None) -> dict:
    pages = json.loads((run_dir / "pages.json").read_text(encoding="utf-8"))
    documents = json.loads((run_dir / "documents.json").read_text(encoding="utf-8"))
    predictions = {x["page_id"]: x for x in json.loads((run_dir / "bt2.json").read_text(encoding="utf-8"))}
    layouts = json.loads((run_dir / "bt3.json").read_text(encoding="utf-8"))
    references, registry, page_results = {}, [], []
    for document in documents:
        if document["format"] != "pdf":
            continue
        path = (data_root / document["source_relative"]).with_suffix(".ocr")
        if not path.is_file():
            continue
        entry = {"source_relative": document["source_relative"], "source_sha256": document.get("source_sha256"),
                 "reference_path": str(path.resolve()), "reference_sha256": sha256_file(path),
                 "origin": "user_supplied", "review_status": "unreviewed", "status": "unaligned"}
        registry.append(entry)
        try:
            segments = split_reference(path.read_text(encoding="utf-8-sig"), document["page_count"])
            for i, segment in enumerate(segments, 1):
                references[(document["document_id"], i)] = segment
            entry["status"], entry["page_count"] = "aligned", len(segments)
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
    for page in pages:
        reference = references.get((page["document_id"], page["page_number"]))
        row = {"page_id": page["page_id"], "status": "not_evaluated", "reason": "no_text_reference"}
        if reference is not None:
            predicted = predictions.get(page["page_id"], {})
            row = {"page_id": page["page_id"], "status": "evaluated", "reference": reference,
                   "prediction_status": predicted.get("status", "missing"),
                   "raw": compare(reference, predicted.get("text", ""), "raw"),
                   "whitespace": compare(reference, predicted.get("text", ""), "whitespace")}
        page_results.append(row)
    assessed = [x for x in page_results if x["status"] == "evaluated"]
    result = {"text_reference_kind": "agreement_with_supplied_unreviewed_reference",
              "evaluated_pages": len(assessed), "not_evaluated_pages": len(pages)-len(assessed),
              "raw": aggregate([x["raw"] for x in assessed]),
              "whitespace": aggregate([x["whitespace"] for x in assessed]),
              "pages": page_results, "reference_registry": registry,
              "layout": {"status": "not_evaluated", "reason": "no_exhaustive_layout_annotations"},
              "tables": {"status": "not_evaluated", "reason": "no_table_annotations"}}
    if pilot_path and pilot_path.is_file():
        pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
        result["tables"] = evaluate_tables(pilot, pages, layouts)
        result["layout"] = evaluate_layout(pilot, pages, layouts)
        result["pilot_registry"] = {"path":str(pilot_path.resolve()),"sha256":sha256_file(pilot_path)}
    write_json(run_dir / "ground_truth_registry.json", registry)
    write_json(run_dir / "metrics.json", result)
    return result
