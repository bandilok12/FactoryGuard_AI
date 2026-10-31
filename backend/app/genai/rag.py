"""Retrieval over maintenance manuals (TF-IDF + cosine similarity, scikit-learn)."""
import re
import threading
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from ..config import MANUALS_DIR

_lock = threading.Lock()
_index = {}


def _chunk_manual(path):
    text = path.read_text(encoding="utf-8")
    title_m = re.search(r"^#\s+(.+)$", text, re.M)
    title = title_m.group(1).strip() if title_m else path.stem
    mtype_m = re.search(r"^Machine type:\s*(.+)$", text, re.M)
    mtype = mtype_m.group(1).strip() if mtype_m else ""
    chunks = []
    # split on level-2/3 headings, keep heading with its body
    parts = re.split(r"(?m)^(?=##+\s)", text)
    for part in parts:
        part = part.strip()
        if not part or part.startswith("# "):
            continue
        head = part.splitlines()[0].lstrip("#").strip()
        body = part
        # very long sections -> split by blank-line paragraphs ~900 chars
        buf = ""
        for para in re.split(r"\n\s*\n", body):
            if len(buf) + len(para) > 1100 and buf:
                chunks.append({"manual": title, "file": path.name, "machine_type": mtype,
                               "section": head, "text": buf.strip()})
                buf = f"## {head} (cont.)\n"
            buf += para + "\n\n"
        if buf.strip():
            chunks.append({"manual": title, "file": path.name, "machine_type": mtype,
                           "section": head, "text": buf.strip()})
    return chunks


def build_index(force=False):
    with _lock:
        if _index and not force:
            return _index
        chunks = []
        for p in sorted(MANUALS_DIR.glob("*.md")):
            chunks.extend(_chunk_manual(p))
        vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True, min_df=1)
        docs = [f"{c['machine_type']} {c['section']} {c['text']}" for c in chunks]
        mat = vec.fit_transform(docs) if docs else None
        _index.update({"chunks": chunks, "vec": vec, "mat": mat})
        return _index


def search(query: str, machine_type: str | None = None, k: int = 4):
    idx = build_index()
    if not idx["chunks"]:
        return []
    q = query + (f" {machine_type}" if machine_type else "")
    sims = cosine_similarity(idx["vec"].transform([q]), idx["mat"]).ravel()
    if machine_type:   # restrict to this machine's manual + the general SOP (fall back if nothing matches)
        allowed = [c["machine_type"].lower() in (machine_type.lower(), "all") for c in idx["chunks"]]
        if any(a and sims[i] > 0.01 for i, a in enumerate(allowed)):
            sims = sims * allowed
    order = sims.argsort()[::-1][:k]
    return [{**idx["chunks"][i], "score": round(float(sims[i]), 4)} for i in order if sims[i] > 0.01]


def list_manuals():
    idx = build_index()
    seen = {}
    for c in idx["chunks"]:
        seen.setdefault(c["file"], {"file": c["file"], "title": c["manual"], "machine_type": c["machine_type"],
                                    "sections": 0})
        seen[c["file"]]["sections"] += 1
    return list(seen.values())
