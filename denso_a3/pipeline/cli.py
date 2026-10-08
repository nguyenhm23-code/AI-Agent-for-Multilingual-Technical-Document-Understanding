import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from ..contracts import PipelineConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def configuration(args):
    return PipelineConfig(input_dir=str(Path(args.input).resolve()),
                          cache_dir=str(PROJECT_ROOT / ".cache"), model_dir=str(PROJECT_ROOT / ".cache/models"),
                          dpi=args.dpi, device=args.device, ocr_backend=args.ocr_backend,
                          language=args.language,
                          threads=args.threads, timeout_seconds=args.timeout,
                          startup_timeout_seconds=args.startup_timeout)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0].startswith("rag-"):
        from .rag_cli import main as rag_main
        return rag_main(argv)
    parser = argparse.ArgumentParser(description="DENSO A3: fresh OCR + layout + tables")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("ingest", "doctor", "run"):
        command = sub.add_parser(name)
        command.add_argument("--input", default=str(PROJECT_ROOT / "evaluation/data"))
        command.add_argument("--dpi", type=int, default=200)
        command.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
        command.add_argument("--ocr-backend", choices=["torch"], default="torch")
        command.add_argument("--language", choices=["en", "vi"], default="en")
        command.add_argument("--threads", type=int, default=4)
        command.add_argument("--timeout", type=int, default=180)
        command.add_argument("--startup-timeout", type=int, default=600)
        command.add_argument("--output", default=None)
        if name != "ingest":
            command.add_argument("--download-models", action="store_true", help="Explicit online bootstrap; default is offline")
        if name == "run":
            command.add_argument("--pilot", default=str(PROJECT_ROOT / "ground_truth/pilot.json"))
            command.add_argument("--limit", type=int)
            command.add_argument("--source", help="Exact path relative to input root")
            command.add_argument("--pages", type=int, nargs="+")
            command.add_argument("--bypass-cache", action="store_true")
    evaluation = sub.add_parser("evaluate")
    evaluation.add_argument("--run", required=True)
    evaluation.add_argument("--input", default=str(PROJECT_ROOT / "evaluation/data"))
    evaluation.add_argument("--pilot", default=str(PROJECT_ROOT / "ground_truth/pilot.json"))
    args = parser.parse_args(argv)
    try:
        if args.command == "evaluate":
            from ..evaluation.evaluator import evaluate
            from ..review.html import create_review
            run_dir = Path(args.run).resolve()
            result = evaluate(run_dir, Path(args.input).resolve(), Path(args.pilot).resolve())
            read = lambda name: json.loads((run_dir / name).read_text(encoding="utf-8"))
            create_review(run_dir, read("pages.json"), read("bt2.json"), read("bt3.json"), result)
            print(json.dumps({"evaluated_pages": result["evaluated_pages"], "whitespace": result["whitespace"], "tables": {k:v for k,v in result["tables"].items() if k != "pages"}}, indent=2))
            return 0
        config = configuration(args)
        config.validate()
        if args.command == "run" and args.limit is not None and args.limit < 1:
            raise ValueError("limit must be positive")
        if args.command == "ingest":
            from ..bt1.ingestion import ingest
            from ..io import write_json
            documents, pages, summary = ingest(config)
            output = Path(args.output).resolve() if args.output else PROJECT_ROOT / "results/ingestion"
            write_json(output / "documents.json", documents)
            write_json(output / "pages.json", [p.to_dict() for p in pages])
            write_json(output / "ingestion.json", summary)
            print(json.dumps({k:v for k,v in summary.items() if k != "alias_groups"}, indent=2))
            return int(any(x["status"] != "ok" for x in documents))
        if args.command == "doctor":
            from .worker import InferenceWorker
            from ..io import write_json
            worker = InferenceWorker(config, args.download_models)
            try:
                runtime = worker.start()
                output = Path(args.output).resolve() if args.output else PROJECT_ROOT / "results/doctor.json"
                write_json(output, runtime)
                print(json.dumps({k:v for k,v in runtime.items() if k not in {"models", "pipeline_options"}}, indent=2))
                return 0
            finally:
                worker.close()
        from .runner import run
        output = Path(args.output).resolve() if args.output else PROJECT_ROOT / "results" / datetime.now().strftime("run_%Y%m%d_%H%M%S")
        result = run(config, output, Path(args.pilot).resolve(), args.limit, args.source, args.pages,
                     args.download_models, args.bypass_cache)
        print(json.dumps(result, indent=2))
        print(f"Review: {output / 'review.html'}")
        return int(bool(result["startup_error"]) or bool(result["bt2_statuses"].get("failed")) or bool(result["bt3_statuses"].get("failed")))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
