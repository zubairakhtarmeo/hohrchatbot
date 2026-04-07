import requests


URLS = (
    "http://localhost:11434/api/tags",
    "http://localhost:11434/api/generate",
    "http://localhost:11434/api/chat",
)


def main() -> None:
    for url in URLS:
        try:
            r = requests.get(url, timeout=5)
            print(url)
            print("  status:", r.status_code)
            body = (r.text or "").replace("\n", " ")
            print("  body:", body[:200])
        except Exception as e:
            print(url)
            print("  ERR:", e)


if __name__ == "__main__":
    main()
