from __future__ import annotations

import re

from hr_chatbot import HRChatbot


def main() -> None:
    bot = HRChatbot()

    source = "6. Business Travel Policy.pdf"
    try:
        res = bot.col.get(where={"source": source}, include=["documents", "metadatas"])
    except Exception as e:
        print("ERROR getting docs:", e)
        return

    docs = res.get("documents") or []
    metas = res.get("metadatas") or []
    print("chunks:", len(docs))

    patterns = [
        re.compile(r"\bcc\b", re.I),
        re.compile(r"\bkm\b", re.I),
        re.compile(r"per\s*km", re.I),
        re.compile(r"\b\d{1,3}\.\d+\b"),
        re.compile(r"\b(pkr|rs|rupees|₨)\b", re.I),
    ]

    for doc, meta in zip(docs, metas):
        text = (doc or "").strip()
        if not text:
            continue
        if not any(p.search(text) for p in patterns):
            continue
        chunk_i = meta.get("chunk_i")
        print("\n--- chunk", chunk_i, "---")
        print(text[:1200])


if __name__ == "__main__":
    main()
