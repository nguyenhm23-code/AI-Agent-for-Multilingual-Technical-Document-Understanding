"""Validate requirement coverage against supplied source text/visual observations."""
from dataclasses import asdict, is_dataclass
import re
from ..contracts import EvidenceAssessment
from .question_analysis import ATTRIBUTES
from .planner import fold


def contains_entity(entity, text):
    return bool(re.search(r"(?<!\w)"+re.escape(fold(entity))+r"(?!\w)",fold(text)))


def attribute_supported(requirement, chunk, quote):
    attribute = requirement.get("attribute")
    if attribute not in ATTRIBUTES or attribute=="name":
        return True
    pattern = r"\b(?:"+ATTRIBUTES[attribute]+r")\b"
    entity = requirement.get("entity", "")
    if not chunk.get("table"):
        if not entity:
            return bool(re.search(pattern, fold(quote)))
        return any(contains_entity(entity, line) and re.search(pattern, fold(line)) for line in quote.splitlines())
    cells = (chunk.get("table") or {}).get("cells", [])
    # Table context means a column header plus its actual value in the quoted
    # entity row, not an arbitrary attribute mentioned elsewhere on the page.
    wording = fold(requirement.get("wording", ""))
    header_pattern = re.escape(wording) if wording=="latest estimate" else pattern
    columns = {c["column_index"] for c in cells if c.get("column_header") and
               re.search(header_pattern, fold(c.get("text_raw", "")))}
    rows = {c["row_index"] for c in cells if entity and contains_entity(entity, c.get("text_raw", ""))}
    if not entity:
        rows = {c["row_index"] for c in cells if c.get("text_raw") and c["text_raw"] in quote and not c.get("column_header")}
    return any(c.get("column_index") in columns and c.get("row_index") in rows and
               c.get("text_raw") and c["text_raw"] in quote and c.get("content_status") not in {"unknown", "missing", "unreadable"}
               for c in cells)


def assess_evidence(analysis, evidence, visual_result=None, generated_coverage=None):
    ids = [r.requirement_id if is_dataclass(r) else r["requirement_id"] for r in analysis.requirements]
    requirements = {(asdict(r) if is_dataclass(r) else r)["requirement_id"]:
                    (asdict(r) if is_dataclass(r) else r) for r in analysis.requirements}
    if visual_result and visual_result.conflicts:
        return EvidenceAssessment("conflict", reason_codes=["visual_text_conflict"])
    if not evidence:
        return EvidenceAssessment("missing", ids, next_action="research", reason_codes=["empty_evidence"])
    if analysis.needs_calculation and generated_coverage is None:
        return EvidenceAssessment("requires_research", ids, next_action="research", reason_codes=["calculator_required"])
    if visual_result and visual_result.ambiguous_requirement_ids:
        return EvidenceAssessment("missing", visual_result.ambiguous_requirement_ids,
                                  needs_visual=True, next_action="research", reason_codes=["visual_ambiguous"])
    if analysis.needs_visual and (not visual_result or not any(
            (asdict(o) if is_dataclass(o) else o).get("state")=="observed" for o in visual_result.observations)):
        return EvidenceAssessment("missing", ids, needs_visual=True, next_action="research", reason_codes=["visual_missing"])
    if generated_coverage is None:
        return EvidenceAssessment("requires_research" if analysis.needs_dependency else "missing", ids,
                                  next_action="generate", reason_codes=["coverage_not_yet_generated"])
    sources = {e.evidence_id: e.chunk for e in evidence}
    observations = {(asdict(o) if is_dataclass(o) else o)["observation_id"]:
                    (asdict(o) if is_dataclass(o) else o) for o in (visual_result.observations if visual_result else [])}
    missing, errors, support, seen = [], [], [], set()
    for item in generated_coverage:
        rid = item.get("requirement_id")
        if rid not in ids or rid in seen:
            errors.append("Unknown or duplicate requirement ID")
            continue
        seen.add(rid)
        refs = item.get("support_refs", item.get("supports", []))
        valid = item.get("state") == "supported" and bool(refs)
        for ref in refs:
            if isinstance(ref, str):
                ref = {"source_id": ref, "quote": item.get("quote", "")}
            source = ref.get("source_id", ref.get("evidence_id", ref.get("observation_id")))
            if source in sources:
                quote = ref.get("quote", "")
                if sources[source].get("kind")=="page_visual" or not quote or quote not in sources[source]["text"]:
                    valid = False
                    errors.append("Text support quote is absent from supplied evidence")
                requirement = requirements[rid]
                if not attribute_supported(requirement, sources[source], quote):
                    valid = False
                    errors.append("Source does not identify the required attribute")
                if requirement.get("entity") and not contains_entity(requirement["entity"], quote):
                    valid = False
                    errors.append("Source does not identify the requested entity")
            elif source in observations:
                if observations[source].get("state") != "observed" or observations[source].get("requirement_id") != rid:
                    valid = False
                    errors.append("Visual support does not match requirement")
                if not ref.get("quote") or ref["quote"] not in observations[source].get("observed_text", ""):
                    valid = False
                    errors.append("Visual support quote is absent from validated observation")
            else:
                valid = False
                errors.append("Unknown support source")
        if valid:
            support.append(item)
        else:
            missing.append(rid)
    missing += [rid for rid in ids if rid not in seen]
    return EvidenceAssessment("missing" if missing or errors else "sufficient", missing, support,
                              next_action="research" if missing else "answer", reason_codes=["validated_coverage"], validation_errors=errors)
