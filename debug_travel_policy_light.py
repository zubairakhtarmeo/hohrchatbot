from __future__ import annotations

import os
import re

import chromadb


def main() -> None:
    base = os.path.dirname(os.path.abspath(__file__))
    db_dir = os.path.join(base, "Bot Files", "chroma_db")

    client = chromadb.PersistentClient(path=db_dir)

    # Collection name should match hr_chatbot.py; fall back to first collection if unknown.
    try:
        col = client.get_collection("hr_docs")
    except Exception:
        cols = client.list_collections()
        if not cols:
            raise SystemExit("No Chroma collections found")
        col = client.get_collection(cols[0].name)

    # Discover exact source names
    sample = col.get(limit=2000, include=["metadatas"])
    sources = sorted({(m or {}).get("source", "") for m in (sample.get("metadatas") or []) if (m or {}).get("source")})
    print("Sources (first 30):")
    for s in sources[:30]:
        print(" -", s)

    # Pick the Business Travel Policy source
    target = None
    for s in sources:
        if "business travel" in s.lower():
            target = s
            break
    if not target:
        raise SystemExit("Could not find a source containing 'business travel'")

    print("\nTarget source:", target)
    res = col.get(where={"source": target}, include=["documents", "metadatas"], limit=5000)
    docs = res.get("documents") or []
    metas = res.get("metadatas") or []
    print("Chunks:", len(docs))

    patterns = [
        re.compile(r"\bcc\b", re.I),
        re.compile(r"\bkm\b", re.I),
        re.compile(r"per\s*km", re.I),
        re.compile(r"\b\d{1,3}\.\d+\b"),
        re.compile(r"\b(pkr|rs|rupees|₨)\b", re.I),
        re.compile(r"\b(mileage|engine)\b", re.I),
    ]

    shown = 0
    for doc, meta in zip(docs, metas):
        text = (doc or "").strip()
        if not text:
            continue
        if not any(p.search(text) for p in patterns):
            continue
        print("\n--- chunk", (meta or {}).get("chunk_i"), "---")
        print(text[:1400])
        shown += 1
        if shown >= 8:
            break


if __name__ == "__main__":
    main()
