"""Document/page routing plus an in-scope leaf escape branch."""
from dataclasses import replace
from ..backends.rag_models import TextEncoder
from ..contracts import RetrievedEvidence
from .retrieval import retrieve


def retrieve_hierarchical(store, hierarchy, plan, config, manifest):
    allowed = store.allowed(plan.hard_filters, include_visual=plan.visual_policy != "off")
    vector = None
    if allowed and plan.strategy != "lexical":
        spec = manifest["embedding"]
        encoder = TextEncoder(spec["path"],spec["repo"],config.query_device)
        try:
            vector = encoder.encode([plan.search_question],query=True)[0]
        finally:
            encoder.close()
    documents = hierarchy.rank(plan.search_question,allowed,"document",vector,limit=config.hierarchy_documents)
    document_ids = {hierarchy.nodes[nid]["document_id"] for nid,_ in documents}
    pages = []
    for did in sorted(document_ids):
        pages += hierarchy.rank(plan.search_question,allowed,"page",vector,documents={did},limit=config.hierarchy_pages)
    selected = set()
    for nid,_ in pages:
        selected.update(hierarchy.nodes[nid]["chunk_ids"])
    scoped = [cid for cid in allowed if cid in selected]
    # Both routes use precisely the user's allowed set; derived routing never relaxes it.
    leaf_config = replace(config,evidence_limit=config.research_evidence_per_task,
        rerank_candidates=max(config.rerank_candidates,config.research_evidence_per_task))
    narrow = replace(plan,hard_filters={**plan.hard_filters,"page_id":list(dict.fromkeys(store.rows[c]["page_id"] for c in scoped))})
    # Retain original filters as well, including document/kind/equipment metadata.
    routed, route_trace = retrieve(store,narrow,leaf_config,manifest,vector) if scoped else ([],{})
    escaped, escape_trace = retrieve(store,plan,leaf_config,manifest,vector)
    seen, result = set(),[]
    for e in routed + escaped:
        cid = e.chunk["chunk_id"]
        if cid not in seen:
            seen.add(cid)
            result.append(e)
    # Expand only real leaf records, so an added value has its own chunk/bbox citation.
    parents = {e.chunk.get("parent_id") for e in result if e.chunk["kind"]=="table"}
    for cid in allowed:
        chunk = store.rows[cid]
        if cid not in seen and chunk["kind"]=="table" and chunk.get("parent_id") in parents:
            result.append(RetrievedEvidence(chunk,0,scores={"parent_expansion":True}))
            seen.add(cid)
        if len(result)>=config.research_evidence_per_task*4:
            break
    return result, {"documents":[nid for nid,_ in documents],"pages":[nid for nid,_ in pages],
        "eligible_chunks":len(allowed),"routed":route_trace,"escape":escape_trace}
