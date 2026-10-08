"""Node persistence shares the leaf store's Qdrant client and its lock."""
import json
import re
import sqlite3
from ..bt4.hierarchy import routing_text
from .store import point_id


class HierarchyStore:
    def __init__(self, store, create=False):
        self.store = store
        self.db = sqlite3.connect(store.root / "hierarchy.sqlite")
        if create:
            self.db.executescript("CREATE TABLE nodes(id TEXT PRIMARY KEY, record TEXT NOT NULL);"
                "CREATE VIRTUAL TABLE node_text USING fts5(id UNINDEXED,text,tokenize='unicode61 remove_diacritics 2');")
        try:
            self.nodes = {cid: json.loads(record) for cid, record in self.db.execute("SELECT id,record FROM nodes")}
        except Exception:
            self.db.close()
            raise

    def add(self, nodes, vectors):
        models = self.store.models
        self.store.qdrant.create_collection("routing_nodes", vectors_config=models.VectorParams(
            size=vectors.shape[1], distance=models.Distance.COSINE))
        for start in range(0, len(nodes), 128):
            points = []
            for node, vector in zip(nodes[start:start+128], vectors[start:start+128]):
                nid = node["node_id"]
                self.db.execute("INSERT INTO nodes VALUES(?,?)", (nid, json.dumps(node, ensure_ascii=False)))
                self.db.execute("INSERT INTO node_text VALUES(?,?)", (nid, node["routing_text"]))
                points.append(models.PointStruct(id=point_id(nid), vector=vector.tolist(),
                    payload={"node_id": nid, "level": node["level"], "document_id": node["document_id"]}))
                self.nodes[nid] = node
            self.store.qdrant.upsert("routing_nodes", points=points, wait=True)
        self.db.commit()

    def rank(self, question, allowed, level, vector=None, documents=None, limit=6):
        permitted = set(allowed)
        nodes = [n for n in self.nodes.values() if n["level"] == level
            and (documents is None or n["document_id"] in documents) and permitted.intersection(n["chunk_ids"])]
        if not nodes:
            return []
        # Partial-scope parents use only allowed text, never a mixed cached synopsis.
        complete = [n for n in nodes if set(n["chunk_ids"]) <= permitted]
        restricted = [n for n in nodes if set(n["chunk_ids"]) - permitted]
        rankings = []
        terms = list(dict.fromkeys(re.findall(r"\w+", question)))[:64]
        if terms and complete:
            self.db.execute("CREATE TEMP TABLE IF NOT EXISTS eligible_nodes(id TEXT PRIMARY KEY)")
            self.db.execute("DELETE FROM eligible_nodes")
            self.db.executemany("INSERT INTO eligible_nodes VALUES(?)", [(n["node_id"],) for n in complete])
            match = " OR ".join('"' + t.replace('"','""') + '"' for t in terms)
            rankings.append([(nid, -score) for nid, score in self.db.execute(
                "SELECT node_text.id,bm25(node_text) AS s FROM node_text JOIN eligible_nodes "
                "ON eligible_nodes.id=node_text.id WHERE node_text MATCH ? ORDER BY s LIMIT ?", (match,limit))])
        if vector is not None and complete:
            models = self.store.models
            hits = self.store.qdrant.query_points("routing_nodes", query=vector.tolist(), limit=limit,
                query_filter=models.Filter(must=[models.HasIdCondition(has_id=[point_id(n["node_id"]) for n in complete])]),
                with_payload=True).points
            rankings.append([(h.payload["node_id"],h.score) for h in hits])
        folded_terms = {t.casefold() for t in terms}
        filtered = []
        for node in restricted:
            text = routing_text([self.store.rows[cid] for cid in node["chunk_ids"] if cid in permitted])
            score = sum(t in text.casefold() for t in folded_terms)
            if score:
                filtered.append((node["node_id"],score))
        rankings.append(sorted(filtered,key=lambda x:x[1],reverse=True))
        fused = {}
        for ranking in rankings:
            for rank,(nid,_) in enumerate(ranking,1):
                fused[nid] = fused.get(nid,0) + 1/(60+rank)
        return sorted(fused.items(),key=lambda x:x[1],reverse=True)[:limit]

    def close(self):
        self.db.close()
