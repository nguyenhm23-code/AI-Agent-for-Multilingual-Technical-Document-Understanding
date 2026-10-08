"""Normalize Docling's native geometry without fabricating word boxes."""

from ..contracts import checked_box
from ..bt2.quality import quality_signals


def bbox(box: dict, width: float, height: float) -> list[float]:
    if str(box.get("coord_origin", "TOPLEFT")).upper().endswith("BOTTOMLEFT"):
        coords = [box["l"], height-box["t"], box["r"], height-box["b"]]
    else:
        coords = [box["l"], box["t"], box["r"], box["b"]]
    return checked_box(coords, width, height)


def intersecting_spans(box: list[float] | None, spans: list[dict]) -> list[str]:
    if box is None:
        return []
    # Center assignment is evidence association, not a fabricated cell transcription.
    return [s["span_id"] for s in spans
            if box[0] <= (s["bbox"][0]+s["bbox"][2])/2 <= box[2]
            and box[1] <= (s["bbox"][1]+s["bbox"][3])/2 <= box[3]]


def normalize_payload(raw: dict, ocr_cells: list[dict], frame: tuple[float, float], order: list[str],
                      conversion_status: str, engine_errors: list[str]) -> tuple[dict, dict]:
    width, height = frame
    spans, geometry_errors = [], []
    for index, cell in enumerate(ocr_cells):
        try:
            box = bbox(cell["bbox"], width, height)
        except ValueError as exc:
            geometry_errors.append(f"ocr_span_{index}: {exc}")
            continue
        spans.append({"span_id": f"s{index}", "text": cell["text"], "bbox": box,
                      "bbox_space": "normalized_page_image", "source_bbox": cell["bbox"],
                      "source_frame": [width, height], "score": cell.get("confidence"),
                      "bbox_precision": "line", "from_ocr": cell.get("from_ocr", True)})
    text = "\n".join(c["text"] for c in ocr_cells)
    bt2 = {"status": "ok" if conversion_status == "success" and not geometry_errors else "partial" if text else "failed",
           "method": "docling_rapidocr", "text": text, "spans": spans,
           "confidence": {"score": None, "kind": "no_calibrated_page_accuracy", "reason": "line scores are kept on spans"},
           "text_order": "raw_ocr_cells", "quality": quality_signals(text, spans),
           "errors": engine_errors + geometry_errors}
    nodes = {item["self_ref"]: item for kind in ("texts", "tables", "pictures") for item in raw.get(kind, [])}
    sequence = list(dict.fromkeys(order + list(nodes)))
    blocks, tables, errors = [], [], list(geometry_errors)
    label_map = {"page_header": "header", "page_footer": "footer", "picture": "figure",
                 "list_item": "list", "text": "paragraph"}
    for reference in sequence:
        item = nodes.get(reference)
        if not item:
            continue
        provenance = next((x for x in item.get("prov", []) if x.get("page_no") == 1), None)
        if provenance is None:
            errors.append(f"{reference}: missing_page_provenance")
            continue
        try:
            box = bbox(provenance["bbox"], width, height)
        except ValueError as exc:
            errors.append(f"{reference}: {exc}")
            continue
        label = item.get("label", "unknown")
        block = {"block_id": f"b{len(blocks)}", "type": label_map.get(label, label), "engine_label": label,
                 "bbox": box, "bbox_space": "normalized_page_image", "text_raw": item.get("text", ""),
                     "span_ids": intersecting_spans(box, spans), "reading_order": len(blocks),
                     "order_origin": "docling_assembled" if reference in order else "unlisted_item_appended",
                 "backend_reference": reference, "source_bbox": provenance["bbox"]}
        blocks.append(block)
        if reference.startswith("#/tables/"):
            data = item.get("data", {})
            table = {"table_id": f"t{len(tables)}", "block_id": block["block_id"], "bbox": box,
                     "bbox_space": "normalized_page_image", "num_rows": data.get("num_rows", 0),
                     "num_columns": data.get("num_cols", 0), "cells": [], "method": "docling_tableformer",
                     "backend_reference": reference, "errors": []}
            occupied = set()
            for cell in data.get("table_cells", []):
                row, col = cell["start_row_offset_idx"], cell["start_col_offset_idx"]
                rs, cs = cell.get("row_span", 1), cell.get("col_span", 1)
                slots = {(r,c) for r in range(row, row+rs) for c in range(col, col+cs)}
                if row < 0 or col < 0 or rs < 1 or cs < 1 or row+rs > table["num_rows"] or col+cs > table["num_columns"] or slots & occupied:
                    table["errors"].append(f"invalid_or_overlapping_grid: {row},{col},{rs},{cs}")
                    continue
                occupied.update(slots)
                cell_box = None
                if cell.get("bbox"):
                    try:
                        cell_box = bbox(cell["bbox"], width, height)
                    except ValueError as exc:
                        table["errors"].append(f"cell_{row}_{col}: {exc}")
                table["cells"].append({"row_index": row, "column_index": col,
                    "rowspan": rs, "colspan": cs, "text_raw": cell.get("text", ""),
                    "bbox": cell_box, "bbox_space": "normalized_page_image",
                    "span_ids": intersecting_spans(cell_box, spans),
                    "column_header": cell.get("column_header", False), "row_header": cell.get("row_header", False),
                    "content_status": "observed" if cell.get("text") else "unknown_not_verified_empty"})
            table["grid_coverage"] = len(occupied)/(table["num_rows"]*table["num_columns"]) if table["num_rows"]*table["num_columns"] else None
            table["unknown_grid_positions"] = [{"row_index":r,"column_index":c,"content_status":"unknown"}
                                                for r in range(table["num_rows"]) for c in range(table["num_columns"])
                                                if (r,c) not in occupied]
            table["quality_flags"] = ["incomplete_grid"] if table["unknown_grid_positions"] else []
            table["status"] = "partial" if table["errors"] or table["quality_flags"] else "ok"
            tables.append(table)
    bt3 = {"status": "failed" if conversion_status == "failure" else "partial" if conversion_status != "success" or errors or engine_errors or any(t["status"] == "partial" for t in tables) else "ok",
           "method": "docling_layout_tableformer", "blocks": blocks, "tables": tables,
           "errors": engine_errors + errors, "quality": {"review_status": "unreviewed"}}
    return bt2, bt3
