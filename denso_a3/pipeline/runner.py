"""BT1 -> joint C adapter -> independent artifacts -> evaluation/review."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time

from ..contracts import PipelineConfig, SCHEMA_VERSION
from ..io import fingerprint, sha256_file, write_json, write_text
from ..bt1.ingestion import ingest
from ..bt3.tables import table_html, table_markdown, continuation_candidates
from ..evaluation.evaluator import evaluate
from .worker import InferenceWorker


def code_fingerprint():
    root = Path(__file__).parents[1]
    return fingerprint([{ "path": p.relative_to(root).as_posix(), "sha256": sha256_file(p)}
                        for p in sorted(root.rglob("*.py"))])


def cache_key(page, config, runtime, implementation):
    return fingerprint({"image_sha256": page.image_sha256, "config": config.inference_dict(),
                        "versions": runtime["versions"], "models": runtime["model_fingerprint"],
                        "code": implementation, "schema": SCHEMA_VERSION})


def bind_payload(payload, page):
    """Rebind cache-local IDs to logical source identity, including duplicates."""
    payload = deepcopy(payload)
    prefix = page.page_id + ":"
    for stage in ("bt2", "bt3"):
        payload[stage].update({"page_id": page.page_id, "document_id": page.document_id,
                              "source_relative": page.source_relative, "page_number": page.page_number})
    for span in payload["bt2"]["spans"]:
        span["span_id"] = prefix + span["span_id"]
    for block in payload["bt3"]["blocks"]:
        block["block_id"] = prefix + block["block_id"]
        block["span_ids"] = [prefix + x for x in block["span_ids"]]
    for table in payload["bt3"]["tables"]:
        table["table_id"] = prefix + table["table_id"]
        table["block_id"] = prefix + table["block_id"]
        for cell in table["cells"]:
            cell["span_ids"] = [prefix + x for x in cell["span_ids"]]
    return payload


def failure_payload(message):
    return {"bt2": {"status": "failed", "text": "", "spans": [], "errors": [message]},
            "bt3": {"status": "failed", "blocks": [], "tables": [], "errors": [message]},
            "timing": {}, "conversion_status": "failure"}


def run(config: PipelineConfig, run_dir: Path, pilot_path: Path | None = None,
        limit: int | None = None, source: str | None = None, page_numbers: list[int] | None = None,
        allow_download: bool = False, bypass_cache: bool = False) -> dict:
    config.validate()
    run_dir = run_dir.resolve()
    if run_dir.exists() and any(run_dir.iterdir()):
        raise ValueError("Use a new output directory; existing runs are immutable")
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    documents, all_pages, ingestion = ingest(config)
    selected = [p for p in all_pages if (source is None or p.source_relative == source)
                and (not page_numbers or p.page_number in page_numbers)]
    if limit is not None:
        selected = selected[:limit]
    pages = [p.to_dict() for p in selected]
    write_json(run_dir / "documents.json", documents)
    write_json(run_dir / "pages.json", pages)
    write_json(run_dir / "ingestion.json", ingestion)
    write_json(run_dir / "run_config.json", {**config.__dict__, "schema_version": SCHEMA_VERSION,
               "selected_pages": len(selected), "full_dataset": len(selected) == len(all_pages),
               "created_at": datetime.now(timezone.utc).isoformat(), "source_filter": source,
               "page_filter": page_numbers, "limit": limit, "bypass_cache": bypass_cache})
    ingestion_seconds = time.perf_counter() - started
    worker = InferenceWorker(config, allow_download)
    bt2, bt3, execution, cache_results = [], [], [], {}
    implementation = code_fingerprint()
    runtime, startup_error = None, None
    init_started = time.perf_counter()
    try:
        runtime = worker.start()
        write_json(run_dir / "runtime.json", runtime)
    except Exception as exc:
        startup_error = f"{type(exc).__name__}: {exc}"
        write_text(run_dir / "startup_error.txt", startup_error)
    startup_seconds = time.perf_counter() - init_started
    try:
        for index, page in enumerate(selected, 1):
            tick = time.perf_counter()
            kind, key = "failed", None
            if page.status != "ok":
                payload = failure_payload(page.error or "ingestion_failed")
            elif startup_error:
                payload = failure_payload("model_startup_failed: " + startup_error.splitlines()[0])
            else:
                key = cache_key(page, config, runtime, implementation)
                cache_path = Path(config.cache_dir) / "predictions" / (key + ".json")
                try:
                    if key in cache_results:
                        payload, kind = cache_results[key], "alias_reuse"
                    elif cache_path.is_file() and not bypass_cache:
                        cached = json.loads(cache_path.read_text(encoding="utf-8"))
                        if cached.get("cache_key") != key:
                            raise ValueError("Prediction cache key mismatch")
                        payload, kind = cached["payload"], "disk_cache_hit"
                    else:
                        payload, kind = worker.convert(page.image_path), "inferred"
                        if payload["conversion_status"] == "success" and not payload["bt2"]["errors"] and not payload["bt3"]["errors"]:
                            write_json(cache_path, {"cache_key": key, "payload": payload})
                    cache_results[key] = payload
                except Exception as exc:
                    payload = failure_payload(f"joint_bt2_bt3: {type(exc).__name__}: {exc}")
                    kind = "failed"
            bound = bind_payload(payload, page)
            bt2.append(bound["bt2"])
            bt3.append(bound["bt3"])
            row = {"page_id": page.page_id, "execution": kind, "cache_key": key,
                   "bt2_status": bound["bt2"]["status"], "bt3_status": bound["bt3"]["status"],
                   "page_wall_ms": round((time.perf_counter()-tick)*1000, 2),
                   "origin_timing": bound["timing"]}
            if "raw_document" in payload:
                raw_path = run_dir / "raw" / (page.image_sha256 + ".json")
                if not raw_path.is_file():
                    write_json(raw_path, {"docling_document":payload["raw_document"],
                                          "ocr_cells":payload["raw_ocr_cells"]})
                row["raw_artifact"] = raw_path.relative_to(run_dir).as_posix()
            execution.append(row)
            folder = run_dir / "pages" / page.page_id.replace(":", "_")
            write_json(folder / "bt2.json", bound["bt2"])
            write_json(folder / "bt3.json", bound["bt3"])
            write_text(folder / "ocr.txt", bound["bt2"]["text"])
            for number, table in enumerate(bound["bt3"]["tables"]):
                write_text(folder / f"table_{number}.html", table_html(table))
                write_text(folder / f"table_{number}.md", table_markdown(table))
            print(f"[{index}/{len(selected)}] {page.source_relative} page={page.page_number} {kind} BT2={row['bt2_status']} BT3={row['bt3_status']}", flush=True)
            # Resume artifacts survive interruption; aggregate files are atomic snapshots.
            write_json(run_dir / "bt2.json", bt2)
            write_json(run_dir / "bt3.json", bt3)
            write_json(run_dir / "execution.json", execution)
    finally:
        worker.close()
    write_json(run_dir / "bt2.json", bt2)
    write_json(run_dir / "bt3.json", bt3)
    write_json(run_dir / "continuation_candidates.json", continuation_candidates(bt3, pages))
    actual_times = [x["origin_timing"]["joint_bt2_bt3_ms"] for x in execution if x["execution"] == "inferred"]
    performance = {"logical_pages": len(pages), "execution_counts": dict(Counter(x["execution"] for x in execution)),
                   "bt2_statuses": dict(Counter(x["status"] for x in bt2)),
                   "bt3_statuses": dict(Counter(x["status"] for x in bt3)),
                   "ingestion_seconds": round(ingestion_seconds, 3), "startup_seconds": round(startup_seconds, 3),
                   "joint_inference_ms": {"total": round(sum(actual_times), 2),
                                          "median": statistics.median(actual_times) if actual_times else None,
                                          "p95": sorted(actual_times)[min(len(actual_times)-1, int(.95*len(actual_times)))] if actual_times else None},
                   "cuda_peak_allocated_bytes": max((x["origin_timing"].get("cuda_peak_allocated_bytes") or 0 for x in execution if x["execution"] == "inferred"), default=0),
                   "startup_error": startup_error, "implementation_fingerprint": implementation}
    metrics = evaluate(run_dir, Path(config.input_dir), pilot_path)
    from ..review.html import create_review
    create_review(run_dir, pages, bt2, bt3, metrics)
    performance["total_wall_seconds"] = round(time.perf_counter()-started, 3)
    write_json(run_dir / "performance.json", performance)
    return performance
