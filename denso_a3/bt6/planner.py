import re
import unicodedata

from ..contracts import QueryPlan
from ..bt4.metadata import verified_values, FIELDS


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
                   if unicodedata.category(c) != "Mn")


def language_of(text):
    return "vi" if fold(text) != text.casefold() or re.search(r"\b(bao nhieu|tai lieu|phien ban|la gi)\b", text.casefold()) else "en"


def plan_query(question, registry, strategy="hybrid", filters=None, search_question=None, visual_policy="auto"):
    if not isinstance(question, str) or not question.strip() or len(question) > 8000:
        raise ValueError("Question must contain 1–8000 characters")
    if strategy not in {"auto", "lexical", "dense", "hybrid", "visual"}:
        raise ValueError("Unknown retrieval strategy")
    requested = strategy
    if strategy in {"auto", "visual"}:
        visual_policy = "required" if strategy == "visual" else "auto"
        strategy = "hybrid"
    plan = QueryPlan(question, search_question or question, language_of(question), strategy,
                     dict(filters or {}))
    plan.requested_strategy, plan.visual_policy = requested, visual_policy
    allowed = {"source_relative", "document_id", "page_id", "page_number", "kind"} | set(FIELDS)
    if set(plan.hard_filters) - allowed:
        raise ValueError("Unsupported metadata filter")
    q = fold(plan.search_question)
    if re.search(r"\b(chart|graph|diagram|photo|image|handwrit\w*|bieu do|hinh anh|viet tay)\b", q) or visual_policy == "required":
        plan.intents.append("visual")
    if re.search(r"\b(table|row|column|bang|hang|cot|thong so)\b", q):
        plan.intents.append("table")
    if re.search(r"\b(version|revision|compare|latest(?!\s+estimates?\b)|current|phien ban|so sanh|hien hanh|moi nhat)\b", q):
        plan.intents.append("version")
    if re.search(r"\b(?:(?:latest|current)(?!\s+(?:estimate|year|month|quarter|week|day)s?\b)|hien hanh|moi nhat)\b", q):
        plan.intents.append("current")
    if re.search(r"\b[A-Za-z]+[-_]?[0-9]+[-A-Za-z0-9]*\b", plan.search_question):
        plan.intents.append("identifier")
    docs = registry["documents"]
    for field in ("equipment_id", "part_number", "document_type"):
        matches = {cell.get("value") for record in docs.values()
                   if (cell := record.get("fields", {}).get(field, {})).get("value")
                   and fold(cell["value"]) in q}
        if len(matches) == 1:
            plan.soft_preferences[field] = matches.pop()
    revision = re.search(r"\b(?:revision|rev\.?|phien ban)\s+([\w.-]+)", q)
    if revision and revision.group(1) not in {"truoc", "cu", "moi", "previous", "old", "latest"}:
        value = revision.group(1)
        valid = {v["revision_id"] for d in docs.values() if "revision_id" in (v := verified_values(d.get("fields", {})))}
        found = [r for r in valid if fold(r) == value]
        if len(found) == 1:
            prior = plan.hard_filters.get("revision_id")
            if prior is not None and found[0] not in (prior if isinstance(prior,list) else [prior]):
                plan.clarification = "Requested revision conflicts with the selected revision scope."
            else:
                plan.hard_filters["revision_id"] = found[0]
        else:
            plan.clarification = ("Chưa có revision đã xác minh phù hợp; hãy chọn nguồn hoặc bổ sung metadata."
                                  if plan.language == "vi" else "No verified matching revision; select a source or supply verified metadata.")
    vague = re.fullmatch(r"\s*(what is (the )?(name of (the )?)?(company|university)|ten (cong ty|truong dai hoc)( la gi)?)\s*[?.]*", q)
    if vague and not plan.hard_filters.get("source_relative") and len(docs) > 1:
        plan.clarification = "Bạn muốn hỏi tài liệu nào?" if plan.language == "vi" else "Which document do you mean?"
    plan.reasons = ["Explicit source/verified metadata filters apply to all retrievers",
                    "Inferred metadata remains a soft preference", "Hybrid is the default; visual processing is conditional"]
    return plan
