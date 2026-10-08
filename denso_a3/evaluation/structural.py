"""Small pilot metrics; never invent table truth from OCR plain text."""

import re
from .text import compare, aggregate


def iou(a: list[float], b: list[float]) -> float:
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2]-a[0]) * (a[3]-a[1]) + (b[2]-b[0]) * (b[3]-b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def match_boxes(truth: list[dict], predicted: list[dict], threshold: float = 0.5) -> list[tuple[int, int, float]]:
    # Maximum-cardinality bipartite matching, so greedy IoU does not suppress a valid match.
    adjacency = {i: sorted([(j, iou(t["bbox"], p["bbox"])) for j, p in enumerate(predicted)
                            if iou(t["bbox"], p["bbox"]) >= threshold], key=lambda x: -x[1])
                 for i, t in enumerate(truth)}
    owners = {}

    def assign(index, seen):
        for other, score in adjacency[index]:
            if other in seen:
                continue
            seen.add(other)
            if other not in owners or assign(owners[other][0], seen):
                owners[other] = (index, score)
                return True
        return False

    for index in range(len(truth)):
        assign(index, set())
    return sorted((index, other, score) for other, (index, score) in owners.items())


def evaluate_tables(pilot: dict, pages: list[dict], layouts: list[dict]) -> dict:
    results, gt_count, pred_count, matched_count = [], 0, 0, 0
    text_correct = text_count = 0
    nonempty_correct = nonempty_count = blank_correct = blank_count = 0
    cell_comparisons = []
    topology_results, all_exhaustive = [], True
    for annotation in pilot.get("pages", []):
        matches = [p for p in pages if p["source_relative"] == annotation["source_relative"]
                   and p["page_number"] == annotation["page_number"]]
        if not matches:
            continue
        page = matches[0]
        if page["source_sha256"] != annotation["source_sha256"]:
            results.append({"page_id": page["page_id"], "status": "source_hash_mismatch"})
            continue
        layout = next((x for x in layouts if x["page_id"] == page["page_id"]), {})
        truth, predicted = annotation.get("tables", []), layout.get("tables", [])
        all_exhaustive = all_exhaustive and bool(annotation.get("table_detection_exhaustive"))
        pairings = match_boxes(truth, predicted)
        gt_count += len(truth)
        if annotation.get("table_detection_exhaustive"):
            pred_count += len(predicted)
        else:
            pred_count += len(pairings)  # Only matched predictions are in scope for partial annotation.
        matched_count += len(pairings)
        matching = {a: (b, score) for a, b, score in pairings}
        table_results = []
        for index, expected in enumerate(truth):
            found = matching.get(index)
            actual = predicted[found[0]] if found else None
            assessed = []
            for cell in expected.get("cells", []):
                if cell.get("content_status") in {"redacted", "unreadable"}:
                    continue
                text_count += 1
                actual_cell = next((c for c in (actual or {}).get("cells", [])
                                    if c["row_index"] == cell["row_index"] and c["column_index"] == cell["column_index"]), None)
                expected_text = re.sub(r"\s+", " ", cell["text_raw"]).strip()
                actual_text = re.sub(r"\s+", " ", (actual_cell or {}).get("text_raw", "")).strip()
                exact = actual_cell is not None and expected_text == actual_text
                text_correct += int(exact)
                if expected_text:
                    nonempty_count += 1
                    nonempty_correct += int(exact)
                    cell_comparisons.append(compare(expected_text,actual_text,"whitespace"))
                else:
                    blank_count += 1
                    blank_correct += int(exact)
                assessed.append({"row": cell["row_index"], "column": cell["column_index"],
                                 "reference": expected_text, "prediction": actual_text,
                                 "exact": exact, "missing_cell": actual_cell is None,
                                 "reference_content_status":cell.get("content_status","visible")})
            structure = None
            if "num_rows" in expected:
                structure = bool(actual and actual["num_rows"] == expected["num_rows"]
                                 and actual["num_columns"] == expected["num_columns"])
            topology = None
            if expected.get("cell_annotation_exhaustive"):
                slots = lambda cells: {(c["row_index"], c["column_index"], c.get("rowspan",1), c.get("colspan",1)) for c in cells}
                topology = bool(actual and structure and slots(expected["cells"]) == slots(actual["cells"]))
                topology_results.append(topology)
            table_results.append({"annotation_id": expected["annotation_id"],
                                  "predicted_table_id": actual["table_id"] if actual else None,
                                  "iou": found[1] if found else None, "row_column_count_exact": structure,
                                  "complete_grid_topology_exact": topology,
                                  "cells": assessed})
        results.append({"page_id": page["page_id"], "status": "evaluated", "tables": table_results,
                        "unmatched_predicted_tables": len(predicted)-len(pairings)})
    precision = matched_count / pred_count if pred_count and all_exhaustive else None
    recall = matched_count / gt_count if gt_count else None
    return {"annotation_origin": pilot.get("annotation_origin"), "review_status": pilot.get("review_status"),
            "iou_threshold": 0.5, "ground_truth_tables": gt_count, "predicted_tables_in_scope": pred_count,
            "matched_tables": matched_count, "precision": precision, "recall": recall,
            "f1": 2*precision*recall/(precision+recall) if precision and recall else 0.0 if gt_count and all_exhaustive else None,
            "complete_grid_topology_exact": sum(topology_results)/len(topology_results) if topology_results else None,
            "complete_grid_reference_count": len(topology_results),
            "visible_cell_exact_match": text_correct / text_count if text_count else None,
            "visible_cell_correct": text_correct, "visible_cell_reference_count": text_count,
            "nonempty_cell_exact_match":nonempty_correct/nonempty_count if nonempty_count else None,
            "nonempty_cell_correct":nonempty_correct,"nonempty_cell_reference_count":nonempty_count,
            "verified_empty_cell_exact_match":blank_correct/blank_count if blank_count else None,
            "verified_empty_cell_correct":blank_correct,"verified_empty_cell_reference_count":blank_count,
            "nonempty_cell_text":aggregate(cell_comparisons),
            "pages": results, "grits": {"status": "not_evaluated", "reason": "pilot_has_no_exhaustive_cell_topology_for_all_tables"}}


def evaluate_layout(pilot: dict, pages: list[dict], layouts: list[dict]) -> dict:
    results, reference_count, matched_count = [], 0, 0
    for annotation in pilot.get("pages", []):
        page = next((p for p in pages if p["source_relative"] == annotation["source_relative"]
                     and p["page_number"] == annotation["page_number"]), None)
        if page is None:
            continue
        if page["source_sha256"] != annotation["source_sha256"]:
            results.append({"page_id":page["page_id"],"status":"source_hash_mismatch"})
            continue
        predicted = next((x for x in layouts if x["page_id"] == page["page_id"]), {}).get("blocks", [])
        truth = annotation.get("layout", [])
        pairs=[]
        for kind in sorted({x["type"] for x in truth}):
            a, b = [x for x in truth if x["type"] == kind], [x for x in predicted if x["type"] == kind]
            matches=match_boxes(a,b)
            pairs.extend({"annotation_id":a[i]["annotation_id"],"block_id":b[j]["block_id"],"type":kind,"iou":s} for i,j,s in matches)
        reference_count += len(truth)
        matched_count += len(pairs)
        results.append({"page_id":page["page_id"],"status":"evaluated","reference_blocks":len(truth),"matches":pairs})
    return {"annotation_origin":pilot.get("annotation_origin"),"review_status":pilot.get("review_status"),
            "status":"evaluated_partial" if reference_count else "not_evaluated", "iou_threshold":.5,
            "reference_blocks":reference_count,"matched_blocks":matched_count,
            "recall_on_annotated_blocks":matched_count/reference_count if reference_count else None,
            "precision":None,"map":None,"reason":"partial_labels_cannot_estimate_false_positives_or_mAP","pages":results}
