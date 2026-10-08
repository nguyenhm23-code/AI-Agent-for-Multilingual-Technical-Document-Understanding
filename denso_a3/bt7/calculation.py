"""Typed Decimal arithmetic over source literals; no eval or model arithmetic."""
import re
from decimal import Decimal, InvalidOperation


def decimal_number(value):
    value = value.replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?",value):
        value = value.replace(",","")
    elif "," in value and "." not in value:
        value = value.replace(",",".")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid numeric literal") from exc
    if not number.is_finite():
        raise ValueError("Non-finite numeric literal")
    return number


def parse_quantity(raw):
    """Normalize explicit currency, scales and per-mille from an exact source literal."""
    match = re.fullmatch(r"\s*(GBP|USD|EUR|£|\$|€)?\s*(-?\d[\d,.]*)\s*(.*?)\s*",raw,re.I)
    if not match:
        raise ValueError("Unsupported quantity literal")
    currency,number,suffix = match.groups()
    scale = None
    scale_match = re.fullmatch(r"(million|billion|thousand|bn|[mk])(?:\s+(.*))?",suffix,re.I)
    if scale_match and (currency or scale_match.group(1).lower() not in {"m","k"} or scale_match.group(2)):
        scale,suffix = scale_match.group(1),scale_match.group(2) or ""
    amount = decimal_number(number)
    amount *= {"m":Decimal(10)**6,"million":Decimal(10)**6,"bn":Decimal(10)**9,
        "billion":Decimal(10)**9,"k":Decimal(1000),"thousand":Decimal(1000)}.get((scale or "").lower(),Decimal(1))
    currency = {"£":"GBP","$":"USD","€":"EUR"}.get(currency,currency.upper() if currency else None)
    suffix = suffix.lower().strip().rstrip(".")
    if currency:
        dimensions = {currency:1}
        if suffix in {"per mille","per 1000 sticks","/1000 sticks","per thousand sticks"}:
            amount /= 1000
            dimensions["stick"] = -1
        elif suffix in {"per stick","/stick"}:
            dimensions["stick"] = -1
        elif suffix:
            raise ValueError("Unsupported currency denominator")
    else:
        units = {"":"number","sticks":"stick","stick":"stick","cigarettes":"stick",
                 "%":"percent","v":"V","a":"A","rpm":"rpm","mm":"mm","cm":"mm","m":"mm",
                 "pa":"Pa","kpa":"Pa","mpa":"Pa"}
        if suffix not in units:
            raise ValueError("Unit is missing or unsupported")
        amount *= {"cm":10,"m":1000,"kpa":1000,"mpa":1000000}.get(suffix,1)
        dimensions = {} if units[suffix]=="number" else {units[suffix]:1}
    return str(amount),dimensions


def calculate(operations, facts, question_conditions=None, *, validated_plan=None):
    """Calculate a validated DAG with separate document/condition provenance.

    Legacy source-only calls remain valid. User inputs require the canonical
    validated plan, which is checked against its literal question provenance.
    """
    conditions = list(question_conditions or [])
    if conditions:
        if not validated_plan or not validated_plan.get("_validated"):
            raise ValueError("Question conditions require a validated research plan")
        from ..bt6.question_conditions import validate_question_conditions
        conditions = validate_question_conditions(validated_plan.get("_question", ""),
            validated_plan.get("_clarification"),conditions)
        if conditions!=validated_plan.get("question_conditions",[]):
            raise ValueError("Calculator conditions differ from the validated plan")
    values = {k:{"value":v["value"],"dimensions":v["dimensions"],"sources":[v["chunk_id"]],
                 "condition_ids":[]} for k,v in facts.items() if v["unit"]!="text"}
    for row in conditions:
        if row["kind"] not in {"ratio","quantity"} or not row["value"]:
            continue
        cid = row["condition_id"]
        if cid in values:
            raise ValueError("Condition ID collides with a document fact")
        values[cid] = {"value":row["value"],"dimensions":row["dimensions"],"sources":[],"condition_ids":[cid]}
    results = []
    for op in operations:
        operands = [values[x] for x in op["inputs"]]
        periods = {facts[x].get("period") for x in op["inputs"] if x in facts and facts[x].get("period")}
        if op["operation"] in {"sum","subtract"} and len(periods)>1:
            raise ValueError("Explicit source periods differ; select a common period")
        dimensions = dict(operands[0]["dimensions"])
        numbers = [Decimal(x["value"]) for x in operands]
        if op["operation"] in {"sum","subtract"}:
            if any(x["dimensions"]!=dimensions for x in operands):
                raise ValueError("Cannot add/subtract incompatible units")
            number = sum(numbers,Decimal(0)) if op["operation"]=="sum" else numbers[0]-numbers[1]
        elif op["operation"] in {"multiply","divide"}:
            sign = 1 if op["operation"]=="multiply" else -1
            for unit,power in operands[1]["dimensions"].items():
                dimensions[unit] = dimensions.get(unit,0)+sign*power
            dimensions = {u:p for u,p in dimensions.items() if p}
            if op["operation"]=="divide" and numbers[1]==0:
                raise ValueError("Division by zero")
            number = numbers[0]*numbers[1] if sign==1 else numbers[0]/numbers[1]
        else:
            raise ValueError("Unsupported operation")
        result = {"id":op["id"],"label":op["label"],"operation":op["operation"],"inputs":op["inputs"],
            "value":str(number),"dimensions":dimensions,
            "sources":list(dict.fromkeys(cid for operand in operands for cid in operand["sources"])),
            "condition_ids":list(dict.fromkeys(cid for operand in operands for cid in operand["condition_ids"]))}
        results.append(result)
        values[op["id"]] = result
    return results
