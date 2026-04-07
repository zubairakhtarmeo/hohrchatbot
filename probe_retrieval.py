from __future__ import annotations

from hr_chatbot import HRChatbot


def main() -> None:
    bot = HRChatbot()
    bot.index_all()

    queries = [
        "how much leave",
        "how much lesve",
        "chutti kitni hain",
        "leave policy",
        "annual leave",
        "sick leave",
    ]

    for q in queries:
        hits = bot._retrieve(q, k=8)
        print("\nQ:", q)
        print("hits:", len(hits))
        for h in hits[:6]:
            print(f" - {h['source']}  score={h['score']}  chunk={h['chunk']}")

    # End-to-end check (LLM answer grounded in policy)
    for q in ["how much leave do I get?", "chutti kitni hoti hai?"]:
        print("\n" + "=" * 80)
        print("CHAT Q:", q)
        ans = bot.chat(q, history=[])
        print(ans)


if __name__ == "__main__":
    main()
