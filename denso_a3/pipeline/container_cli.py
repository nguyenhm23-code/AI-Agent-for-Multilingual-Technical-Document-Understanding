"""Single-image entry point; each stage runs in a fresh process to release GPU memory."""

import argparse
from pathlib import Path
import subprocess
import sys


def execute(arguments):
    return subprocess.call([sys.executable, "-m", "denso_a3", *arguments])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["rag-ui", "--host", "0.0.0.0"]
    if argv[0] not in {"bootstrap-all", "pipeline"}:
        return execute(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap = sub.add_parser("bootstrap-all", help="Download OCR + RAG models; requires network and GPU")
    bootstrap.add_argument("--language", choices=["en", "vi"], default="en")
    pipeline = sub.add_parser("pipeline", help="Run OCR, build RAG index, optionally answer a question")
    pipeline.add_argument("--input", default="/app/evaluation/data")
    pipeline.add_argument("--output", required=True, help="New directory for extraction, index and answer")
    pipeline.add_argument("--language", choices=["en", "vi"], default="en")
    pipeline.add_argument("--config", default="/app/configs/rag.local.json")
    pipeline.add_argument("--question")
    pipeline.add_argument("--source")
    pipeline.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if args.command == "bootstrap-all":
        stages = [["doctor", "--language", args.language, "--download-models"], ["rag-bootstrap"]]
    else:
        output = Path(args.output).resolve()
        if output.exists():
            parser.error("--output must be a new directory; existing runs are never overwritten")
        if args.limit is not None and args.limit < 1:
            parser.error("--limit must be positive")
        extraction, index = output / "extraction", output / "index"
        run = ["run", "--input", args.input, "--output", str(extraction), "--language", args.language]
        if args.source:
            run += ["--source", args.source]
        if args.limit is not None:
            run += ["--limit", str(args.limit)]
        stages = [run, ["rag-build", "--run", str(extraction), "--output", str(index), "--config", args.config]]
        if args.question:
            ask = ["rag-ask", "--index", str(index), "--question", args.question,
                   "--config", args.config, "--output", str(output / "answer.json")]
            if args.source:
                ask += ["--source", args.source]
            stages.append(ask)
    for stage in stages:
        print("Running stage: " + stage[0], flush=True)
        status = execute(stage)
        if status:
            return status
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
