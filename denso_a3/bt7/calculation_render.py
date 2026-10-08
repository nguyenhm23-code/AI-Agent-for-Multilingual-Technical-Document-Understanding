"""Render typed calculations; orchestration and verification share this formatter."""


def render_calculations(plan, facts, calculations, evidence, question_conditions, language):
    ids = {e.chunk["chunk_id"]: e.evidence_id for e in evidence}
    vi = language == "vi"
    lines = ["Các dữ kiện dùng để tính:" if vi else "Inputs used:"]
    for task in plan["tasks"]:
        fact = facts[task["task_id"]]
        lines.append(f"- {task['label']}: {fact['raw_value']} [{ids[fact['chunk_id']]}]")
    if question_conditions:
        lines += ["", "Điều kiện nêu trong câu hỏi:" if vi else "Conditions stated in the question:"]
        for row in question_conditions:
            lines.append(f"- {row['condition_id']}: {row['raw_text']} ({row['origin']}, {row['start']}:{row['end']})")
    lines += ["", "Kết quả tính toán:" if vi else "Calculated results:"]
    for result in calculations:
        unit = " * ".join(f"{k}^{p}" if p != 1 else k for k, p in result["dimensions"].items()) or "number"
        refs = " ".join(f"[{ids[cid]}]" for cid in result["sources"])
        condition_refs = ", ".join(result.get("condition_ids", []))
        if condition_refs:
            refs += ("; điều kiện " if vi else "; conditions ") + condition_refs
        expression = {"sum": " + ", "subtract": " - ", "multiply": " × ", "divide": " ÷ "}[result["operation"]].join(result["inputs"])
        lines.append(f"- {result['label']}: **{result['value']} {unit}** ({expression}). {refs}")
    if any(f.get("support_kind") == "image" for f in facts.values()):
        lines.append("Dữ kiện ảnh được đọc từ vùng nguồn có provenance trong citations." if vi else
                     "Image inputs were read from the cited source regions.")
    return "\n".join(lines)
