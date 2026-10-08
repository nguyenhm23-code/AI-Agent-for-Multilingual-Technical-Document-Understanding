"""Pinned Docling C backend: image-only, explicit GPU, one conversion/page."""

import os
import time
import shutil
from pathlib import Path
from importlib.metadata import version

from ..contracts import PipelineConfig
from ..io import sha256_file, fingerprint
from .normalization import normalize_payload


def model_manifest(model_dir: Path) -> list[dict]:
    result = []
    # Snapshot copies and RapidOCR checkpoints are stable cache evidence.
    for path in sorted(model_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".pt", ".pth", ".onnx", ".safetensors", ".bin", ".json", ".txt"}:
            result.append({"path": path.relative_to(model_dir).as_posix(), "sha256": sha256_file(path), "bytes": path.stat().st_size})
    return result


class DoclingBackend:
    def __init__(self, config: PipelineConfig, allow_download: bool = False):
        self.config = config
        root = Path(config.model_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(root / "huggingface")
        os.environ["TORCH_HOME"] = str(root / "torch")
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        os.environ["HF_HUB_DISABLE_XET"] = "1"
        os.environ["HF_HUB_OFFLINE"] = "0" if allow_download else "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "0" if allow_download else "1"
        import torch
        if config.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable; CPU fallback requires explicit configuration")
        torch.set_num_threads(config.threads)
        from docling.document_converter import DocumentConverter, ImageFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions, OcrMode, TableFormerMode

        bootstrap_started = time.perf_counter()
        artifacts = root / "artifacts"
        from huggingface_hub import snapshot_download
        specs = [("docling-project/docling-layout-heron", "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8", ["*.json", "*.safetensors"]),
                 ("docling-project/docling-models", "v2.3.0", ["model_artifacts/tableformer/accurate/*"])]
        for repo, revision, patterns in specs:
            destination = artifacts / repo.replace("/", "--")
            if not destination.exists() or allow_download:
                snapshot = Path(snapshot_download(repo, revision=revision, allow_patterns=patterns,
                                                  max_workers=1, local_files_only=not allow_download))
                # Docling's explicit artifacts_path avoids offline completeness
                # checks on irrelevant repository files (e.g. README/fast model).
                for path in snapshot.rglob("*"):
                    if path.is_file() and path.suffix in {".json", ".safetensors", ".pt", ".pth"}:
                        target = destination / path.relative_to(snapshot)
                        if not target.exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(path, target)
        rapid_root = artifacts / "RapidOcr"
        rapid_root.mkdir(parents=True, exist_ok=True)
        # Resolve the correct recognizer AND dictionary for the selected language.
        # These APIs belong to the pinned Docling 2.130.0 adapter.
        from docling.models.stages.ocr.rapid_ocr_model import (
            RapidOcrModel, _resolve_rapidocr, _rapidocr_artifacts, _backend_to_engine_type)
        resolved = _resolve_rapidocr(config.language, config.ocr_backend)
        ocr_artifacts = _rapidocr_artifacts(rapid_root, _backend_to_engine_type(config.ocr_backend),
                                          resolved.ppocr_version, resolved.rapidocr_code)
        required = [p.relative_to(rapid_root).as_posix() for a in ocr_artifacts.values() for p in a.files]
        dictionary = ocr_artifacts["rec"].dict_path
        from rapidocr.ch_ppocr_rec.main import DEFAULT_MODEL_PATH
        # Reuse already fetched library-managed OCR assets when bootstrapping.
        for name in required:
            target = rapid_root/name
            for old_root in (root/"rapidocr", DEFAULT_MODEL_PATH):
                if not target.exists() and (old_root/name).is_file():
                    shutil.copyfile(old_root/name, target)
        if allow_download:
            RapidOcrModel.download_models(backend=config.ocr_backend, lang=config.language, local_dir=rapid_root)
        missing = [name for name in required if not (rapid_root/name).is_file()]
        if missing:
            raise RuntimeError(f"Offline OCR assets missing: {missing}; run doctor --download-models")
        bootstrap_ms = round((time.perf_counter()-bootstrap_started)*1000,2)
        rapid_params = {"Global.model_root_dir": rapid_root}
        if dictionary is not None:
            rapid_params["Rec.rec_keys_path"] = str(dictionary)

        options = PdfPipelineOptions(
            accelerator_options=AcceleratorOptions(device=config.device, num_threads=config.threads),
            artifacts_path=artifacts,
            do_ocr=True, do_table_structure=True, generate_parsed_pages=True,
            document_timeout=config.timeout_seconds,
            ocr_options=RapidOcrOptions(backend=config.ocr_backend, lang=[config.language],
                                       mode=OcrMode.FULL_PAGE, scale=1.0,
                                       rapidocr_params=rapid_params),
            ocr_batch_size=1, layout_batch_size=1, table_batch_size=1,
        )
        options.table_structure_options.mode = TableFormerMode(config.table_mode)
        self.converter = DocumentConverter(allowed_formats=[InputFormat.IMAGE],
                                          format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=options)})
        started = time.perf_counter()
        self.converter.initialize_pipeline(InputFormat.IMAGE)
        self.pipeline = next(iter(self.converter.initialized_pipelines.values()))
        reader = self.pipeline.ocr_model.reader
        devices = {}
        for component in ("text_det", "text_cls", "text_rec"):
            session = getattr(getattr(reader, component), "session")
            devices[component] = str(getattr(session, "device", "unknown"))
        if config.device == "cuda" and config.ocr_backend == "torch" and any(not x.startswith("cuda") for x in devices.values()):
            raise RuntimeError(f"OCR GPU placement not confirmed: {devices}; no CPU fallback")
        layout_engine = self.pipeline.layout_model.engine
        stage_devices = {"layout": str(next(layout_engine._model.parameters()).device),
                         "table": str(next(self.pipeline.table_model.tf_predictor._model.parameters()).device)}
        if config.device == "cuda" and any(not x.startswith("cuda") for x in stage_devices.values()):
            raise RuntimeError(f"Layout/table GPU placement not confirmed: {stage_devices}; no CPU fallback")
        manifests = model_manifest(artifacts)
        self.runtime = {"versions": {name: version(name) for name in ("docling-slim", "docling-core", "docling-ibm-models", "rapidocr", "torch", "PyMuPDF")},
                        "device_requested": config.device, "ocr_devices": devices,
                        "stage_devices": stage_devices,
                        "cuda_name": torch.cuda.get_device_name(0) if config.device == "cuda" else None,
                        "cuda_runtime": torch.version.cuda, "model_initialization_ms": round((time.perf_counter()-started)*1000, 2),
                        "bootstrap_ms": bootstrap_ms, "initialization_includes_download":False,
                        "models": manifests, "model_fingerprint": fingerprint(manifests),
                        "pipeline_options": options.model_dump(mode="json")}

    def convert(self, image_path: str) -> dict:
        import torch
        import psutil
        if self.config.device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        result = self.converter.convert(Path(image_path), raises_on_error=False)
        elapsed = round((time.perf_counter()-started)*1000, 2)
        if not result.pages:
            raise RuntimeError(f"Docling returned no pages: {result.status}")
        page = result.pages[0]
        raw = result.document.export_to_dict()
        cells = [{"text": c.text, "confidence": c.confidence, "from_ocr": c.from_ocr,
                  "bbox": c.to_bounding_box().model_dump(mode="json")} for c in page.cells]
        order = [item.self_ref for item, _ in result.document.iterate_items()]
        status = result.status.value
        engine_errors = [str(x) for x in result.errors]
        bt2, bt3 = normalize_payload(raw, cells, (page.size.width, page.size.height), order, status, engine_errors)
        return {"bt2": bt2, "bt3": bt3, "raw_document": raw, "raw_ocr_cells": cells,
                "timing": {"joint_bt2_bt3_ms": elapsed, "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated() if self.config.device == "cuda" else None,
                           "worker_rss_bytes_after_page": psutil.Process().memory_info().rss},
                "conversion_status": status}
