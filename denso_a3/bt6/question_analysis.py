"""Rules describing the requested work; no model I/O or length classifier."""
import re
from ..contracts import QueryAnalysis, QueryRequirement
from .planner import fold

ATTRIBUTES = {
    "pressure": r"pressure|ap suat",
    "voltage": r"voltages?|dien ap",
    "temperature": r"temperature|nhiet do",
    "manufacturer": r"manufacturer|maker|nha san xuat",
    "cost": r"costs?|chi phi|gia thanh|latest estimate",
    "volume": r"volume|san luong|khoi luong",
    "name": r"name|ten",
    "rate": r"rate|ty le|per mille",
    "margin": r"gross margin|loi nhuan gop",
    "capacity": r"capacity|cong suat|dung tich",
    "weight": r"weight|trong luong|khoi luong",
    "budget": r"budgets?|ngan sach|latest estimate|co plan",
}


def explicit_entities(question):
    """Literal named pairs, avoiding guesses from examples or document names."""
    values = []
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9_-]*)\s+(?:and|và|va|versus|vs\.?|minus)\s+(?:Project\s+)?([A-Z][A-Za-z0-9_-]*)\b", question):
        values.extend(match.groups())
    return list(dict.fromkeys(v for v in values if v not in {"Project", "GBP", "USD", "EUR"}))


def analyze_query(question, query_plan):
    text = query_plan.search_question
    q = fold(text)
    analysis = QueryAnalysis(question, text, query_plan.language)
    analysis.needs_clarification = bool(query_plan.clarification)
    analysis.needs_visual = "visual" in query_plan.intents
    analysis.needs_calculation = bool(re.search(
        r"\b(calculate|compute|sum|subtract|multiply|divide|minus|net margin|gross margin.*(?:minus|less)|"
        r"tinh|tong|hieu|tru|nhan|chia|loi nhuan.*(?:rong|tru))\b|[+*/=]", q))
    analysis.needs_comparison = bool(re.search(r"\b(compare|comparison|versus|vs|difference between|so sanh|chenh lech)\b", q))
    analysis.needs_dependency = bool(re.search(
        r"\b(then|subsequently|manufacturer.s|maker.s|supplier.s|sau do|tiep theo|cua nha san xuat|"
        r"cua cong ty do|cua nha cung cap)\b", q))
    entities = explicit_entities(text)
    identifiers = re.findall(r"\b[A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b", text)
    singular = re.search(r"\b(?:of|for|của)\s+([A-Z][A-Za-z0-9_-]*)\b", text)
    direct = re.match(r"^(?:List |Read )?([A-Z][A-Za-z0-9_-]*)(?:'s)?\s+(?:voltage|weight|pressure|cost|budget|capacity|temperature)\b", text)
    if direct and not singular:
        singular = direct
    entity = identifiers[0] if len(set(identifiers))==1 else (singular.group(1) if singular else "")
    for attribute, pattern in ATTRIBUTES.items():
        match = re.search(r"\b(?:"+pattern+r")\b", q)
        if match:
            # Folded characters have the same offsets for Vietnamese base characters.
            wording = text[match.start():match.end()]
            if any(fold(r.wording)==fold(wording) for r in analysis.requirements):
                continue
            analysis.requirements.append(QueryRequirement(f"R{len(analysis.requirements)+1}", wording, entity=entity,
                                                          attribute=attribute, wording=wording))
    if not analysis.requirements:
        analysis.requirements = [QueryRequirement("R1", question, wording=question)]
    if entities:
        analysis.requirements = [QueryRequirement(f"R{i+1}", r.label+" / "+name, entity=name,
                                                  attribute=r.attribute, wording=r.wording)
                                 for i,(r,name) in enumerate((r,name) for r in analysis.requirements for name in entities)]
    signals = ((analysis.needs_calculation, "calculation"), (analysis.needs_comparison, "comparison"),
               (analysis.needs_dependency, "dependency"), (analysis.needs_visual, "visual"))
    analysis.rule_hits = [name for flag, name in signals if flag]
    analysis.ambiguous = bool(re.search(r"\b(analy[sz]e|evaluate|implications|why|recommend|synthesize|"
                                         r"phan tich|danh gia|tai sao|de xuat|tong hop)\b", q)) and not any(
        (analysis.needs_calculation, analysis.needs_comparison, analysis.needs_dependency))
    if not analysis.ambiguous and not any((analysis.needs_calculation, analysis.needs_comparison, analysis.needs_dependency)):
        analysis.rule_hits.append("direct_lookup" if len(analysis.requirements)==1 else "attribute_probe")
    return analysis
