"""Validate scenario inputs against literal user wording, never document facts."""
import re
from dataclasses import asdict, is_dataclass
from decimal import Decimal

from ..contracts import QuestionCondition
from ..bt7.calculation import parse_quantity


def _row(value):
    return asdict(value) if is_dataclass(value) else dict(value)


def validate_question_conditions(question, clarification=None, candidates=None):
    """Return conditions with unique literal spans and code-normalized quantities.

    Offsets refer to the complete question or clarification indicated by origin.
    A supplied offset disambiguates repeated wording; without one the literal must
    occur once. ``applies_to`` must be literal within the condition's wording, so a
    model cannot relabel a target-completion percentage as a sales-transfer ratio.
    """
    if not isinstance(question, str) or clarification is not None and not isinstance(clarification, str):
        raise ValueError("Question/clarification must be strings")
    result, seen, spans = [], set(), set()
    for candidate in candidates or []:
        row = _row(candidate)
        cid, origin = row.get("condition_id"), row.get("origin", "user_question")
        if not isinstance(cid, str) or not re.fullmatch(r"U[1-9]\d*", cid) or cid in seen:
            raise ValueError("Question conditions require unique U1,U2,... IDs")
        if origin not in {"user_question", "user_clarification"}:
            raise ValueError("Unsupported condition origin")
        source = question if origin=="user_question" else clarification
        if not source:
            raise ValueError("Condition has no user wording for its origin")
        raw, applies = row.get("raw_text", ""), row.get("applies_to", "")
        if not isinstance(raw, str) or not raw.strip() or len(raw)>1500:
            raise ValueError("Condition needs literal user wording")
        if not isinstance(applies, str) or not applies.strip() or applies not in raw:
            raise ValueError("Condition applies_to must identify its literal entity/relation")
        start, end = row.get("start"), row.get("end")
        if start is None and end is None:
            occurrences = [m.start() for m in re.finditer(re.escape(raw), source)]
            if len(occurrences)!=1:
                raise ValueError("Condition literal is absent or repeated; clarify its span")
            start, end = occurrences[0], occurrences[0]+len(raw)
        if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int)
                or not isinstance(end, int) or start<0 or end<=start or end>len(source)
                or source[start:end]!=raw):
            raise ValueError("Condition span does not match literal user wording")
        if (origin,start,end) in spans:
            raise ValueError("Duplicate question condition span")
        kind = row.get("kind", "text")
        value, unit, dimensions, conversions = "", "text", {}, []
        if kind in {"ratio", "percentage", "percent"}:
            percentages = list(re.finditer(r"(?<![\w.,])(-?\d+(?:[.,]\d+)?)\s*%", raw))
            if len(percentages)!=1:
                raise ValueError("A ratio condition needs one explicit percentage literal")
            literal = percentages[0].group()
            from ..bt7.calculation import decimal_number
            value = str(decimal_number(percentages[0].group(1))/Decimal(100))
            unit, kind = "number", "ratio"
            conversions = [{"literal":literal,"operation":"divide","divisor":"100","result":value}]
        elif kind in {"quantity", "number"}:
            # Numeric text embedded in arbitrary business prose is deliberately
            # unsupported. A number may be supplied separately with its own span.
            value, dimensions = parse_quantity(raw)
            unit = " * ".join(f"{k}^{v}" if v!=1 else k for k,v in sorted(dimensions.items())) or "number"
            kind = "quantity"
        elif kind!="text":
            raise ValueError("Unsupported question condition kind")
        # Reject a forged normalized value even if the literal itself is genuine.
        for key, normalized in (("value",value),("unit",unit),("dimensions",dimensions)):
            if key in row and row[key] not in (None,"",{}) and row[key]!=normalized:
                raise ValueError("Condition normalized value/unit does not match its literal")
        condition = QuestionCondition(condition_id=cid,kind=kind,raw_text=raw,start=start,end=end,
            origin=origin,applies_to=applies,value=value,unit=unit,dimensions=dimensions,
            conversion_trace=conversions,source_text=source)
        result.append(asdict(condition))
        seen.add(cid)
        spans.add((origin,start,end))
    return result


def condition_candidates(question, clarification=None):
    """Conservatively identify explicit percentage scenarios for the planner.

    Preserve the complete clause and its relation; no percentage is interpreted
    as a transfer condition merely because it appears in the question.
    """
    rows = []
    for origin, source in (("user_question",question),("user_clarification",clarification or "")):
        for match in re.finditer(r"[^.;!?\n]*\d+(?:[.,]\d+)?\s*%[^.;!?\n]*", source):
            raw = match.group().strip()
            if len(re.findall(r"\d+(?:[.,]\d+)?\s*%",raw))!=1:
                continue
            start = match.start()+len(match.group())-len(match.group().lstrip())
            rows.append({"condition_id":f"U{len(rows)+1}","kind":"ratio","raw_text":raw,
                "start":start,"end":start+len(raw),"origin":origin,"applies_to":raw})
    return rows
