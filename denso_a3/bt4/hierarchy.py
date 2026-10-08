"""Extractive document -> page -> chunk hierarchy. Routing is never evidence."""
from dataclasses import asdict
from collections import defaultdict
from ..contracts import HierarchyNode
from ..io import fingerprint


def routing_text(chunks, heading="", limit=6000):
    # Bounded source snippets; truncation can omit details, so retrieval also has a leaf escape.
    texts = [heading]
    for chunk in chunks:
        if chunk["kind"] == "page_visual":
            continue
        text = chunk.get("text", "")
        texts.append((chunk.get("heading", "") + "\n" + text[:450]).strip())
    return "\n".join(dict.fromkeys(t for t in texts if t))[:limit]


def build_hierarchy(chunks, documents):
    by_doc, by_page = defaultdict(list), defaultdict(list)
    for chunk in chunks:
        by_doc[chunk["document_id"]].append(chunk)
        by_page[(chunk["document_id"], chunk["page_id"])].append(chunk)
    names = {d["document_id"]: d["source_relative"] for d in documents}
    nodes = []
    for did, items in by_doc.items():
        docid = "hdoc_" + fingerprint({"document": did})[:24]
        nodes.append(asdict(HierarchyNode(docid, "document", did, None, names[did],
            [c["chunk_id"] for c in items], list(dict.fromkeys(c["page_id"] for c in items)),
            routing_text(items, names[did]))))
        for (pdid, pid), leaves in by_page.items():
            if pdid != did:
                continue
            title = " | ".join(dict.fromkeys(c.get("heading", "") for c in leaves if c.get("heading")))
            nodes.append(asdict(HierarchyNode("hpage_" + fingerprint({"page": pid})[:24],
                "page", did, docid, title or pid, [c["chunk_id"] for c in leaves], [pid],
                routing_text(leaves, title))))
    validate_hierarchy(nodes, chunks)
    return nodes


def validate_hierarchy(nodes, chunks):
    records = {c["chunk_id"]: c for c in chunks}
    mapping = {n["node_id"]: n for n in nodes}
    if len(mapping) != len(nodes):
        raise ValueError("Duplicate hierarchy node")
    covered = set()
    for node in nodes:
        if node["level"] not in {"document", "page"} or not node["chunk_ids"]:
            raise ValueError("Invalid hierarchy level/empty node")
        parent = node["parent_node_id"]
        if node["level"] == "page" and (parent not in mapping or mapping[parent]["level"] != "document"):
            raise ValueError("Orphan page node")
        if node["level"] == "document" and parent is not None:
            raise ValueError("Document node must be a root")
        for cid in node["chunk_ids"]:
            if cid not in records or records[cid]["document_id"] != node["document_id"]:
                raise ValueError("Hierarchy source mismatch")
            if records[cid]["page_id"] not in node["page_ids"]:
                raise ValueError("Hierarchy page mismatch")
            if parent and cid not in mapping[parent]["chunk_ids"]:
                raise ValueError("Child outside parent")
        if node["level"] == "page":
            covered.update(node["chunk_ids"])
    if covered != set(records):
        raise ValueError("Hierarchy does not cover every chunk")
