"""Conservative structured numeric conflict gate; never infer version authority."""
import re
from decimal import Decimal
from .planner import fold
from ..bt4.metadata import verified_values

UNITS = {"pa": ("pressure", 1), "kpa": ("pressure", 1000), "mpa": ("pressure", 1000000),
         "mm": ("length", 1), "cm": ("length", 10), "m": ("length", 1000),
         "n": ("force", 1), "v": ("voltage", 1), "a": ("current", 1),
         "rpm": ("speed", 1), "°c": ("temperature", 1), "%": ("percent", 1)}


def facts(text):
    for line in text.splitlines():
        match = re.fullmatch(r"\s*([\w ()/-]{2,80})\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*([a-zA-Z°%]+)\s*", line)
        if match and match[3].lower() in UNITS:
            dimension, factor = UNITS[match[3].lower()]
            yield fold(match[1]).strip(), dimension, Decimal(match[2])*factor, line.strip()


def chunk_facts(chunk):
    found = list(facts(chunk["text"]))
    table = chunk.get("table") or {}
    rows = {}
    for cell in table.get("cells", []):
        if cell.get("rowspan", 1) != 1 or cell.get("colspan", 1) != 1:
            continue  # Do not infer ambiguous merged-cell attribute/value relationships.
        rows.setdefault(cell["row_index"], []).append(cell)
    for cells in rows.values():
        cells.sort(key=lambda c: c["column_index"])
        values = [c.get("text_raw", "").strip() for c in cells]
        if len(values) in {2, 3} and all(values):
            found.extend(facts(values[0] + ": " + " ".join(values[1:])))
    return list(dict.fromkeys(found))


def detect_conflicts(chunks):
    groups, results, seen = {}, [], set()
    for c in chunks:
        meta = verified_values(c.get("metadata", {}))
        family = meta.get("document_family_id")
        entity = meta.get("equipment_id") or meta.get("part_number")
        if not family or not entity:
            continue
        for attribute, dimension, value, raw in chunk_facts(c):
            key = (family, entity, attribute, dimension)
            for old, old_value, old_raw in groups.get(key, []):
                if old["document_id"] != c["document_id"] and old_value != value:
                    pair = (key, *sorted([old["document_id"], c["document_id"]]), *sorted([str(old_value), str(value)]))
                    if pair in seen:
                        continue
                    seen.add(pair)
                    results.append({"family": family, "entity": entity, "attribute": attribute,
                        "values": [old_raw, raw], "chunk_ids": [old["chunk_id"], c["chunk_id"]],
                        "status": "potential_conflict", "reason": "Different numeric values; authority unresolved"})
            groups.setdefault(key, []).append((c, value, raw))
    return results
