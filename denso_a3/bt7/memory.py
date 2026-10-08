import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class ConversationStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS conversations(
            id TEXT PRIMARY KEY, title TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS turns(id INTEGER PRIMARY KEY, conversation TEXT,
            scope TEXT, question TEXT, answer TEXT);
            CREATE TABLE IF NOT EXISTS pending_research(conversation TEXT PRIMARY KEY, scope TEXT, state TEXT);''')

    def create(self, title="Cuộc trò chuyện mới"):
        cid = uuid.uuid4().hex
        self.db.execute("INSERT INTO conversations VALUES(?,?,?)", (cid, title, datetime.now(timezone.utc).isoformat()))
        self.db.commit()
        return cid

    def list(self):
        return [dict(zip(("id", "title", "created"), r)) for r in self.db.execute("SELECT * FROM conversations ORDER BY created DESC")]

    def history(self, cid, scope=None, limit=8):
        if not self.db.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone():
            raise ValueError("Unknown conversation")
        rows = self.db.execute("SELECT scope,question,answer FROM turns WHERE conversation=? " +
            ("AND scope=? " if scope is not None else "") + "ORDER BY id DESC LIMIT ?",
            (cid,scope,limit) if scope is not None else (cid,limit)).fetchall()
        return [{"scope": s, "question": q, "answer": json.loads(a)} for s, q, a in reversed(rows) if scope is None or s == scope]

    def append(self, cid, scope, question, answer):
        self.history(cid, limit=1)
        self.db.execute("INSERT INTO turns(conversation,scope,question,answer) VALUES(?,?,?,?)", (cid, scope, question, json.dumps(answer, ensure_ascii=False)))
        self.db.commit()

    def close(self):
        self.db.close()

    def pending(self,cid,scope):
        row = self.db.execute("SELECT scope,state FROM pending_research WHERE conversation=?",(cid,)).fetchone()
        return json.loads(row[1]) if row and (scope is None or row[0]==scope) else None

    def set_pending(self,cid,scope,state):
        self.history(cid,limit=1)
        self.db.execute("DELETE FROM pending_research WHERE conversation=?",(cid,))
        if state:
            self.db.execute("INSERT INTO pending_research VALUES(?,?,?)",(cid,scope,json.dumps(state,ensure_ascii=False)))
        self.db.commit()
