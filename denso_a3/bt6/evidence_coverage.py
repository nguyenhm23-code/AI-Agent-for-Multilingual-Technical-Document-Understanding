"""Coverage is a ledger of supported inputs, never a confidence percentage."""


def check_coverage(plan, facts):
    tasks = [{"task_id":t["task_id"],"label":t["label"],
        "status":"supported" if t["task_id"] in facts else "missing"} for t in plan["tasks"]]
    return {"ready":all(t["status"]=="supported" for t in tasks),"tasks":tasks,
        "missing":[t["task_id"] for t in tasks if t["status"]=="missing"],
        "meaning":"All planned inputs have quoted evidence; this is not a probability of correctness"}


def fact_conflicts(task, old, new):
    # Only compare like-for-like explicit contexts; unresolved period differences are not equal contexts.
    if old and old["dimensions"]==new["dimensions"] and old.get("period")==new.get("period") and old.get("scenario")==new.get("scenario"):
        if old["value"]!=new["value"]:
            return {"task_id":task["task_id"],"values":[old["raw_value"],new["raw_value"]],
                    "chunk_ids":[old["chunk_id"],new["chunk_id"]],"reason":"Quoted candidate inputs disagree; context/authority unresolved"}
    return None
