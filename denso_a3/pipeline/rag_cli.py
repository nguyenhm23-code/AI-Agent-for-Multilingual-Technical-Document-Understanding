"""CLI dispatch kept separate from the extraction command parser."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from .rag_config import load_config, PROJECT as PROJECT_ROOT
from ..io import write_json


def main(argv):
    parser = argparse.ArgumentParser(description="DENSO metadata-aware RAG")
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap = sub.add_parser("rag-bootstrap")
    bootstrap.add_argument("--include-e5", action="store_true")
    bootstrap.add_argument("--root", default=str(PROJECT_ROOT / ".cache/rag"))
    ui = sub.add_parser("rag-ui")
    ui.add_argument("--port", type=int, default=8501)
    build = sub.add_parser("rag-build")
    build.add_argument("--run", nargs="+", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--registry")
    build.add_argument("--config")
    upgrade = sub.add_parser("rag-upgrade",help="Create a hierarchical snapshot without re-running OCR")
    upgrade.add_argument("--index",required=True)
    upgrade.add_argument("--output",required=True)
    upgrade.add_argument("--config")
    ask = sub.add_parser("rag-ask")
    ask.add_argument("--index", required=True)
    ask.add_argument("--question", required=True)
    ask.add_argument("--source", action="append")
    ask.add_argument("--filters", help="Path to JSON object of explicit hard filters")
    ask.add_argument("--strategy", choices=["auto", "lexical", "dense", "hybrid", "visual"],
        help="Debug retrieval override: auto aliases hybrid + visual auto; visual aliases hybrid + visual required")
    ask.add_argument("--config")
    ask.add_argument("--output")
    ask.add_argument("--mode",choices=["auto","baseline","hierarchical"],
        help="Auto by default; forced routes are for debugging and A/B evaluation")
    multidoc = sub.add_parser("rag-evaluate-multidoc")
    multidoc.add_argument("--index",required=True)
    multidoc.add_argument("--qa",required=True)
    multidoc.add_argument("--output",required=True)
    multidoc.add_argument("--config")
    multidoc.add_argument("--mode", choices=["auto", "baseline", "hierarchical"])
    ask.add_argument("--conversation")
    ask.add_argument("--memory", default=str(PROJECT_ROOT / ".local/conversations.sqlite"))
    meta = sub.add_parser("rag-metadata")
    meta.add_argument("--index", required=True)
    meta.add_argument("--registry", required=True)
    meta.add_argument("--output", required=True)
    suggestions = sub.add_parser("rag-suggest-metadata", help="Create source-backed drafts awaiting explicit user confirmation")
    suggestions.add_argument("--index", required=True)
    suggestions.add_argument("--output", required=True)
    suggestions.add_argument("--config")
    suggestions.add_argument("--rules-only", action="store_true", help="Extract labeled fields without loading the local LLM")
    evaluation = sub.add_parser("rag-evaluate")
    evaluation.add_argument("--index", required=True)
    evaluation.add_argument("--qa", required=True)
    evaluation.add_argument("--output", required=True)
    evaluation.add_argument("--config")
    evaluation.add_argument("--strategy", choices=["auto", "lexical", "dense", "hybrid", "visual"])
    evaluation.add_argument("--mode", choices=["auto", "baseline", "hierarchical"])
    evaluation.add_argument("--limit", type=int)
    evaluation.add_argument("--retrieval-only", action="store_true")
    evaluation.add_argument("--scope", choices=["source", "global"], default="source")
    evaluation.add_argument("--language", choices=["en", "vi"], default="en")
    if any(argument == "--assumptions" or argument.startswith("--assumptions=") for argument in argv):
        parser.error("--assumptions was removed. Put explicit scenario conditions in --question or answer the clarification; no assumptions JSON is accepted.")
    args = parser.parse_args(argv)
    try:
        if args.command == "rag-bootstrap":
            from ..backends.bootstrap import bootstrap
            bootstrap(Path(args.root), args.include_e5)
            return 0
        if args.command == "rag-ui":
            return subprocess.call([sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "denso_a3/ui/app.py"),
                "--server.address", "127.0.0.1", "--server.port", str(args.port), "--browser.gatherUsageStats", "false"], cwd=PROJECT_ROOT)
        if args.command == "rag-metadata":
            from .index_builder import update_metadata
            result = update_metadata(args.index, json.loads(Path(args.registry).read_text(encoding="utf-8-sig")), args.output)
        else:
            config = load_config(args.config)
            if getattr(args, "mode", None):
                config = replace(config, orchestration_mode=args.mode)
                config.validate()
            if args.command == "rag-suggest-metadata":
                from .index_builder import suggest_index_metadata
                if args.rules_only:
                    config = replace(config, metadata_use_llm=False)
                result = suggest_index_metadata(args.index, args.output, config)
            elif args.command == "rag-upgrade":
                from .index_builder import upgrade_hierarchy
                result = upgrade_hierarchy(args.index,args.output,config)
            elif args.command == "rag-evaluate-multidoc":
                from ..evaluation.multidoc_evaluator import evaluate_multidoc
                result = evaluate_multidoc(args.index,args.qa,args.output,config)
            elif args.command == "rag-build":
                from .index_builder import build_index
                result = build_index(args.run, args.output, config, args.registry)
            elif args.command == "rag-evaluate":
                from ..evaluation.rag_evaluator import evaluate_rag
                result = evaluate_rag(args.index, args.qa, args.output, config, args.strategy, args.limit,
                                      args.retrieval_only, args.scope, args.language)
            else:
                from .rag_runner import ask
                filters = json.loads(Path(args.filters).read_text(encoding="utf-8-sig")) if args.filters else {}
                if args.source:
                    existing = filters.get("source_relative")
                    allowed = existing if isinstance(existing,list) else [existing]
                    filters["source_relative"] = [source for source in args.source if source in allowed] if existing is not None else args.source
                result = ask(index=args.index, question=args.question, config=config,
                    strategy=args.strategy, filters=filters, conversation_id=args.conversation,
                    memory_path=args.memory)
                if args.output:
                    write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return int(args.command in {"rag-evaluate","rag-evaluate-multidoc"} and bool(result.get("errors")))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
