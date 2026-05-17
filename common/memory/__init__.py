"""Lightweight persistent memory store for PM agents.

Uses TF-IDF cosine similarity over a sqlite3 backing store.
No extra dependencies — only numpy (already installed).

Each PM gets its own db at pm_<id>/state/memory.db.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

_APP_ROOT = Path(__file__).resolve().parent.parent.parent
_STORES: dict[str, "MemoryStore"] = {}


def get_store(pm_id: str) -> "MemoryStore":
    if pm_id not in _STORES:
        _STORES[pm_id] = MemoryStore(pm_id)
    return _STORES[pm_id]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class MemoryStore:
    def __init__(self, pm_id: str):
        self.pm_id = pm_id
        db_path = _APP_ROOT / f"pm_{pm_id}" / "state" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                content TEXT,
                tokens TEXT,
                ts REAL
            )
        """)
        self._conn.commit()

    def put(self, key: str, content: str) -> None:
        tokens = json.dumps(_tokenize(content))
        self._conn.execute(
            "INSERT OR REPLACE INTO memories (key, content, tokens, ts) VALUES (?,?,?,?)",
            (key, content, tokens, time.time()),
        )
        self._conn.commit()

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT key, content, tokens FROM memories ORDER BY ts DESC LIMIT 200"
        ).fetchall()
        if not rows:
            return []

        q_tokens = _tokenize(query)
        if not q_tokens:
            return [{"key": r[0], "content": r[1]} for r in rows[:top_k]]

        # Build IDF over corpus
        df: Counter = Counter()
        docs = []
        for _, _, tok_json in rows:
            toks = set(json.loads(tok_json))
            docs.append(toks)
            df.update(toks)

        N = len(docs)
        vocab = sorted(df.keys())
        # +1 smoothing so single-doc corpus doesn't collapse to zero
        idf = {t: math.log((N + 1) / (df[t] + 1)) + 1.0 for t in vocab}

        def tfidf(tokens_set: set) -> np.ndarray:
            vec = np.array([idf.get(t, 1.0) * (1 if t in tokens_set else 0) for t in vocab])
            norm = np.linalg.norm(vec)
            return vec / norm if norm > 0 else vec

        q_vec = tfidf(set(q_tokens))
        scored = []
        for i, (key, content, _) in enumerate(rows):
            d_vec = tfidf(docs[i])
            score = float(np.dot(q_vec, d_vec))
            scored.append((score, key, content))

        scored.sort(reverse=True)
        return [{"key": k, "content": c, "score": s} for s, k, c in scored[:top_k] if s > 0]

    def list_keys(self) -> list[str]:
        rows = self._conn.execute("SELECT key FROM memories ORDER BY ts DESC").fetchall()
        return [r[0] for r in rows]
