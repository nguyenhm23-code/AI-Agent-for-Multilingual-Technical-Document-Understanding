from html import escape


def table_html(table: dict) -> str:
    """Grid-first, preserving merged-cell spans and escaping source text."""
    cells = {(c["row_index"], c["column_index"]): c for c in table["cells"]}
    occupied = set()
    rows = []
    for row in range(table["num_rows"]):
        values = []
        for col in range(table["num_columns"]):
            if (row, col) in occupied:
                continue
            cell = cells.get((row, col))
            if cell is None:
                values.append('<td class="unknown">?</td>')
                continue
            rs, cs = cell["rowspan"], cell["colspan"]
            for r in range(row, row + rs):
                for c in range(col, col + cs):
                    if (r, c) != (row, col):
                        occupied.add((r, c))
            tag = "th" if cell.get("column_header") else "td"
            values.append(f'<{tag} rowspan="{rs}" colspan="{cs}">{escape(cell["text_raw"]).replace(chr(10), "<br>")}</{tag}>')
        rows.append("<tr>" + "".join(values) + "</tr>")
    return '<table class="extracted">' + "".join(rows) + "</table>"


def table_markdown(table: dict) -> str:
    grid = [["" for _ in range(table["num_columns"])] for _ in range(table["num_rows"])]
    for cell in table["cells"]:
        grid[cell["row_index"]][cell["column_index"]] = cell["text_raw"].replace("|", "\\|").replace("\n", " ")
    if not grid:
        return ""
    lines = ["| " + " | ".join(row) + " |" for row in grid]
    lines.insert(1, "| " + " | ".join(["---"] * table["num_columns"]) + " |")
    return "\n".join(lines) + "\n\n<!-- View only: merged-cell structure is in JSON/HTML. -->"


def continuation_candidates(layouts: list[dict], pages: list[dict]) -> list[dict]:
    by_document = {}
    by_id = {p["page_id"]: p for p in pages}
    for layout in layouts:
        page = by_id[layout["page_id"]]
        if page["page_number"] is not None:
            by_document.setdefault(page["document_id"], {})[page["page_number"]] = layout
    candidates = []
    for document in by_document.values():
        for number, left in document.items():
            right = document.get(number + 1)
            if not right:
                continue
            for a in left.get("tables", []):
                for b in right.get("tables", []):
                    if a["num_columns"] == b["num_columns"] and a["bbox"][3] > 0.6 and b["bbox"][1] < 0.3:
                        candidates.append({"from_table": a["table_id"], "to_table": b["table_id"],
                                           "status": "candidate_not_merged", "reason": "adjacent_pages_same_column_count_and_edge_positions"})
    return candidates
