"""Ingest documents, preserving aliases and isolating file/page failures."""

from collections import defaultdict
from pathlib import Path
import json
import os
import tempfile
import time

import pymupdf
from PIL import Image, ImageOps

from ..contracts import PageInput, PipelineConfig
from ..io import fingerprint, sha256_file

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
SIDECARS = {".ocr", ".json"}


def save_png_atomic(target: Path, save):
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(suffix=".png", dir=target.parent)
    os.close(descriptor)
    try:
        save(name)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def exif_affine(orientation: int, width: int, height: int) -> list[float]:
    return {1: [1, 0, 0, 1, 0, 0], 2: [-1, 0, 0, 1, width, 0],
            3: [-1, 0, 0, -1, width, height], 4: [1, 0, 0, -1, 0, height],
            5: [0, 1, 1, 0, 0, 0], 6: [0, 1, -1, 0, height, 0],
            7: [0, -1, -1, 0, height, width], 8: [0, -1, 1, 0, 0, width]
            }.get(orientation, [1, 0, 0, 1, 0, 0])


def inspect_pdf_page(page, document_metadata: dict) -> dict:
    traces = page.get_texttrace()
    visible = sum(len(s.get("chars", ())) for s in traces if s.get("type") != 3)
    hidden = sum(len(s.get("chars", ())) for s in traces if s.get("type") == 3)
    fonts = sorted({s.get("font", "") for s in traces})
    # Approximation, not the union of overlapping image rectangles.
    area = page.rect.width * page.rect.height
    image_area = sum(max(0, pymupdf.Rect(i["bbox"]).get_area())
                     for i in page.get_image_info())
    coverage = min(1.0, image_area / area) if area else 0.0
    glyphless = any("GlyphLessFont" in name for name in fonts)
    creator_ocr = "ocr" in (document_metadata.get("creator") or "").lower()
    if hidden > 0 and coverage > 0.8 and (glyphless or creator_ocr):
        kind, origin, reason = "scan_with_ocr", "ocr", "hidden_text_and_full_page_image"
    elif visible > 0 and coverage > 0.8:
        kind, origin, reason = "mixed", "unknown", "visible_text_with_large_image"
    elif visible > 0 and hidden == 0:
        kind, origin, reason = "native_text", "native", "visible_text_without_ocr_signals"
    elif visible + hidden == 0 and coverage > 0:
        kind, origin, reason = "scan", "none", "image_without_text_layer"
    elif visible + hidden == 0 and not page.get_drawings():
        kind, origin, reason = "blank", "none", "no_text_images_or_vector_drawings"
    else:
        kind, origin, reason = "unknown", "unknown", "ambiguous_page_content"
    return {"content_type": kind, "text_layer_origin": origin, "inspection_reason": reason,
            "visible_text_chars": visible, "hidden_text_chars": hidden,
            "image_coverage_estimate": coverage, "fonts": fonts,
            "classification_is_heuristic": True}


def _canonical_image(source: Path, target: Path) -> tuple[int, int, dict]:
    with Image.open(source) as raw:
        # Reject unsupported multi-frame input explicitly instead of dropping pages.
        if getattr(raw, "n_frames", 1) != 1:
            raise ValueError("Multi-frame images require explicit frame ingestion (not supported yet)")
        orientation = int(raw.getexif().get(274, 1))
        original = list(raw.size)
        image = ImageOps.exif_transpose(raw).convert("RGB")
        if not target.exists():
            save_png_atomic(target, lambda path: image.save(path, format="PNG"))
        return image.width, image.height, {"source_image_dimensions": original,
                                           "exif_orientation": orientation,
                                           "source_to_canonical": exif_affine(orientation, *original)}


def ingest(config: PipelineConfig) -> tuple[list[dict], list[PageInput], dict]:
    root = Path(config.input_dir).resolve()
    excluded = [Path(config.cache_dir).resolve(), Path(config.model_dir).resolve()]
    documents, pages, ignored = [], [], []
    # QA supplies identities only. Its text/answers are never attached to PageInput.
    identities = {}
    qa = root / "dataset_test.json"
    
    if qa.is_file():
        for record in json.loads(qa.read_text(encoding="utf-8")):
            identities[record["image_path"].replace("\\", "/")] = {
                "record_id": str(record["id"]), "external_document_id": record.get("docId"),
                "ucsf_document_id": record.get("ucsf_document_id")}
    
    for source in sorted(root.rglob("*")):
        if not source.is_file() or any(source.resolve().is_relative_to(p) for p in excluded):
            continue
        relative = source.relative_to(root).as_posix()
        if any(part in {".cache", "results", "ground_truth", "__pycache__"} for part in source.relative_to(root).parts):
            continue
        suffix = source.suffix.lower()
        if suffix in SIDECARS:
            ignored.append({"path": relative, "reason": "evaluation_sidecar"})
            continue
        document_id = "doc_" + fingerprint(relative)[:16]
        document = {"document_id": document_id, "source_path": str(source.resolve()),
                    "source_relative": relative, "format": None, "status": "failed",
                    "page_count": 0, "error": None, "metadata": {}}
        documents.append(document)
        started = time.perf_counter()
        try:
            document["source_sha256"] = sha256_file(source)
            document["size_bytes"] = source.stat().st_size
            document["filesystem_mtime_ns"] = source.stat().st_mtime_ns
            with source.open("rb") as stream:
                signature = stream.read(1024)
            pdf = b"%PDF-" in signature
            if pdf:
                document["format"] = "pdf"
                with pymupdf.open(source) as doc:
                    if doc.needs_pass:
                        raise ValueError("password_required")
                    document["metadata"] = dict(doc.metadata)
                    document["page_count"] = len(doc)
                    for index in range(len(doc)):
                        number = index + 1
                        item = PageInput(f"{document_id}:p{number:04}", document_id,
                                         str(source.resolve()), relative, document["source_sha256"],
                                         number, "pdf_scan")
                        pages.append(item)
                        try:
                            page = doc[index]
                            item.metadata = inspect_pdf_page(page, document["metadata"])
                            item.metadata.update({"route_policy": "force_fresh_ocr",
                                "route_reason": "architecture_C_image_only_no_existing_layer",
                                "page_size_points": [page.rect.width, page.rect.height],
                                "rotation": page.rotation, "cropbox": list(page.cropbox),
                                "render_dpi": config.dpi, "bbox_space": "canonical_image_pixels"})
                            target = Path(config.cache_dir) / "renders" / (
                                fingerprint({"source": item.source_sha256, "page": number,
                                             "dpi": config.dpi, "renderer": pymupdf.VersionBind,
                                             "version": "1"}) + ".png")
                            if not target.exists():
                                pix = page.get_pixmap(dpi=config.dpi, alpha=False)
                                save_png_atomic(target, pix.save)
                            with Image.open(target) as canonical:
                                item.width, item.height = canonical.size
                            origin = (page.rect * pymupdf.Matrix(config.dpi/72, config.dpi/72)).irect
                            matrix = page.rotation_matrix * pymupdf.Matrix(config.dpi / 72, config.dpi / 72)
                            matrix.e -= origin.x0
                            matrix.f -= origin.y0
                            item.metadata["pdf_points_to_canonical"] = list(matrix)
                            item.metadata["canonical_to_pdf_points"] = list(~matrix)
                            item.image_path, item.image_sha256 = str(target.resolve()), sha256_file(target)
                        except Exception as exc:
                            item.status, item.error = "failed", f"render_or_inspection: {type(exc).__name__}: {exc}"
            elif suffix in IMAGE_SUFFIXES or signature.startswith((b"\x89PNG", b"\xff\xd8", b"II*\x00", b"MM\x00*", b"BM")):
                document["format"], document["page_count"] = "image", 1
                item = PageInput(f"{document_id}:p0001", document_id, str(source.resolve()),
                                 relative, document["source_sha256"], None, "image")
                pages.append(item)
                try:
                    target = Path(config.cache_dir) / "renders" / (item.source_sha256 + ".png")
                    item.width, item.height, geometry = _canonical_image(source, target)
                    item.image_path, item.image_sha256 = str(target.resolve()), sha256_file(target)
                    item.metadata = {"content_type": "image", "route_policy": "force_fresh_ocr",
                                     "bbox_space": "canonical_image_pixels", **geometry,
                                     **identities.get(relative, {})}
                except Exception as exc:
                    item.status, item.error = "failed", f"image_decode: {type(exc).__name__}: {exc}"
            else:
                raise ValueError(f"unsupported_format: {suffix or 'no extension'}")
            own = [p for p in pages if p.document_id == document_id]
            document["status"] = "partial" if any(p.status == "failed" for p in own) else "ok"
        except Exception as exc:
            document["error"] = f"{type(exc).__name__}: {exc}"
        document["ingestion_ms"] = round((time.perf_counter() - started) * 1000, 2)
    groups = defaultdict(list)
    for page in pages:
        if page.image_sha256:
            groups[page.image_sha256].append(page.page_id)
    for page in pages:
        page.metadata["alias_page_ids"] = groups.get(page.image_sha256, [page.page_id])
    summary = {"documents": len(documents), "logical_pages": len(pages),
               "unique_canonical_images": len(groups), "duplicate_page_records": sum(len(v)-1 for v in groups.values()),
               "ignored_sidecars": ignored, "alias_groups": dict(groups)}
    return documents, pages, summary
