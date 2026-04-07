"""
MG Apparel HR Chatbot — High-Accuracy Edition
Improvements:
  ✅ Stronger HR persona system prompt (acts like a real HR officer)
  ✅ Query expansion (synonyms + paraphrasing before search)
  ✅ Larger context window (TOP_K = 8, MAX_CTX = 10000)
  ✅ Relevance threshold raised (0.25) to filter noisy chunks
  ✅ Multi-query retrieval (searches 3 angles, deduplicates)
  ✅ Higher num_predict (600) so answers never cut off
  ✅ Groq max_tokens raised to 2048
  ✅ Smarter answer formatting with markdown
  ✅ Fallback gracefully when policy not found
  ✅ Conversation-aware (references prior turns properly)
Run locally : python hr_chatbot.py
Deploy cloud: Push to Railway / Render / Hugging Face Spaces
"""

import os, re, json, hashlib, threading, time, socket, atexit
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, make_response
from werkzeug.utils import secure_filename

# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION  — edit these or set as environment variables
# ═══════════════════════════════════════════════════════════════════════════════

# NOTE: Defaults are workspace-relative so the bot indexes the policies shipped
# with this repo. Override via HR_DATA_DIR / HR_BOT_DIR if needed.
BASE_DIR     = Path(__file__).resolve().parent
DATA_DIR     = os.getenv("HR_DATA_DIR", str(BASE_DIR / "Data"))
BOT_DIR      = os.getenv("HR_BOT_DIR",  str(BASE_DIR / "Bot Files"))
CHROMA_DIR   = os.path.join(BOT_DIR, "chroma_db")
PORT         = int(os.getenv("PORT", 5000))

# ── AI Backend ─────────────────────────────────────────────────────────────────
AI_BACKEND   = os.getenv("HR_AI_BACKEND", "ollama").lower()   # ollama | groq

# Ollama settings (local)
OLLAMA_URL   = os.getenv("OLLAMA_URL",    "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL",  "llama3.2:latest")

# Groq settings (cloud free tier)
GROQ_API_KEY = os.getenv("GROQ_API_KEY",  "")
GROQ_MODEL   = os.getenv("GROQ_MODEL",    "llama-3.3-70b-versatile")

# Master console credentials
MASTER_USER  = os.getenv("HR_MASTER_USER", "HR User")
MASTER_PASS  = os.getenv("HR_MASTER_PASS", "MGAHR")

MAX_UPLOAD_MB = int(os.getenv("HR_MAX_UPLOAD_MB", "25"))

# ── RAG tuning (IMPROVED) ──────────────────────────────────────────────────────
CHUNK_CHARS      = 1000   # slightly smaller chunks = more precise retrieval
CHUNK_OVERLAP    = 150    # overlap to avoid missing cross-boundary info
TOP_K_CHUNKS     = 8      # retrieve more chunks for better coverage
MAX_CTX_CHARS    = 10000  # larger context = more complete answers
RELEVANCE_THRESH = 0.25   # raised from 0.15 — filters irrelevant chunks
GEN_TIMEOUT      = 120    # seconds to wait for LLM
CHAT_TIMEOUT     = 360    # API window

COLLECTION_NAME  = os.getenv("HR_COLLECTION", "hr_docs_v4")

# ──────────────────────────────────────────────────────────────────────────────

import requests
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

try:    import fitz;                          PDF_OK   = True
except ImportError:                           PDF_OK   = False
try:    from docx import Document as DocxDoc; DOCX_OK  = True
except ImportError:                           DOCX_OK  = False
try:    import openpyxl;                      XLSX_OK  = True
except ImportError:                           XLSX_OK  = False

SUPPORTED_EXT = {".txt", ".md"}
if PDF_OK:   SUPPORTED_EXT.add(".pdf")
if DOCX_OK:  SUPPORTED_EXT.add(".docx")
if XLSX_OK:  SUPPORTED_EXT.update({".xlsx", ".xls"})

PUBLIC_URL    = None
SHARE_PROC    = None
_bot_instance = None
_chat_pool    = ThreadPoolExecutor(max_workers=4)


def _shutdown_chat_pool():
    try:
        _chat_pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


atexit.register(_shutdown_chat_pool)


# ═══════════════════════════════════════════════════════════════════════════════
#  QUERY EXPANSION — generates alternative search queries for better retrieval
# ═══════════════════════════════════════════════════════════════════════════════

HR_SYNONYMS = {
    "leave":         ["leave", "absence", "time off", "vacation", "holiday", "days off"],
    "annual":        ["annual", "yearly", "earned", "privilege"],
    "sick":          ["sick", "medical", "illness", "health", "unwell"],
    "casual":        ["casual", "short notice", "personal"],
    "salary":        ["salary", "pay", "wages", "compensation", "remuneration", "income"],
    "overtime":      ["overtime", "extra hours", "OT", "after hours", "additional hours"],
    "attendance":    ["attendance", "punctuality", "presence", "timing", "absenteeism"],
    "resign":        ["resign", "resignation", "quit", "leave the job", "notice period", "separation"],
    "notice":        ["notice period", "resignation notice", "last working day"],
    "grievance":     ["grievance", "complaint", "issue", "dispute", "concern", "problem"],
    "promotion":     ["promotion", "increment", "raise", "appraisal", "performance review", "growth"],
    "training":      ["training", "learning", "development", "course", "workshop", "skill"],
    "benefits":      ["benefits", "perks", "allowances", "entitlements", "facilities"],
    "loan":          ["loan", "advance", "financial assistance", "salary advance"],
    "maternity":     ["maternity", "pregnancy", "childbirth", "new mother"],
    "paternity":     ["paternity", "new father", "parental"],
    "harassment":    ["harassment", "bullying", "misconduct", "discrimination"],
    "code of conduct": ["code of conduct", "behavior", "ethics", "discipline", "rules"],
    "working hours": ["working hours", "shift", "office hours", "duty hours", "schedule"],
    "probation":     ["probation", "probationary period", "trial period", "new employee"],
    "contract":      ["contract", "appointment letter", "agreement", "terms of employment"],
    "deduction":     ["deduction", "fine", "penalty", "cut", "deducted"],
    "eobi":          ["EOBI", "provident fund", "pension", "social security"],
    "medical":       ["medical", "health insurance", "OPD", "hospitalization", "hospital"],
}

ROMAN_URDU_HINTS = {
    "leave": [
        "chutti", "chhutti", "chhuti", "chuti", "chhutiyaan", "chutiyan",
        "rukhsat", "rukhsati", "off", "vacation", "holiday",
        "lesve", "leav", "leave",
    ],
    "sick": ["bimari", "bimar", "tabiyat", "medical", "sick"],
    "casual": ["casual", "personal", "zaroorat", "urgent"],
    "attendance": ["hazri", "attendance", "late", "punctual", "timing"],
    "salary": ["tankhwa", "salary", "pay", "wages"],
    "overtime": ["overtime", "ot", "extra", "additional hours"],
    "resign": ["resign", "resignation", "istifa", "notice", "chorn"],
}

SOURCE_TOPIC_HINTS = {
    "leave": ["leave"],
    "overtime": ["overtime", "ot"],
    "attendance": ["attendance", "attendence", "punctual"],
    "shift": ["shift"],
    "separation": ["separation", "resignation", "exit"],
    "travel": ["travel"],
    "grievance": ["grievance"],
    "conduct": ["conduct"],
    "recruitment": ["recruitment", "hiring"],
    "pension": ["pension", "eobi", "provident"],
    "health": ["health", "medical"],
}


def _contains_any(text: str, needles: list[str]) -> bool:
    low = (text or "").lower()
    return any(n.lower() in low for n in needles)


def _roman_urdu_expansions(query: str) -> list[str]:
    """Adds a few English policy-focused variants for Roman Urdu / misspellings."""
    expansions: list[str] = []
    if _contains_any(query, ROMAN_URDU_HINTS["leave"]):
        expansions += [
            "leave policy",
            "how many leaves are allowed",
            "annual leave sick leave casual leave policy",
        ]
    if _contains_any(query, ROMAN_URDU_HINTS["sick"]):
        expansions += ["sick leave policy", "medical leave policy"]
    if _contains_any(query, ROMAN_URDU_HINTS["casual"]):
        expansions += ["casual leave policy"]
    if _contains_any(query, ROMAN_URDU_HINTS["attendance"]):
        expansions += ["attendance policy", "late coming policy"]
    if _contains_any(query, ROMAN_URDU_HINTS["salary"]):
        expansions += ["salary policy", "payroll policy"]
    if _contains_any(query, ROMAN_URDU_HINTS["overtime"]):
        expansions += ["overtime policy", "OT rules"]
    if _contains_any(query, ROMAN_URDU_HINTS["resign"]):
        expansions += ["resignation policy", "notice period policy"]
    # Deduplicate while preserving order
    out: list[str] = []
    seen = set()
    for e in expansions:
        if e not in seen:
            out.append(e)
            seen.add(e)
    return out


def _detect_topics(query: str) -> set[str]:
    topics: set[str] = set()
    if _contains_any(query, ROMAN_URDU_HINTS["leave"]):
        topics.add("leave")
    if _contains_any(query, ROMAN_URDU_HINTS["sick"]):
        topics.add("health")
        topics.add("leave")
    if _contains_any(query, ROMAN_URDU_HINTS["casual"]):
        topics.add("leave")
    if _contains_any(query, ROMAN_URDU_HINTS["attendance"]):
        topics.add("attendance")
    if _contains_any(query, ROMAN_URDU_HINTS["salary"]):
        topics.add("pension")
    if _contains_any(query, ROMAN_URDU_HINTS["overtime"]):
        topics.add("overtime")
    if _contains_any(query, ROMAN_URDU_HINTS["resign"]):
        topics.add("separation")
    # English keyword hints
    ql = (query or "").lower()
    if "shift" in ql:
        topics.add("shift")
    if "travel" in ql or "trip" in ql:
        topics.add("travel")
    if "griev" in ql or "complaint" in ql:
        topics.add("grievance")
    if "conduct" in ql or "discipline" in ql:
        topics.add("conduct")
    if "recruit" in ql or "hiring" in ql:
        topics.add("recruitment")
    return topics

def expand_query(query: str) -> list[str]:
    """
    Returns 3 search query variants for multi-angle retrieval.
    """
    q_lower = query.lower()
    queries = [query]  # original always included

    # Roman Urdu / misspelling expansions (helps retrieval find the right policy doc)
    for extra in _roman_urdu_expansions(query):
        if extra.lower() not in q_lower and extra not in queries:
            queries.append(extra)

    # Add synonym-based expansion
    for keyword, synonyms in HR_SYNONYMS.items():
        if keyword in q_lower:
            # Create an alternative with a synonym
            for syn in synonyms:
                if syn.lower() not in q_lower:
                    alt = re.sub(re.escape(keyword), syn, q_lower, flags=re.IGNORECASE, count=1)
                    if alt not in queries:
                        queries.append(alt)
                    if len(queries) >= 3:
                        break
        if len(queries) >= 3:
            break

    # If still only 1 query, add a rephrased version
    if len(queries) < 2:
        queries.append(f"policy regarding {query}")
    if len(queries) < 3:
        queries.append(f"MG Apparel {query} rules and procedure")

    return queries[:3]


# ═══════════════════════════════════════════════════════════════════════════════
#  CHATBOT ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class HRChatbot:

    def __init__(self):
        os.makedirs(CHROMA_DIR, exist_ok=True)
        os.makedirs(DATA_DIR,   exist_ok=True)

        print("[AI] Loading embedding model (first run downloads ~90 MB)…")
        try:
            self.embedder = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
            print("[AI] Embedding model loaded from local cache [OK]")
        except Exception:
            self.embedder = SentenceTransformer("all-MiniLM-L6-v2")
        print("[AI] Embedding model ready [OK]")

        self.chroma = chromadb.PersistentClient(
            path=CHROMA_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
        self.col = self.chroma.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        # Meta is per-collection to avoid stale skip logic after collection bumps.
        self._meta_path = os.path.join(CHROMA_DIR, f"meta_{COLLECTION_NAME}.json")
        self._meta: dict = self._load_meta()

        # If meta exists but collection is empty, force a rebuild.
        try:
            if self._meta and self.col.count() == 0:
                print("[Index] Meta found but collection is empty — forcing reindex.")
                self._meta = {}
                self._save_meta()
        except Exception:
            pass

        self.ollama_model = OLLAMA_MODEL

        if AI_BACKEND == "ollama":
            self.ollama_model = self._resolve_ollama_model(OLLAMA_MODEL)
            self._check_ollama()
        elif AI_BACKEND == "groq":
            self._check_groq()

    def _resolve_ollama_model(self, desired: str) -> str:
        """Pick an installed Ollama model tag; falls back safely if missing."""
        try:
            r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            r.raise_for_status()
            models = [m.get("name", "") for m in r.json().get("models", [])]
            models = [m for m in models if m]
        except Exception:
            return desired

        if not models:
            return desired

        if desired in models:
            return desired

        base = (desired.split(":", 1)[0] or "").strip()
        if base:
            # Prefer :latest if present; otherwise any tag that starts with the base.
            preferred = f"{base}:latest"
            if preferred in models:
                print(f"[Ollama] ⚠ Model '{desired}' not installed — using '{preferred}'.")
                return preferred
            for m in models:
                if m.startswith(base + ":") or m == base:
                    print(f"[Ollama] ⚠ Model '{desired}' not installed — using '{m}'.")
                    return m

        # Final fallback: first installed model
        print(f"[Ollama] ⚠ Model '{desired}' not installed — using '{models[0]}'.")
        return models[0]

    # ── Backend health checks ─────────────────────────────────────────────────

    def _check_ollama(self):
        try:
            r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            models = [m["name"] for m in r.json().get("models", [])]
            if self.ollama_model in models:
                print(f"[Ollama] '{self.ollama_model}' ready [OK]")
            else:
                print(f"[Ollama] ⚠ Model not found — run: ollama pull {self.ollama_model}")
        except Exception:
            print("[Ollama] ⚠ Not running. Start Ollama first.")

    def _check_groq(self):
        if not GROQ_API_KEY:
            print("[Groq] ⚠ GROQ_API_KEY not set. Get a free key at https://console.groq.com")
        else:
            print(f"[Groq] API key found. Model: {GROQ_MODEL} [OK]")

    # ── LLM generation ────────────────────────────────────────────────────────

    def _generate(self, system: str, user_prompt: str) -> str:
        if AI_BACKEND == "groq":
            return self._call_groq(system, user_prompt)
        return self._call_ollama(system, user_prompt)

    def _call_ollama(self, system: str, user_prompt: str) -> str:
        payload = {
            "model":     getattr(self, "ollama_model", OLLAMA_MODEL),
            "prompt":    user_prompt,
            "system":    system,
            "stream":    False,
            "keep_alive": "20m",
            "options": {
                "temperature": 0.15,
                "num_predict": 600,   # was 300 — now answers never cut off
                "num_ctx":     4096,  # was 2048 — larger context window
                "top_p":       0.9,
                "repeat_penalty": 1.1,
            },
        }
        try:
            r = requests.post(f"{OLLAMA_URL}/api/generate",
                              json=payload, timeout=(10, GEN_TIMEOUT))
            r.raise_for_status()
            data   = r.json()
            answer = (data.get("response") or "").strip()

            # Continue if cut off
            loops = 0
            while answer and loops < 2 and (
                data.get("done_reason") == "length" or self._looks_incomplete(answer)
            ):
                cont_payload = {
                    "model":  OLLAMA_MODEL,
                    "prompt": (
                        "Continue the HR answer from the exact next word. "
                        "Do NOT repeat any text already written.\n\n"
                        f"Text so far:\n{answer[-800:]}"
                    ),
                    "system":     system,
                    "stream":     False,
                    "keep_alive": "20m",
                    "options": {
                        "temperature": 0.15,
                        "num_predict": 200,
                        "num_ctx":     2048,
                        "top_p":       0.9,
                    },
                }
                try:
                    r2 = requests.post(f"{OLLAMA_URL}/api/generate",
                                       json=cont_payload, timeout=(10, GEN_TIMEOUT))
                    if not r2.ok:
                        break
                    data = r2.json()
                    tail = (data.get("response") or "").strip()
                    if not tail:
                        break
                    answer = f"{answer}\n\n{tail}".strip()
                    loops += 1
                except Exception:
                    break

            return self._clean_answer(answer)

        except requests.exceptions.ConnectionError:
            return "⚠️ Ollama is not running. Start it from your system tray or run `ollama serve`."
        except requests.exceptions.Timeout:
            retry_payload = {
                "model":  OLLAMA_MODEL,
                "prompt": user_prompt + "\n\nPlease give a concise but complete summary.",
                "system": system,
                "stream": False,
                "keep_alive": "20m",
                "options": {"temperature": 0.15, "num_predict": 350, "num_ctx": 2048},
            }
            try:
                r2 = requests.post(f"{OLLAMA_URL}/api/generate",
                                   json=retry_payload, timeout=(10, 45))
                r2.raise_for_status()
                retry_answer = (r2.json().get("response") or "").strip()
                if retry_answer:
                    return self._clean_answer(retry_answer)
            except Exception:
                pass
            return "⚠️ The assistant took too long. Please try again or ask for a shorter summary."
        except Exception as e:
            return f"⚠️ Ollama error: {e}"

    def _call_groq(self, system: str, user_prompt: str) -> str:
        if not GROQ_API_KEY:
            return "⚠️ GROQ_API_KEY is not set. Add it to your environment variables."
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type":  "application/json",
        }
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user_prompt},
            ],
            "temperature": 0.15,
            "max_tokens":  2048,   # was 1024 — doubled for complete answers
        }
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                              headers=headers, json=payload, timeout=(10, GEN_TIMEOUT))
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except requests.exceptions.Timeout:
            return "⚠️ Groq timed out. Please try again."
        except Exception as e:
            return f"⚠️ Groq error: {e}"

    def model_info(self) -> str:
        if AI_BACKEND == "groq":
            return f"Groq / {GROQ_MODEL}"
        try:
            r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
            models = [m["name"] for m in r.json().get("models", [])]
            model = getattr(self, "ollama_model", OLLAMA_MODEL)
            return f"Ollama / {model}" if models else "Ollama (offline)"
        except Exception:
            return "Ollama (not reachable)"

    # ── Cleaning ──────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_answer(text: str) -> str:
        if not text:
            return text
        # Remove leaked prompt artifacts
        text = re.sub(r"^\s*COMPANY HR POLICY DOCUMENTS:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*SOURCE DOCUMENT:.*$", "", text, flags=re.IGNORECASE | re.MULTILINE)
        text = re.sub(r"^={3,}.*$", "", text, flags=re.MULTILINE)
        text = text.replace('/";', '"').replace('";', '"')
        lines = [ln.rstrip() for ln in text.splitlines()]
        text  = "\n".join(lines)
        text  = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _looks_incomplete(text: str) -> bool:
        t = (text or "").strip()
        if not t or len(t) < 80:
            return False
        bad_endings = (",", ":", ";", "-", "(", "[", "{", "/", "and", "or", "with", "to", "of")
        low = t.lower()
        return (t[-1] not in ".!?" and any(low.endswith(x) for x in bad_endings)) or low.endswith("...")

    # ── Metadata ──────────────────────────────────────────────────────────────

    def _load_meta(self) -> dict:
        if os.path.exists(self._meta_path):
            try:
                return json.load(open(self._meta_path, encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_meta(self):
        json.dump(self._meta, open(self._meta_path, "w", encoding="utf-8"), indent=2)

    # ── Document reading ──────────────────────────────────────────────────────

    def _read_file(self, path: str) -> str:
        ext = Path(path).suffix.lower()
        try:
            if ext == ".pdf" and PDF_OK:
                doc  = fitz.open(path)
                text = "\n".join(p.get_text() for p in doc)
                doc.close()
                return text
            if ext == ".docx" and DOCX_OK:
                doc = DocxDoc(path)
                paragraphs = []
                for p in doc.paragraphs:
                    t = p.text.strip()
                    if t:
                        paragraphs.append(t)
                for table in doc.tables:
                    for row in table.rows:
                        cells = [c.text.strip() for c in row.cells if c.text.strip()]
                        if cells:
                            paragraphs.append(" | ".join(cells))
                return "\n".join(paragraphs)
            if ext in {".xlsx", ".xls"} and XLSX_OK:
                wb    = openpyxl.load_workbook(path, data_only=True)
                lines = []
                for ws in wb.worksheets:
                    lines.append(f"[Sheet: {ws.title}]")
                    for row in ws.iter_rows(values_only=True):
                        row_text = " | ".join(str(c) for c in row if c is not None)
                        if row_text.strip():
                            lines.append(row_text)
                return "\n".join(lines)
            return open(path, encoding="utf-8", errors="ignore").read()
        except Exception as e:
            print(f"[Read] Error reading {path}: {e}")
            return ""

    # ── Chunking ──────────────────────────────────────────────────────────────

    @staticmethod
    def _chunk_text(text: str) -> list[str]:
        text = text.strip()
        if not text:
            return []

        paragraphs = re.split(r"\n{2,}", text)
        chunks     = []
        current    = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current) + len(para) + 2 <= CHUNK_CHARS:
                current = (current + "\n\n" + para).strip()
            else:
                if current:
                    chunks.append(current)
                    overlap_prefix = current[-CHUNK_OVERLAP:] if len(current) > CHUNK_OVERLAP else current
                    current = (overlap_prefix + "\n\n" + para).strip()
                else:
                    current = para

        if current:
            chunks.append(current)

        return [c for c in chunks if len(c.strip()) > 60]

    # ── Indexing ──────────────────────────────────────────────────────────────

    def index_doc(self, path: str):
        ext = Path(path).suffix.lower()
        if ext not in SUPPORTED_EXT:
            return

        text = self._read_file(path)
        if not text.strip():
            print(f"[Index] No text extracted from: {path}")
            return

        name  = os.path.basename(path)
        fhash = hashlib.md5(text.encode()).hexdigest()

        # Skip only if BOTH:
        # - file hash unchanged
        # - chunks for this doc exist in the current collection
        if self._meta.get(name, {}).get("hash") == fhash:
            try:
                existing = self.col.get(where={"source": name}, include=[])
                if existing.get("ids"):
                    print(f"[Index] '{name}' unchanged — skipping.")
                    return
            except Exception:
                # If we can't verify existence, reindex to be safe.
                pass

        try:
            old = self.col.get(where={"source": name})
            if old["ids"]:
                self.col.delete(ids=old["ids"])
        except Exception:
            pass

        chunks = self._chunk_text(text)
        if not chunks:
            return

        print(f"[Index] Embedding '{name}' — {len(chunks)} chunks…")
        embeddings = self.embedder.encode(chunks, show_progress_bar=False).tolist()

        self.col.add(
            ids        = [f"{name}__c{i}" for i in range(len(chunks))],
            embeddings = embeddings,
            documents  = chunks,
            metadatas  = [{"source": name, "path": path, "chunk_i": i}
                          for i in range(len(chunks))],
        )

        self._meta[name] = {
            "hash":       fhash,
            "chunks":     len(chunks),
            "indexed_at": datetime.now().isoformat(),
            "path":       path,
        }
        self._save_meta()
        print(f"[Index] [OK] '{name}' — {len(chunks)} chunks stored.")

    def index_all(self):
        if not os.path.exists(DATA_DIR):
            print(f"[Index] Data folder not found: {DATA_DIR}")
            return
        files = []
        for root, _, fnames in os.walk(DATA_DIR):
            for fn in fnames:
                if Path(fn).suffix.lower() in SUPPORTED_EXT:
                    files.append(os.path.join(root, fn))

        print(f"[Index] Scanning {len(files)} file(s)…")
        for fp in files:
            self.index_doc(fp)
        print(f"[Index] Complete — {len(self._meta)} documents in knowledge base.")

    def remove_doc(self, name: str):
        try:
            old = self.col.get(where={"source": name})
            if old["ids"]:
                self.col.delete(ids=old["ids"])
        except Exception:
            pass
        self._meta.pop(name, None)
        self._save_meta()
        print(f"[Index] Removed '{name}'.")

    # ── Retrieval (IMPROVED: multi-query + deduplication) ─────────────────────

    def _retrieve_single(self, query: str, k: int) -> list[dict]:
        total = self.col.count()
        if total == 0:
            return []

        q_emb = self.embedder.encode([query]).tolist()
        res   = self.col.query(
            query_embeddings = q_emb,
            n_results        = min(k, total),
            include          = ["documents", "metadatas", "distances"],
        )

        hits = []
        for doc, meta, dist in zip(
            res["documents"][0],
            res["metadatas"][0],
            res["distances"][0],
        ):
            score = round(1.0 - float(dist), 4)
            if score >= RELEVANCE_THRESH:
                hits.append({
                    "text":   doc,
                    "source": meta.get("source", "Unknown"),
                    "score":  score,
                    "chunk":  meta.get("chunk_i", 0),
                })
        return hits

    def _retrieve_single_raw(self, query: str, k: int) -> list[dict]:
        """Unfiltered retrieval for fallback (returns top-k with scores)."""
        total = self.col.count()
        if total == 0:
            return []

        q_emb = self.embedder.encode([query]).tolist()
        res   = self.col.query(
            query_embeddings = q_emb,
            n_results        = min(k, total),
            include          = ["documents", "metadatas", "distances"],
        )

        hits: list[dict] = []
        for doc, meta, dist in zip(
            res["documents"][0],
            res["metadatas"][0],
            res["distances"][0],
        ):
            score = round(1.0 - float(dist), 4)
            hits.append({
                "text":   doc,
                "source": meta.get("source", "Unknown"),
                "score":  score,
                "chunk":  meta.get("chunk_i", 0),
            })
        hits.sort(key=lambda x: x["score"], reverse=True)
        return hits

    def _retrieve(self, query: str, k: int = TOP_K_CHUNKS) -> list[dict]:
        """
        Multi-query retrieval:
        - Generates 3 query variants via expand_query()
        - Searches each, deduplicates by text content
        - Returns top-k by relevance score
        """
        queries    = expand_query(query)
        seen_texts = set()
        all_hits   = []

        for q in queries:
            hits = self._retrieve_single(q, k)
            for h in hits:
                if h["text"] not in seen_texts:
                    seen_texts.add(h["text"])
                    all_hits.append(h)

        # If strict filtering returned nothing, progressively relax (Roman Urdu often needs this)
        if not all_hits:
            fallback_hits: list[dict] = []
            for q in queries:
                fallback_hits.extend(self._retrieve_single_raw(q, k))
            fallback_hits.sort(key=lambda x: x["score"], reverse=True)

            # Use a softer cutoff, but still avoid very low-similarity junk
            soft_min = 0.12
            best = fallback_hits[0]["score"] if fallback_hits else 0.0
            if best >= soft_min:
                for h in fallback_hits:
                    if h["score"] >= soft_min and h["text"] not in seen_texts:
                        seen_texts.add(h["text"])
                        all_hits.append(h)

            # Last resort: take top 2 if they are at least weakly related
            if not all_hits and best >= 0.08:
                for h in fallback_hits[:2]:
                    if h["text"] not in seen_texts:
                        seen_texts.add(h["text"])
                        all_hits.append(h)

        topics = _detect_topics(query)

        def _rank(h: dict) -> float:
            base = float(h.get("score") or 0.0)
            if not topics:
                return base
            src = str(h.get("source") or "").lower()
            bonus = 0.0
            for t in topics:
                for token in SOURCE_TOPIC_HINTS.get(t, []):
                    if token in src:
                        # Strongly prefer the policy file that matches the user's topic.
                        bonus = max(bonus, 0.18 if t == "leave" else 0.10)
                        break
            return base + bonus

        all_hits.sort(key=_rank, reverse=True)

        # If we can identify topic-matching policy files, filter out unrelated sources
        # so the LLM doesn't get distracted by irrelevant policies.
        if topics:
            def _source_matches_topics(h: dict) -> bool:
                src = str(h.get("source") or "").lower()
                for t in topics:
                    for token in SOURCE_TOPIC_HINTS.get(t, []):
                        if token in src:
                            return True
                return False

            topic_hits = [h for h in all_hits if _source_matches_topics(h)]
            if topic_hits:
                all_hits = topic_hits

        return all_hits[:k]

    # ── Context builder ───────────────────────────────────────────────────────

    def _build_context(self, hits: list[dict]) -> str:
        if not hits:
            return ""

        by_source: dict[str, list[str]] = {}
        for h in hits:
            src = h["source"]
            by_source.setdefault(src, []).append(h["text"])

        parts       = []
        total_chars = 0
        for src, texts in by_source.items():
            header = f"\n--- SOURCE: {src} ---\n"
            for text in texts:
                entry = header + text + "\n"
                if total_chars + len(entry) > MAX_CTX_CHARS:
                    remaining = MAX_CTX_CHARS - total_chars
                    if remaining > 200:
                        parts.append(header + text[:remaining] + "\n[...truncated]")
                        total_chars = MAX_CTX_CHARS
                    break
                parts.append(entry)
                total_chars += len(entry)
                header = ""
            if total_chars >= MAX_CTX_CHARS:
                break

        return "\n".join(parts)

    # ── Main chat method (IMPROVED SYSTEM PROMPT) ─────────────────────────────

    def chat(self, message: str, history: list[dict]) -> str:
        """
        1. Multi-query retrieval from ChromaDB
        2. Build context
        3. Strong HR persona prompt → LLM → clean response
        """
        # Step 1: Retrieve
        hits    = self._retrieve(message, k=TOP_K_CHUNKS)
        context = self._build_context(hits)

        # Step 2: Conversation history (last 6 turns)
        hist_parts = []
        for turn in history[-6:]:
            role    = "Employee" if turn.get("role") == "user" else "HR Officer"
            content = str(turn.get("content", "")).strip()
            if content:
                hist_parts.append(f"{role}: {content}")
        history_str = "\n".join(hist_parts)

        # ── Step 3: System prompt — acts like a real HR Officer ────────────────
        system = """You are Sara, the official HR Officer at MG Apparel — a professional, knowledgeable, and approachable human resources representative.

YOUR ROLE:
You help employees understand their rights, entitlements, and company policies based on MG Apparel's official HR policy documents.

HOW TO ANSWER:
1. ALWAYS read the provided policy documents carefully before answering.
2. Give a COMPLETE and ACCURATE answer. Never leave out eligibility criteria, entitlements, steps, timelines, or exceptions.
3. Format your answer like a real HR officer would:
   - Start with a direct answer to the question
   - Then provide supporting details (eligibility, process, conditions)
   - If there is a table or list of entitlements in the policy, reproduce it clearly
   - End with a helpful note if needed (e.g., "For further assistance, contact HR directly")
4. Always mention which policy document the information comes from (e.g., "As per the Leave Policy...").
5. If a question has multiple parts, answer ALL parts.
6. Use bullet points or numbered steps when explaining a process.
7. If the policy documents do NOT contain the answer, say:
   "This specific information is not covered in our current policy documents. I recommend reaching out to the HR department directly at [HR email/extension] for guidance on this matter."
8. NEVER make up information. NEVER guess. Only answer from what is in the documents.
9. Be warm, professional, and clear — like a real HR officer sitting across from the employee.
10. Do NOT output raw technical artifacts like "SOURCE DOCUMENT:", separator lines (===), or prompt blocks.
11. Keep your answers thorough but focused — typically 150 to 300 words, longer if the policy requires it.
12. Respond in the same language the employee uses (Urdu or English).

TONE: Professional, supportive, clear. Like an HR officer who genuinely wants to help."""

        # ── Step 4: User prompt ────────────────────────────────────────────────
        if context:
            user_prompt = f"""HR POLICY DOCUMENTS (official MG Apparel policies):
{context}

PREVIOUS CONVERSATION:
{history_str if history_str else "(This is the start of the conversation)"}

EMPLOYEE QUESTION:
{message}

Instructions: Read the policy documents above carefully. Give a complete, accurate, and well-structured HR answer. Include all relevant details: eligibility, entitlements, step-by-step process, timelines, conditions, and exceptions. Cite which document the information comes from. If anything is unclear in the documents, say so honestly."""

        else:
            user_prompt = f"""PREVIOUS CONVERSATION:
{history_str if history_str else "(This is the start of the conversation)"}

EMPLOYEE QUESTION:
{message}

Note: No matching policy documents were found for this question in our knowledge base.
Please respond as an HR officer would: acknowledge the question, provide general HR guidance if appropriate, and advise the employee to contact the HR department directly for company-specific details."""

        # Step 5: Generate
        return self._generate(system, user_prompt)

    # ── Document suggestions ──────────────────────────────────────────────────

    def suggest(self, doc_name: str) -> str:
        try:
            result = self.col.get(where={"source": doc_name}, include=["documents"])
            chunks = result.get("documents", [])
        except Exception:
            chunks = []

        if not chunks:
            return f"Document '{doc_name}' not found in the knowledge base."

        full_text = "\n\n".join(chunks)[:8000]
        system    = "You are a senior HR policy specialist with 15 years of experience in corporate HR, compliance, and policy writing."
        prompt    = f"""Please review the following MG Apparel HR policy document and provide detailed, actionable improvement suggestions.

Document Name: {doc_name}

Document Content:
{full_text}

Provide your review under these headings:
1. OVERALL ASSESSMENT — Is the policy clear, complete, and professionally written?
2. SPECIFIC ISSUES — List exact sections that are unclear, missing, or need updating
3. MISSING CONTENT — What important information should be added?
4. LANGUAGE & FORMATTING — How to improve readability and structure
5. COMPLIANCE & BEST PRACTICES — HR best-practice or legal considerations missing
6. RECOMMENDED REWRITES — Suggest improved wording for 2-3 key sections"""

        return self._generate(system, prompt)

    # ── List documents ────────────────────────────────────────────────────────

    def list_docs(self) -> list[dict]:
        return [
            {
                "name":       name,
                "chunks":     info["chunks"],
                "indexed_at": info["indexed_at"],
            }
            for name, info in self._meta.items()
        ]


# ═══════════════════════════════════════════════════════════════════════════════
#  FILE WATCHER
# ═══════════════════════════════════════════════════════════════════════════════

def start_watcher(bot: HRChatbot):
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        class Handler(FileSystemEventHandler):
            def _ok(self, p):
                return Path(p).suffix.lower() in SUPPORTED_EXT
            def on_created(self, e):
                if not e.is_directory and self._ok(e.src_path):
                    print(f"[Watcher] New file: {e.src_path}")
                    bot.index_doc(e.src_path)
            def on_modified(self, e):
                if not e.is_directory and self._ok(e.src_path):
                    print(f"[Watcher] Modified: {e.src_path}")
                    bot.index_doc(e.src_path)
            def on_deleted(self, e):
                if not e.is_directory and self._ok(e.src_path):
                    bot.remove_doc(Path(e.src_path).name)

        os.makedirs(DATA_DIR, exist_ok=True)
        observer = Observer()
        observer.schedule(Handler(), DATA_DIR, recursive=True)
        observer.start()
        print(f"[Watcher] Watching '{DATA_DIR}' in real-time [OK]")
        while True:
            time.sleep(5)
    except ImportError:
        print("[Watcher] watchdog not installed — polling every 60 s.")
        snapshot = {}
        while True:
            time.sleep(60)
            if not os.path.exists(DATA_DIR):
                continue
            current = {
                os.path.join(r, f): os.path.getmtime(os.path.join(r, f))
                for r, _, files in os.walk(DATA_DIR)
                for f in files
                if Path(f).suffix.lower() in SUPPORTED_EXT
            }
            for fp, mt in current.items():
                if fp not in snapshot or snapshot[fp] != mt:
                    bot.index_doc(fp)
            for fp in set(snapshot) - set(current):
                bot.remove_doc(Path(fp).name)
            snapshot = current


# ═══════════════════════════════════════════════════════════════════════════════
#  FLASK APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)


def get_bot() -> HRChatbot:
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = HRChatbot()
        _bot_instance.index_all()
        threading.Thread(target=start_watcher, args=(_bot_instance,), daemon=True).start()
    return _bot_instance


def is_master(req) -> bool:
    u = req.headers.get("X-Master-User", "").strip() or req.form.get("master_user", "").strip()
    p = req.headers.get("X-Master-Password", "").strip() or req.form.get("master_pass", "").strip()
    return u == MASTER_USER and p == MASTER_PASS


def lan_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if not ip.startswith("127.") else None
    except Exception:
        return None


# ── HTML ──────────────────────────────────────────────────────────────────────

EMPLOYEE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>MG Apparel — HR Assistant</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0a0c10;--surface:#10141c;--s2:#181d28;--border:#222a3a;
  --green:#22d3a5;--glow:rgba(34,211,165,.13);--warn:#f59e0b;
  --text:#dce6f5;--dim:#64748b;--bot:#111520;--user:#131a2a;
  --r:12px;--f:'DM Sans',sans-serif;--m:'DM Mono',monospace
}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--text);font-family:var(--f)}
body{display:flex;height:100vh}

/* SIDEBAR */
.sb{width:268px;min-width:268px;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.logo{padding:20px 18px 14px;border-bottom:1px solid var(--border)}
.logo-co{font-size:9.5px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);margin-bottom:4px}
.logo-t{font-size:17px;font-weight:700;display:flex;align-items:center;gap:9px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 10px var(--green);flex-shrink:0;animation:breathe 2.5s ease-in-out infinite}
@keyframes breathe{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(.85)}}
.badge{display:inline-flex;align-items:center;gap:5px;background:var(--glow);border:1px solid rgba(34,211,165,.25);color:var(--green);font-size:10px;font-weight:700;letter-spacing:.07em;padding:3px 9px;border-radius:99px;margin-top:8px;width:fit-content}
.nav{padding:10px 8px;display:flex;flex-direction:column;gap:3px}
.nb{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:9px;background:none;border:none;color:var(--dim);font-family:var(--f);font-size:13.5px;font-weight:500;cursor:pointer;width:100%;text-align:left;transition:all .14s}
.nb:hover{background:var(--s2);color:var(--text)}
.nb.on{background:var(--glow);color:var(--green)}
.sdocs{flex:1;overflow-y:auto;padding:10px;border-top:1px solid var(--border)}
.sdocs::-webkit-scrollbar{width:3px}
.sdocs::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.slbl{font-size:9.5px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);padding:0 5px 8px}
.sdi{display:flex;align-items:center;gap:7px;padding:6px 9px;border-radius:7px;color:var(--dim);font-size:12.5px;cursor:default;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:background .12s}
.sdi:hover{background:var(--s2)}
.cpill{margin-left:auto;flex-shrink:0;font-size:10px;font-family:var(--m);background:var(--s2);color:var(--dim);padding:1px 6px;border-radius:99px}
.minfo{padding:11px 15px;border-top:1px solid var(--border);font-size:11.5px;color:var(--dim)}
.mr{display:flex;justify-content:space-between;margin-bottom:3px}
.mv{color:var(--text);font-weight:600;font-family:var(--m);font-size:11px}

/* MAIN */
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.panel{display:none;flex:1;flex-direction:column;overflow:hidden}
.panel.on{display:flex}

/* CHAT */
.ctop{padding:14px 24px;border-bottom:1px solid var(--border);background:var(--surface);display:flex;align-items:center;gap:13px}
.cico{width:36px;height:36px;border-radius:9px;background:var(--glow);color:var(--green);display:flex;align-items:center;justify-content:center;font-size:17px}
.ctop h2{font-size:15px;font-weight:600}
.ctop p{font-size:11.5px;color:var(--dim);margin-top:2px}
.ctop-btns{margin-left:auto;display:flex;gap:8px}
.tbtn{padding:7px 13px;border-radius:7px;background:none;border:1px solid var(--border);color:var(--dim);font-size:12px;font-family:var(--f);cursor:pointer;transition:all .14s}
.tbtn:hover{border-color:var(--green);color:var(--green)}
.msgs{flex:1;overflow-y:auto;padding:24px 28px;display:flex;flex-direction:column;gap:20px}
.msgs::-webkit-scrollbar{width:4px}
.msgs::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
.msg{display:flex;gap:12px}
.msg.u{flex-direction:row-reverse;align-self:flex-end;max-width:75%}
.msg.b{align-self:flex-start;max-width:85%}
.av{width:34px;height:34px;border-radius:9px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:15px}
.msg.b .av{background:var(--glow)}
.msg.u .av{background:var(--s2)}
.bbl{padding:12px 16px;border-radius:12px;font-size:14px;line-height:1.75;word-break:break-word}
.msg.b .bbl{background:var(--bot);border:1px solid var(--border);border-top-left-radius:3px}
.msg.u .bbl{background:var(--user);border:1px solid #1e3055;border-top-right-radius:3px}
.bbl strong{color:var(--green);font-weight:600}
.bbl code{font-family:var(--m);font-size:12.5px;background:rgba(255,255,255,.05);padding:2px 5px;border-radius:4px}
.bbl ul,.bbl ol{padding-left:20px;margin:6px 0}
.bbl li{margin-bottom:4px}
.bbl h3,.bbl h4{color:var(--green);font-size:14px;margin:10px 0 4px}
.bbl blockquote{border-left:3px solid var(--green);padding-left:10px;color:var(--dim);margin:8px 0;font-style:italic}
.typing{display:flex;align-items:center;gap:5px;padding:12px 16px;background:var(--bot);border:1px solid var(--border);border-radius:12px;border-top-left-radius:3px}
.typing span{width:6px;height:6px;border-radius:50%;background:var(--dim);animation:blink 1.2s infinite}
.typing span:nth-child(2){animation-delay:.2s}
.typing span:nth-child(3){animation-delay:.4s}
@keyframes blink{0%,80%,100%{opacity:.25;transform:scale(.9)}40%{opacity:1;transform:scale(1)}}
.welcome{align-self:center;text-align:center;max-width:460px;padding:48px 20px}
.wi{font-size:54px;margin-bottom:18px}
.welcome h2{font-size:21px;font-weight:700;margin-bottom:9px}
.welcome p{font-size:13.5px;color:var(--dim);line-height:1.65}
.wtags{display:flex;flex-wrap:wrap;gap:7px;justify-content:center;margin-top:16px}
.wtag{padding:5px 12px;border-radius:99px;background:var(--s2);border:1px solid var(--border);color:var(--dim);font-size:11.5px}
.ibar{padding:14px 24px;background:var(--surface);border-top:1px solid var(--border)}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:10px}
.chip{padding:5px 12px;border-radius:99px;background:var(--s2);border:1px solid var(--border);color:var(--dim);font-size:12px;font-family:var(--f);cursor:pointer;transition:all .14s}
.chip:hover{border-color:var(--green);color:var(--green)}
.iwrap{display:flex;gap:10px;align-items:flex-end;background:var(--bg);border:1px solid var(--border);border-radius:var(--r);padding:10px 13px;transition:border-color .2s}
.iwrap:focus-within{border-color:var(--green)}
#inp{flex:1;background:none;border:none;color:var(--text);font-family:var(--f);font-size:14px;resize:none;outline:none;max-height:130px;line-height:1.5}
#inp::placeholder{color:var(--dim)}
.sbtn{width:36px;height:36px;border-radius:9px;background:var(--green);border:none;color:#0a0c10;cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:700;transition:opacity .14s,transform .1s}
.sbtn:hover{opacity:.85}
.sbtn:active{transform:scale(.93)}
.sbtn:disabled{opacity:.3;cursor:not-allowed}

/* DOCS PANEL */
.di{flex:1;overflow-y:auto;padding:28px}
.ptitle{font-size:22px;font-weight:700;margin-bottom:6px}
.psub{font-size:13.5px;color:var(--dim);margin-bottom:22px}
.dgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:13px}
.dc{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:17px;transition:all .14s}
.dc:hover{border-color:rgba(34,211,165,.35);transform:translateY(-2px);box-shadow:0 8px 28px rgba(0,0,0,.4)}
.dc-ico{font-size:26px;margin-bottom:11px}
.dc-nm{font-size:13.5px;font-weight:600;word-break:break-word;margin-bottom:5px}
.dc-mt{font-size:11.5px;color:var(--dim);font-family:var(--m)}
.dc-act{display:flex;gap:7px;margin-top:13px}
.dab{flex:1;padding:7px 0;border-radius:7px;background:var(--s2);border:1px solid var(--border);color:var(--dim);font-size:12px;font-family:var(--f);cursor:pointer;transition:all .14s}
.dab:hover{border-color:var(--green);color:var(--green)}

/* SUGGEST PANEL */
.si{flex:1;overflow-y:auto;padding:28px;max-width:820px}
.srow{display:flex;gap:10px;margin-bottom:18px}
.srow select{flex:1;background:var(--surface);border:1px solid var(--border);color:var(--text);font-family:var(--f);font-size:13.5px;border-radius:9px;padding:11px 13px;outline:none;appearance:none}
.srow button{padding:11px 22px;border-radius:9px;background:var(--green);border:none;color:#0a0c10;font-family:var(--f);font-size:13.5px;font-weight:700;cursor:pointer;transition:opacity .14s}
.srow button:hover{opacity:.85}
.srow button:disabled{opacity:.4;cursor:not-allowed}
#sout{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:20px;font-size:13.5px;line-height:1.75;white-space:pre-wrap;color:var(--text);min-height:120px}
#sout.dim{color:var(--dim)}

/* WARN BANNER */
.owarn{background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.28);border-radius:9px;padding:11px 15px;font-size:12.5px;color:var(--warn);margin:14px 24px;display:none}

/* TOAST */
.toast{position:fixed;bottom:22px;right:22px;background:var(--s2);border:1px solid var(--border);border-radius:10px;padding:10px 17px;font-size:13px;display:none;z-index:999;box-shadow:0 8px 32px rgba(0,0,0,.5);animation:pop .2s ease}
@keyframes pop{from{transform:translateY(10px);opacity:0}to{transform:translateY(0);opacity:1}}

@media(max-width:660px){.sb{width:54px;min-width:54px}.logo,.nb span,.sdocs,.minfo{display:none}.nb{justify-content:center;padding:10px}}
</style>
</head>
<body>
<aside class="sb">
  <div class="logo">
    <div class="logo-co">MG Apparel</div>
    <div class="logo-t"><span class="dot"></span>HR Assistant</div>
    <div class="badge">⬡ LOCAL AI · FREE</div>
  </div>
  <nav class="nav">
    <button class="nb on" onclick="pnl('chat',this)"><span>💬</span><span>Chat</span></button>
    <button class="nb" onclick="pnl('docs',this)"><span>📂</span><span>Documents</span></button>
    <button class="nb" onclick="pnl('suggest',this)"><span>✏️</span><span>Suggest Changes</span></button>
  </nav>
  <div class="sdocs">
    <div class="slbl">Indexed Files</div>
    <div id="slist"><div style="color:var(--dim);font-size:12px;padding:6px">Loading…</div></div>
  </div>
  <div class="minfo">
    <div class="mr"><span>Engine</span><span class="mv" id="mname">—</span></div>
    <div class="mr"><span>Docs</span><span class="mv" id="dcnt">—</span></div>
    <div class="mr"><span>Network</span><span class="mv" id="lanip" style="color:var(--green)">—</span></div>
  </div>
</aside>

<main class="main">
  <div class="owarn" id="owarn">⚠️ AI backend not reachable. Start Ollama or set your Groq API key.</div>

  <!-- CHAT PANEL -->
  <div class="panel on" id="panel-chat">
    <div class="ctop">
      <div class="cico">👩‍💼</div>
      <div>
        <h2>Sara — HR Officer</h2>
        <p>Ask me anything about MG Apparel HR policies. I'll give you accurate, complete answers.</p>
      </div>
      <div class="ctop-btns">
        <button class="tbtn" onclick="clearChat()">✕ Clear</button>
        <button class="tbtn" onclick="reindex()">↺ Re-index</button>
      </div>
    </div>

    <div class="msgs" id="msgs">
      <div class="welcome">
        <div class="wi">👩‍💼</div>
        <h2>Hello! I'm Sara, your HR Officer.</h2>
        <p>I can answer your questions about MG Apparel's HR policies — leave, attendance, benefits, salary, grievances, and more. Ask me anything!</p>
        <div class="wtags">
          <span class="wtag">Leave Policies</span>
          <span class="wtag">Attendance</span>
          <span class="wtag">Salary & Benefits</span>
          <span class="wtag">Grievances</span>
          <span class="wtag">Code of Conduct</span>
          <span class="wtag">Overtime</span>
        </div>
      </div>
    </div>

    <div class="ibar">
      <div class="chips">
        <button class="chip" onclick="ch(this)">How many annual leaves do I get?</button>
        <button class="chip" onclick="ch(this)">What is the complete leave policy?</button>
        <button class="chip" onclick="ch(this)">How do I apply for medical leave?</button>
        <button class="chip" onclick="ch(this)">What are the working hours?</button>
        <button class="chip" onclick="ch(this)">What is the overtime policy?</button>
        <button class="chip" onclick="ch(this)">How do I raise a grievance?</button>
      </div>
      <div class="iwrap">
        <textarea id="inp" rows="1" placeholder="Ask Sara your HR question…" oninput="rsz(this)" onkeydown="kd(event)"></textarea>
        <button class="sbtn" id="sb" onclick="snd()">➤</button>
      </div>
    </div>
  </div>

  <!-- DOCUMENTS PANEL -->
  <div class="panel" id="panel-docs">
    <div class="di">
      <div class="ptitle">Knowledge Base</div>
      <div class="psub">All HR documents currently indexed. Drop new files in your Data folder — they are auto-indexed within 60 seconds.</div>
      <div class="dgrid" id="dgrid">Loading…</div>
    </div>
  </div>

  <!-- SUGGEST PANEL -->
  <div class="panel" id="panel-suggest">
    <div class="si">
      <div class="ptitle">Document Review</div>
      <div class="psub">Select a policy document to get AI-powered improvement suggestions.</div>
      <div class="srow">
        <select id="dsel"><option>Loading…</option></select>
        <button id="abtn" onclick="analyse()">Analyse Document</button>
      </div>
      <div id="sout" class="dim">Select a document and click Analyse Document.</div>
    </div>
  </div>
</main>

<div class="toast" id="toast"></div>

<script>
let hist=[], busy=false;

function pnl(n,btn){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('on'));
  document.querySelectorAll('.nb').forEach(b=>b.classList.remove('on'));
  document.getElementById('panel-'+n).classList.add('on');
  btn.classList.add('on');
  if(n!=='chat') loadDocs();
}

function rsz(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,130)+'px'}
function kd(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();snd()}}
function ch(b){document.getElementById('inp').value=b.textContent;snd()}
function clearChat(){
  hist=[];
  const w=document.getElementById('msgs');
  w.innerHTML='<div class="welcome"><div class="wi">👩\u200d💼</div><h2>Chat cleared.</h2><p>Ask me anything about MG Apparel HR policies.</p></div>';
}

async function snd(){
  const inp=document.getElementById('inp'), msg=inp.value.trim();
  if(!msg||busy) return;
  document.querySelector('.welcome')?.remove();
  addMsg('u',msg); inp.value=''; inp.style.height='auto';
  hist.push({role:'user',content:msg});
  busy=true; document.getElementById('sb').disabled=true;
  const t=addTyping();
  try{
    let d=null, r=null;
    let ask=msg;
    for(let attempt=0; attempt<2; attempt++){
      r=await fetch('/api/chat',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({message:ask,history:hist.slice(-10)})});
      d=await r.json();
      if(r.ok) break;
      const err=(d&&d.error)||'';
      if(attempt===0 && /timed out|too long/i.test(err)){
        ask=msg+' Please answer concisely.';
        continue;
      }
      break;
    }
    t.remove();
    if(!r || !r.ok){
      addMsg('b',(d&&d.error)||'⚠️ Chat request failed. Please try again.');
      return;
    }
    const reply=(d&&d.response)||(d&&d.error)||'No response received.';
    addMsg('b',reply);
    hist.push({role:'assistant',content:reply});
  }catch(e){
    t.remove();
    addMsg('b','⚠️ Could not reach the server. Please check if the chatbot is running.');
  }
  busy=false; document.getElementById('sb').disabled=false;
}

function addMsg(role,text){
  const w=document.getElementById('msgs');
  const d=document.createElement('div');
  d.className='msg '+role;
  const avatar = role==='b' ? '👩\u200d💼' : '👤';
  d.innerHTML=`<div class="av">${avatar}</div><div class="bbl">${fmt(text)}</div>`;
  w.appendChild(d); w.scrollTop=w.scrollHeight; return d;
}

function addTyping(){
  const w=document.getElementById('msgs');
  const d=document.createElement('div');
  d.className='msg b';
  d.innerHTML='<div class="av">👩\u200d💼</div><div class="typing"><span></span><span></span><span></span></div>';
  w.appendChild(d); w.scrollTop=w.scrollHeight; return d;
}

function fmt(t){
  t=t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  // Bold
    t=t.replace(/\\*\\*(.*?)\\*\\*/g,'<strong>$1</strong>');
  t=t.replace(/__(.*?)__/g,'<strong>$1</strong>');
  // Italic
    t=t.replace(/\\*(.*?)\\*/g,'<em>$1</em>');
  // Code
  t=t.replace(/`(.*?)`/g,'<code>$1</code>');
  // Headings
  t=t.replace(/^### (.+)$/gm,'<h4>$1</h4>');
  t=t.replace(/^## (.+)$/gm,'<h3>$1</h3>');
  // Blockquote
  t=t.replace(/^&gt; (.+)$/gm,'<blockquote>$1</blockquote>');
    // Numbered lists — must come before bullet lists
    t=t.replace(/^\\d+\\.\\s+(.+)$/gm,'<li class="ol">$1</li>');
    // Bullet lists
    t=t.replace(/^[-•*]\\s+(.+)$/gm,'<li>$1</li>');
    // Wrap <li> groups in <ul>/<ol>
    t=t.replace(/(<li>[^]*?<\\/li>)/g,'<ul>$1</ul>');
    // Newlines
    t=t.replace(/\\n\\n/g,'<br><br>');
    t=t.replace(/\\n/g,'<br>');
  return t;
}

function ico(n){
  const e=(n.split('.').pop()||'').toLowerCase();
  return {pdf:'📄',docx:'📝',doc:'📝',xlsx:'📊',xls:'📊',txt:'📋',md:'📋'}[e]||'📁';
}

async function loadDocs(){
  try{
    const ctrl=new AbortController();
    const tm=setTimeout(()=>ctrl.abort(),12000);
    const r=await fetch('/api/documents',{signal:ctrl.signal});
    clearTimeout(tm);
    const d=await r.json(), docs=d.documents||[];
    document.getElementById('dcnt').textContent=docs.length;
    document.getElementById('slist').innerHTML=docs.length
      ? docs.map(doc=>`<div class="sdi">${ico(doc.name)}<span style="overflow:hidden;text-overflow:ellipsis">${doc.name}</span><span class="cpill">${doc.chunks}</span></div>`).join('')
      : '<div style="color:var(--dim);font-size:12px;padding:6px">No files indexed yet.</div>';
    document.getElementById('dgrid').innerHTML=docs.length
      ? docs.map(doc=>`
          <div class="dc">
            <div class="dc-ico">${ico(doc.name)}</div>
            <div class="dc-nm">${doc.name}</div>
            <div class="dc-mt">${doc.chunks} chunks · ${doc.indexed_at.slice(0,10)}</div>
            <div class="dc-act">
              <button class="dab" onclick="askDoc('${doc.name}')">Ask about</button>
              <button class="dab" onclick="revDoc('${doc.name}')">Review</button>
            </div>
          </div>`).join('')
      : '<p style="color:var(--dim);font-size:13px">No documents indexed yet.<br>Add files to your Data folder.</p>';
    const sel=document.getElementById('dsel');
    sel.innerHTML=docs.length
      ? docs.map(doc=>`<option value="${doc.name}">${doc.name}</option>`).join('')
      : '<option>No documents found</option>';
  }catch(e){
    console.error('loadDocs error:',e);
    document.getElementById('slist').innerHTML='<div style="color:var(--warn);font-size:12px;padding:6px">Unable to load files. Check server.</div>';
    document.getElementById('dgrid').innerHTML='<p style="color:var(--warn);font-size:13px">Unable to load documents right now.</p>';
    const sel=document.getElementById('dsel');
    sel.innerHTML='<option>Unable to load documents</option>';
  }
}

function askDoc(name){
  document.querySelectorAll('.nb')[0].click();
  document.getElementById('inp').value=`Explain the complete contents of: ${name}`;
  snd();
}

function revDoc(name){
  document.querySelectorAll('.nb')[2].click();
  setTimeout(()=>{
    document.getElementById('dsel').value=name;
    analyse();
  },150);
}

async function analyse(){
  const doc=document.getElementById('dsel').value;
  const out=document.getElementById('sout');
  const btn=document.getElementById('abtn');
  if(!doc||doc==='Loading\u2026'||doc==='No documents found') return;
  out.className='';
  out.textContent='\u23F3 Analysing document\u2026 this may take 30\u201390 seconds.';
  btn.disabled=true;
  try{
    const r=await fetch('/api/suggest',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({document:doc})});
    const d=await r.json();
    out.textContent=d.suggestions||'No suggestions returned.';
  }catch{
    out.textContent='\u26A0\uFE0F Error connecting to server.';
  }
  btn.disabled=false;
}

async function reindex(){
  toast('\u23F3 Re-indexing all documents\u2026');
  try{
    const r=await fetch('/api/reindex',{
      method:'POST',
      headers:{'X-Master-User': prompt('Master User ID:'), 'X-Master-Password': prompt('Master Password:')}
    });
    const d=await r.json();
    if(d.error){toast('\u26A0\uFE0F '+d.error);return;}
    toast(`\u2705 Done \u2014 ${d.count} documents indexed.`);
    loadDocs();
  }catch{toast('\u26A0\uFE0F Reindex failed.')}
}

async function checkStatus(){
  try{
    const ctrl=new AbortController();
    const tm=setTimeout(()=>ctrl.abort(),8000);
    const r=await fetch('/api/status',{signal:ctrl.signal});
    clearTimeout(tm);
    const d=await r.json();
    document.getElementById('mname').textContent=d.model||'\u2014';
    document.getElementById('dcnt').textContent=d.documents;
    document.getElementById('lanip').textContent=d.lan_url||'localhost';
    if(d.warn) document.getElementById('owarn').style.display='block';
  }catch{
    document.getElementById('mname').textContent='offline';
    document.getElementById('dcnt').textContent='\u2014';
    document.getElementById('lanip').textContent='unreachable';
    document.getElementById('owarn').style.display='block';
  }
}

function toast(msg){
  const t=document.getElementById('toast');
  t.textContent=msg; t.style.display='block';
  clearTimeout(t._t); t._t=setTimeout(()=>t.style.display='none',4000);
}

checkStatus(); loadDocs(); setInterval(loadDocs,30000);
</script>
</body>
</html>"""


MASTER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>HR Master Console</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:Segoe UI,Arial,sans-serif;background:#0e1117;color:#e5e7eb;padding:24px}
    .card{max-width:820px;margin:0 auto;background:#161b22;border:1px solid #2d333b;border-radius:12px;padding:24px}
    h1{font-size:22px;margin-bottom:6px}
    p{color:#9ca3af;font-size:13.5px;margin-bottom:16px}
    label{font-size:12px;color:#9ca3af;display:block;margin-bottom:4px}
    input,select{padding:10px 12px;border-radius:8px;border:1px solid #374151;background:#0f172a;color:#e5e7eb;width:100%;margin-bottom:12px;font-size:13.5px}
    .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    .btn{padding:11px 20px;border-radius:9px;border:none;font-size:14px;font-weight:600;cursor:pointer;transition:opacity .15s}
    .btn-green{background:#10b981;color:#052e16}.btn-green:hover{opacity:.85}
    .btn-blue{background:#3b82f6;color:#eff6ff}.btn-blue:hover{opacity:.85}
    .btn-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
    #out{white-space:pre-wrap;background:#0b1220;border:1px solid #243043;border-radius:8px;padding:14px;min-height:100px;font-size:13.5px;font-family:monospace;color:#d1fae5}
    a{color:#22d3ee}
    .section{margin-top:20px;padding-top:16px;border-top:1px solid #1f2937}
    .badge{display:inline-block;background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.3);color:#10b981;padding:3px 10px;border-radius:99px;font-size:11px;font-weight:700;margin-bottom:12px}
  </style>
</head>
<body>
<div class="card">
  <h1>🔧 HR Master Console</h1>
  <p>Admin area — for HR team only. Employees should use the <a href="/">main chat interface</a>.</p>
  <div class="badge">Requires Master Credentials</div>

  <div class="row">
    <div>
      <label>Master User ID</label>
      <input id="uid" type="text" placeholder="HR User"/>
    </div>
    <div>
      <label>Master Password</label>
      <input id="pwd" type="password" placeholder="Password"/>
    </div>
  </div>

  <div class="section">
    <p><strong>Upload Document</strong> — Upload a policy file directly from your browser.</p>
    <input id="file" type="file" accept=".txt,.md,.pdf,.docx,.xlsx,.xls"/>
    <div class="btn-row">
      <button class="btn btn-green" onclick="upload()">📤 Upload & Index</button>
      <button class="btn btn-blue" onclick="reindex()">↺ Re-index All</button>
      <button class="btn btn-blue" onclick="listDocs()">📋 List Documents</button>
    </div>
  </div>

  <label>Output</label>
  <div id="out">Ready. Enter credentials and use the buttons above.</div>
</div>

<script>
  function out(t){document.getElementById('out').textContent=typeof t==='string'?t:JSON.stringify(t,null,2)}
  function auth(){return{user:document.getElementById('uid').value.trim(),pass:document.getElementById('pwd').value.trim()}}

  async function upload(){
    const f=document.getElementById('file').files[0];
    if(!f){out('Please select a file first.');return;}
    const a=auth();
    if(!a.user||!a.pass){out('Enter credentials first.');return;}
    out('Uploading and indexing…');
    const fd=new FormData(); fd.append('file',f);
    try{
      const r=await fetch('/api/upload',{method:'POST',headers:{'X-Master-User':a.user,'X-Master-Password':a.pass},body:fd});
      out(await r.json());
    }catch(e){out('Error: '+e.message)}
  }

  async function reindex(){
    const a=auth();
    if(!a.user||!a.pass){out('Enter credentials first.');return;}
    out('Re-indexing all documents… please wait.');
    try{
      const r=await fetch('/api/reindex',{method:'POST',headers:{'X-Master-User':a.user,'X-Master-Password':a.pass}});
      out(await r.json());
    }catch(e){out('Error: '+e.message)}
  }

  async function listDocs(){
    try{
      const r=await fetch('/api/documents');
      const d=await r.json();
      out((d.documents||[]).map((x,i)=>`${i+1}. ${x.name} (${x.chunks} chunks, indexed ${x.indexed_at.slice(0,10)})`).join('\\n')||'No documents indexed.');
    }catch(e){out('Error: '+e.message)}
  }
</script>
</body>
</html>"""


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    resp = make_response(EMPLOYEE_HTML)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"]  = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/master")
def master():
    resp = make_response(MASTER_HTML)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"]  = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/chat", methods=["POST"])
def api_chat():
    d   = request.get_json(silent=True) or {}
    msg = (d.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "Empty message"}), 400
    bot     = get_bot()
    history = d.get("history", [])
    try:
        fut      = _chat_pool.submit(bot.chat, msg, history)
        response = fut.result(timeout=CHAT_TIMEOUT)
        return jsonify({"response": response})
    except FuturesTimeout:
        return jsonify({
            "error": "The assistant is still processing. Please retry, or ask for a shorter answer."
        }), 504


@app.route("/api/suggest", methods=["POST"])
def api_suggest():
    d    = request.get_json(silent=True) or {}
    name = (d.get("document") or "").strip()
    if not name:
        return jsonify({"error": "No document name"}), 400
    return jsonify({"suggestions": get_bot().suggest(name)})


@app.route("/api/documents")
def api_documents():
    return jsonify({"documents": get_bot().list_docs()})


@app.route("/api/reindex", methods=["POST"])
def api_reindex():
    if not is_master(request):
        return jsonify({"error": "Master credentials required"}), 403
    bot = get_bot()
    bot.index_all()
    return jsonify({"status": "ok", "count": len(bot.list_docs())})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if not is_master(request):
        return jsonify({"error": "Master credentials required"}), 403
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"error": "No file selected"}), 400
    safe = secure_filename(f.filename)
    ext  = Path(safe).suffix.lower()
    if ext not in SUPPORTED_EXT:
        return jsonify({"error": f"Unsupported type: {ext}. Allowed: {', '.join(sorted(SUPPORTED_EXT))}"}), 400
    data = f.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        return jsonify({"error": f"File too large (max {MAX_UPLOAD_MB} MB)"}), 400
    dest = os.path.join(DATA_DIR, safe)
    with open(dest, "wb") as fh:
        fh.write(data)
    bot = get_bot()
    bot.index_doc(dest)
    return jsonify({"status": "ok", "file": safe, "total_docs": len(bot.list_docs())})


@app.route("/api/status")
def api_status():
    bot = get_bot()
    ip  = lan_ip()
    return jsonify({
        "status":    "online",
        "documents": len(bot.list_docs()),
        "model":     bot.model_info(),
        "backend":   AI_BACKEND,
        "lan_url":   f"http://{ip}:{PORT}" if ip else None,
        "warn":      False,
    })


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 64)
    print("  MG Apparel HR Chatbot  |  High-Accuracy Edition")
    print("=" * 64)
    print(f"  AI Backend  : {AI_BACKEND.upper()}")
    if AI_BACKEND == "ollama":
        print(f"  Model       : {OLLAMA_MODEL}")
    else:
        print(f"  Model       : {GROQ_MODEL} (Groq cloud — free)")
    print(f"  Data folder : {DATA_DIR}")
    print(f"  Vector DB   : {CHROMA_DIR}")
    print(f"  Web UI      : http://localhost:{PORT}")
    print(f"  Master UI   : http://localhost:{PORT}/master")
    ip = lan_ip()
    if ip:
        print(f"  LAN URL     : http://{ip}:{PORT}  (share with employees)")
    print("=" * 64)

    bot = get_bot()

    import webbrowser
    threading.Timer(2.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
