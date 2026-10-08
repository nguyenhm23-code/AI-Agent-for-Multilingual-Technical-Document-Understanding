"""One eligibility predicate for SQLite FTS5 and Qdrant prefiltered search."""

import json
import re
import sqlite3
import uuid
from pathlib import Path

from ..bt4.metadata import verified_values, FIELDS


def point_id(chunk_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "denso:" + chunk_id))


def eligible(chunk, filters):
    values = verified_values(chunk.get("metadata", {}))
    for key, value in filters.items():
        if key in {"source_relative", "document_id", "page_id", "page_number", "kind"}:
            actual = chunk.get(key)
        elif key in FIELDS:
            actual = values.get(key)
        else:
            raise ValueError("Unsupported hard filter: " + key)
        if isinstance(value, list):
            if actual not in value:
                return False
        elif actual != value:
            return False
    return True


class RetrievalStore:
    def __init__(self, directory, create=False, dimension=None):
        from qdrant_client import QdrantClient, models
        self.root, self.models = Path(directory), models
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "records.sqlite")
        self.db.execute("PRAGMA foreign_keys=ON")
        try:
            self.qdrant = QdrantClient(path=str(self.root / "vectors"))
        except Exception:
            self.db.close()
            raise
        if create:
            self.db.execute("CREATE TABLE chunks(id TEXT PRIMARY KEY, content_key TEXT NOT NULL, record TEXT NOT NULL)")
            self.db.execute("CREATE VIRTUAL TABLE text_index USING fts5(id UNINDEXED, text, tokenize='unicode61 remove_diacritics 2')")
            self.qdrant.create_collection("chunks", vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE))
        self.rows = {r[0]: json.loads(r[1]) for r in self.db.execute("SELECT id, record FROM chunks")}

    def add(self, chunks, vectors):
        models = self.models
        for start in range(0, len(chunks), 128):
            points = []
            for chunk, vector in zip(chunks[start:start+128], vectors[start:start+128]):
                cid = chunk["chunk_id"]
                self.db.execute("INSERT INTO chunks VALUES (?, ?, ?)", (cid, chunk["content_key"], json.dumps(chunk, ensure_ascii=False)))
                self.db.execute("INSERT INTO text_index(id,text) VALUES (?,?)", (cid, chunk["text"]))
                points.append(models.PointStruct(id=point_id(cid), vector=vector.tolist(),
                    payload={"chunk_id": cid, "source_relative": chunk["source_relative"],
                             "page_id": chunk["page_id"], "kind": chunk["kind"],
                             **verified_values(chunk.get("metadata", {}))}))
                self.rows[cid] = chunk
            self.qdrant.upsert("chunks", points=points, wait=True)
        self.db.commit()

    def allowed(self, filters, include_visual=True):
        return [cid for cid, c in self.rows.items() if eligible(c, filters)
                and (include_visual or c["kind"] != "page_visual")]

    def lexical(self, question, allowed, limit):
        terms = list(dict.fromkeys(re.findall(r"\w+", question, re.UNICODE)))[:64]
        if not terms or not allowed:
            return []
        match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        # TEMP eligibility table applies filtering before ranking/limit and avoids SQL variable limits.
        self.db.execute("CREATE TEMP TABLE IF NOT EXISTS eligible_ids(id TEXT PRIMARY KEY)")
        self.db.execute("DELETE FROM eligible_ids")
        self.db.executemany("INSERT INTO eligible_ids VALUES (?)", ((cid,) for cid in allowed))
        rows = self.db.execute("SELECT text_index.id, bm25(text_index) AS score FROM text_index "
                               "JOIN eligible_ids ON eligible_ids.id=text_index.id "
                               "WHERE text_index MATCH ? ORDER BY score LIMIT ?", (match, limit)).fetchall()
        return [(cid, -score) for cid, score in rows]

    def dense(self, vector, allowed, limit):
        if not allowed:
            return []
        result = self.qdrant.query_points("chunks", query=vector.tolist(), limit=limit,
            query_filter=self.models.Filter(must=[self.models.HasIdCondition(has_id=[point_id(cid) for cid in allowed])]),
            with_payload=True).points
        return [(p.payload["chunk_id"], p.score) for p in result]

    def replace_metadata(self, registry):
        for cid, chunk in self.rows.items():
            chunk["metadata"] = registry["documents"][chunk["document_id"]]["fields"]
            self.db.execute("UPDATE chunks SET record=? WHERE id=?", (json.dumps(chunk, ensure_ascii=False), cid))
            self.qdrant.overwrite_payload("chunks", payload={"chunk_id": cid,
                "source_relative": chunk["source_relative"], "page_id": chunk["page_id"], "kind": chunk["kind"],
                **verified_values(chunk["metadata"])}, points=[point_id(cid)], wait=True)
        self.db.commit()

    def close(self):
        self.qdrant.close()
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
