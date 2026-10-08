import time

from ..backends.rag_models import TextEncoder, Reranker
from ..contracts import RetrievedEvidence


def unique_ranking(ranking, rows):
    seen, result = set(), []
    for cid, score in ranking:
        key = rows[cid]["content_key"]
        if key not in seen:
            seen.add(key)
            result.append((cid, score))
    return result


def retrieve(store, plan, config, manifest, query_vector=None):
    started = time.perf_counter()
    visual = plan.visual_policy != "off"
    allowed = store.allowed(plan.hard_filters, include_visual=visual)
    trace = {"eligible_chunks": len(allowed), "plan": plan.to_dict(), "stages_ms": {},
             "requested_strategy": plan.requested_strategy, "effective_strategy": plan.strategy, "visual_policy": plan.visual_policy}
    rankings = {}
    if not allowed:
        return [], trace
    limit = min(len(allowed), config.candidates * 5)
    if plan.strategy != "dense":
        tick = time.perf_counter()
        rankings["lexical"] = unique_ranking(store.lexical(plan.search_question, allowed, limit), store.rows)[:config.candidates]
        trace["stages_ms"]["lexical"] = (time.perf_counter()-tick)*1000
    if plan.strategy != "lexical":
        tick = time.perf_counter()
        spec = manifest["embedding"]
        if query_vector is None:
            encoder = TextEncoder(spec["path"], spec["repo"], config.query_device)
            try:
                vector = encoder.encode([plan.search_question], query=True)[0]
            finally:
                encoder.close()
        else:
            vector = query_vector
        rankings["dense"] = unique_ranking(store.dense(vector, allowed, limit), store.rows)[:config.candidates]
        trace["stages_ms"]["dense_with_model_load"] = (time.perf_counter()-tick)*1000
    candidates = {}
    by_content = {}
    for branch, ranking in rankings.items():
        for rank, (cid, raw_score) in enumerate(ranking, 1):
            content = store.rows[cid]["content_key"]
            canonical = by_content.setdefault(content, cid)
            item = candidates.setdefault(canonical, RetrievedEvidence(store.rows[canonical], 0))
            item.score += 1/(60+rank)
            item.scores[branch] = {"rank": rank, "raw": raw_score}
    items = list(candidates.values())
    for item in items:
        metadata = item.chunk.get("metadata", {})
        matches = sum(metadata.get(k, {}).get("value") == v for k, v in plan.soft_preferences.items()) if config.metadata_boost else 0
        boost = matches * .002
        if "table" in plan.intents and item.chunk["kind"] == "table":
            boost += .002
        item.scores["metadata_boost"] = boost
        item.score += boost
    items.sort(key=lambda x: x.score, reverse=True)
    items = items[:config.rerank_candidates]
    if config.rerank and len(items) > config.evidence_limit:
        tick = time.perf_counter()
        spec = manifest["model_manifest"]["models"]["reranker"]
        model = Reranker(spec["path"], config.rerank_device)
        try:
            scores = model.score(plan.search_question, [e.chunk["text"] for e in items])
        finally:
            model.close()
        for item, score in zip(items, scores):
            item.scores["reranker_logit"] = score
        items.sort(key=lambda x: x.scores["reranker_logit"], reverse=True)
        trace["stages_ms"]["rerank_with_model_load"] = (time.perf_counter()-tick)*1000
    elif config.rerank and items:
        trace["rerank_skipped"] = "All candidates fit the evidence budget"
    items = items[:config.evidence_limit]
    for i, item in enumerate(items, 1):
        item.evidence_id = f"E{i}"
    trace["retrieval_ms"] = (time.perf_counter()-started)*1000
    trace["candidate_count"] = len(candidates)
    trace["rankings"] = {k: [{"chunk_id": cid, "score": score} for cid, score in rows] for k, rows in rankings.items()}
    return items, trace
