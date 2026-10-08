"""Chunk ordered prose and table rows without losing their evidence locations."""

import re
from dataclasses import asdict

from ..contracts import ChunkRecord
from ..io import fingerprint

POLICY_VERSION = "table-aware-1"


def estimate_tokens(text: str) -> int:
    return len(re.findall(r"\w+|[^\w\s]", text, re.UNICODE))


def split_prose(text, count_tokens, budget, overlap):
    sentences = re.split(r"(?<=[.!?。！？])\s+|\n+", text.strip())
    pieces = []
    for sentence in sentences:
        if count_tokens(sentence) <= budget:
            pieces.append(sentence)
        else:
            # Preserve every character; long OCR lines may have no sentence boundaries.
            words = re.findall(r"\S+\s*", sentence)
            part = ""
            for word in words:
                if part and count_tokens(part + word) > budget:
                    pieces.append(part.strip())
                    part = ""
                part += word
            if part:
                pieces.append(part.strip())
    current = []
    for piece in pieces:
        if current and count_tokens("\n".join(current + [piece])) > budget:
            yield "\n".join(current)
            tail = []
            for previous in reversed(current):
                if count_tokens("\n".join([previous] + tail)) > overlap:
                    break
                tail.insert(0, previous)
            current = tail
            if count_tokens("\n".join(current + [piece])) > budget:
                current = []
        if piece:
            current.append(piece)
    if current:
        yield "\n".join(current)


def table_rows(table):
    rows = []
    for r in range(table["num_rows"]):
        cells = []
        for c in table["cells"]:
            if c["row_index"] <= r < c["row_index"] + c["rowspan"]:
                label = f"col {c['column_index'] + 1}"
                if c.get("column_header"):
                    label += " HEADER"
                if c["rowspan"] > 1 or c["colspan"] > 1:
                    label += f" (span {c['rowspan']}x{c['colspan']})"
                cells.append(f"{label}: {c['text_raw'] or '[UNKNOWN: empty not verified]'}")
        unknown = [str(c["column_index"] + 1) for c in table.get("unknown_grid_positions", [])
                   if c["row_index"] == r]
        if unknown:
            cells.append("UNKNOWN columns: " + ", ".join(unknown))
        rows.append(f"Row {r + 1}: " + " | ".join(cells))
    return rows


def build_chunks(run: dict, registry: dict, budget=384, overlap=48,
                 count_tokens=estimate_tokens, policy="table-aware") -> tuple[list[dict], dict]:
    if budget < 64 or overlap < 0 or overlap >= budget:
        raise ValueError("Require chunk budget >=64 and 0 <= overlap < budget")
    if policy not in {"table-aware", "flat"}:
        raise ValueError("Unknown chunk policy")
    chunks, parents = [], {}
    for page in run["pages"]:
        page_id = page["page_id"]
        ocr, layout = run["bt2_by_page"][page_id], run["bt3_by_page"][page_id]
        if page["status"] == "failed" or ocr["status"] == "failed":
            continue
        meta = registry["documents"].get(page["document_id"], {}).get("fields", {})
        page_flags = ["extraction_unreviewed"]
        if ocr["status"] == "partial" or layout["status"] == "partial":
            page_flags.append("partial_extraction")

        def add(text, kind, heading="", blocks=None, spans=None, box=None, table=None,
                parent_id=None, flags=None):
            if not text.strip():
                return
            content_key = fingerprint({"image": page["image_sha256"], "text": text, "kind": kind,
                                       "bbox": box, "policy": POLICY_VERSION, "mode": policy})
            chunk_id = fingerprint({"content": content_key, "source": page["source_sha256"],
                                    "page_id": page_id})
            if any(c["chunk_id"] == chunk_id for c in chunks):
                return
            chunk_flags = page_flags + (flags or [])
            if count_tokens(text) > budget:
                chunk_flags.append("oversized_atomic_unit_embedding_windowed")
            item = ChunkRecord(chunk_id, content_key, page_id, page["document_id"],
                               page["source_relative"], page["source_sha256"], page["image_sha256"],
                               page.get("page_number"), kind, text, parent_id or page_id,
                               heading, blocks or [], spans or [], box, table, chunk_flags, meta)
            chunks.append(asdict(item))

        ordered = sorted(layout.get("blocks", []), key=lambda b: b["reading_order"])
        parents[page_id] = {"page_id": page_id, "text": "\n".join(b.get("text_raw", "") for b in ordered)}
        if policy == "flat":
            for text in split_prose(ocr["text"], count_tokens, budget, overlap):
                add(text, "text", flags=["flat_raw_ocr_baseline"])
        else:
            tables = {t["block_id"]: t for t in layout.get("tables", [])}
            heading, buffer, used_spans = "", [], set()

            def flush():
                if not buffer:
                    return
                text = "\n".join(b.get("text_raw", "") for b in buffer)
                ids = [b["block_id"] for b in buffer]
                spans = list(dict.fromkeys(s for b in buffer for s in b.get("span_ids", [])))
                boxes = [b["bbox"] for b in buffer if b.get("bbox")]
                box = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                       max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None
                flags = ["reading_order_uncertain"] if any(b.get("order_origin") == "unlisted_item_appended" for b in buffer) else []
                prefix = heading + "\n" if heading else ""
                for part in split_prose(text, count_tokens, max(64, budget-count_tokens(prefix)), overlap):
                    add(prefix + part, "text", heading, ids, spans, box, flags=flags)
                buffer.clear()

            for block in ordered:
                used_spans.update(block.get("span_ids", []))
                if block["type"] == "section_header":
                    flush()
                    heading = block.get("text_raw", "")
                    buffer.append(block)
                elif block["block_id"] in tables:
                    flush()
                    table = tables[block["block_id"]]
                    rows = table_rows(table)
                    header_rows = sorted({c["row_index"] for c in table["cells"] if c.get("column_header")})
                    header = "\n".join(rows[r] for r in header_rows)
                    prefix = "\n".join(x for x in (heading, "TABLE", header) if x)
                    parents[table["table_id"]] = {"page_id": page_id, "table": table,
                                                  "text": heading + "\n" + "\n".join(rows)}
                    group = []
                    for row in rows:
                        if group and count_tokens(prefix + "\n" + "\n".join(group + [row])) > budget:
                            add(prefix + "\n" + "\n".join(group), "table", heading,
                                [block["block_id"]], block.get("span_ids"), table["bbox"], table,
                                table["table_id"], table.get("quality_flags", []))
                            group = []
                        group.append(row)
                    if group:
                        add(prefix + "\n" + "\n".join(group), "table", heading,
                            [block["block_id"]], block.get("span_ids"), table["bbox"], table,
                            table["table_id"], table.get("quality_flags", []))
                elif block["type"] == "figure":
                    flush()
                    text = heading + "\nFigure: " + block.get("text_raw", "")
                    add(text, "figure", heading, [block["block_id"]], block.get("span_ids"),
                        block.get("bbox"), flags=["visual_content_not_transcribed"])
                else:
                    buffer.append(block)
            flush()
            missing = [s for s in ocr.get("spans", []) if s["span_id"] not in used_spans]
            for s in missing:
                add(s["text"], "text", spans=[s["span_id"]], box=s.get("bbox"), flags=["raw_ocr_fallback"])
        # Every page is a visual candidate, including handwriting not detected as a figure.
        add("Page image. " + ocr.get("text", "")[:1500], "page_visual", box=[0, 0, 1, 1],
            flags=["visual_descriptor_pending"])
    return chunks, parents
