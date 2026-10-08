"""Reproduce source-only agent annotations (never consume predictions/.ocr)."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HASH = "ee41bd62591fe5390c12fb4625cce15d03997dd1bccde34f34d80635e70a84d7"


def box(x0, y0, x1, y1):
    # Manually inspected Poppler source renders at 144 DPI, 1190 x 1684 pixels.
    return [round(x0/1190, 6), round(y0/1684, 6), round(x1/1190, 6), round(y1/1684, 6)]


def table(identifier, bounds, rows, columns):
    return {"annotation_id": identifier, "bbox": box(*bounds), "num_rows": rows,
            "num_columns": columns, "cells": [], "cell_annotation_exhaustive": False}


def main():
    source = ROOT / "evaluation/data/nhwc0428.pdf"
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_HASH:
        raise ValueError("Reviewed source changed; pilot must be re-annotated")
    definitions = {
        2: [table("p2_revision", (113,346,1050,985), 9,4), table("p2_distribution", (113,1129,1050,1500),7,4)],
        8: [table("p8_milestones", (102,166,1163,1532),21,4)],
        11:[table("p11_schedule",(102,166,1117,452),6,3),table("p11_cost",(102,645,1117,1023),6,4)],
        13:[table("p13_role",(102,166,1081,405),5,3)],
        15:[table("p15_controls",(102,247,1077,662),8,4),table("p15_risk",(102,806,1064,1495),6,5)]}
    contents = [
        ["Control","Frequency","From","To"],
        ["Workstream plans / PID’s submitted","","Workstreams","PM"],
        ["Project PID approved and submitted","","PM","Programme office"],
        ["Workstream review workshops","Twice a year","",""],
        ["Project review meetings","Twice a year","",""],
        ["Workstream monthly highlight reports","Monthly","",""],
        ["Project monthly highlight reports","Monthly","",""],
        ["Significant changes in plans or scope to be approved by the Growth Programme Board","When necessary when changes occur","",""]]
    xs, ys = [102,510,723,893,1077], [247,295,342,390,438,485,532,580,662]
    controls = definitions[15][0]
    controls["cell_annotation_exhaustive"] = True
    for r, row in enumerate(contents):
        for c, text in enumerate(row):
            controls["cells"].append({"row_index":r, "column_index":c, "rowspan":1, "colspan":1,
                                      "text_raw":text, "bbox":box(xs[c],ys[r],xs[c+1],ys[r+1]),
                                      "bbox_precision":"full_cell", "content_status":"visible" if text else "verified_empty"})
    pages=[]
    for number, tables in definitions.items():
        layout = [{"annotation_id": t["annotation_id"], "type":"table", "bbox":t["bbox"]} for t in tables]
        if number == 13:
            layout.append({"annotation_id":"p13_org_chart","type":"figure","bbox":box(114,573,1004,851)})
        pages.append({"source_relative":"nhwc0428.pdf", "source_sha256":SOURCE_HASH, "page_number":number,
                      "bbox_space":"normalized_page_image", "table_detection_exhaustive":True,
                      "layout_detection_exhaustive":False, "tables":tables, "layout":layout})
    result={"schema_version":"1.0", "annotation_origin":"agent_visual_source_review",
            "review_status":"needs_user_review", "reviewer":"Codex agent", "created_at":"2026-09-26",
            "source_render":{"renderer":"Poppler pdftoppm", "dpi":144, "dimensions":[1190,1684]},
            "scope":"Exhaustive table detection on five pilot pages; partial layout (tables + one figure); full cell topology/text only for p15_controls.",
            "notes":["Annotations were transcribed from source images before viewing model predictions.",
                     "Redacted names were not reconstructed; table dimensions still include their cells.",
                     "Pilot is not a held-out test set. Counts/geometry and Unicode punctuation need human confirmation.",
                     "Full-cell ground-truth boxes are not comparable to OCR text-region cell boxes for cell IoU."],
            "pages":pages}
    (Path(__file__).with_name("pilot.json")).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
