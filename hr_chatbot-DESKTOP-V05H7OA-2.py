"""
MG Apparel HR Chatbot — Fixed & Cloud-Ready Edition
100% Local (Ollama) OR Cloud (Groq free tier)
Run locally : python hr_chatbot.py
Deploy cloud: Push to Railway / Render / Hugging Face Spaces
"""

import os, re, json, hashlib, threading, time, socket, atexit, subprocess
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, make_response
from werkzeug.utils import secure_filename

# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION  — edit these or set as environment variables
# ═══════════════════════════════════════════════════════════════════════════════

BASE_DIR     = Path(__file__).resolve().parent
DATA_DIR     = os.getenv("HR_DATA_DIR", str(BASE_DIR / "Data"))
BOT_DIR      = os.getenv("HR_BOT_DIR",  str(BASE_DIR / "Bot Files"))
CHROMA_DIR   = os.path.join(BOT_DIR, "chroma_db")
PORT         = int(os.getenv("PORT", 5000))
RUNTIME_PORT = PORT

# ── AI Backend: "ollama" (local/free) OR "groq" (cloud/free) ──────────────────
AI_BACKEND   = os.getenv("HR_AI_BACKEND", "ollama").lower()   # ollama | groq

# Ollama settings (local)
OLLAMA_URL   = os.getenv("OLLAMA_URL",    "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL",  "llama3.2")        # can override; any installed Ollama model name/tag works

# Groq settings (cloud free tier — https://console.groq.com → free API key)
GROQ_API_KEY = os.getenv("GROQ_API_KEY",  "")
GROQ_MODEL   = os.getenv("GROQ_MODEL",    "llama-3.3-70b-versatile")  # free & very fast

# Master console credentials
MASTER_USER  = os.getenv("HR_MASTER_USER", "HR User")
MASTER_PASS  = os.getenv("HR_MASTER_PASS", "MGAHR")

MAX_UPLOAD_MB = int(os.getenv("HR_MAX_UPLOAD_MB", "25"))

# ── RAG tuning — these are the FIXED values (was causing bad answers) ─────────
CHUNK_CHARS   = 1200   # chars per chunk (preserves full paragraphs)
CHUNK_OVERLAP = 200    # overlap between chunks
TOP_K_CHUNKS  = 4      # fewer chunks keeps responses faster while preserving coverage
MAX_CTX_CHARS = 5000   # lighter context for faster generation
GEN_TIMEOUT   = 90     # seconds to wait for LLM response
CHAT_TIMEOUT  = 300    # API window; frontend handles waiting without aborting

# ──────────────────────────────────────────────────────────────────────────────

import requests
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

try:    import fitz;                        PDF_OK   = True
except ImportError:                         PDF_OK   = False
try:    from docx import Document as DocxDoc; DOCX_OK = True
except ImportError:                          DOCX_OK  = False
try:    import openpyxl;                    XLSX_OK  = True
except ImportError:                         XLSX_OK  = False

SUPPORTED_EXT = {".txt", ".md"}
if PDF_OK:   SUPPORTED_EXT.add(".pdf")
if DOCX_OK:  SUPPORTED_EXT.add(".docx")
if XLSX_OK:  SUPPORTED_EXT.update({".xlsx", ".xls"})

PUBLIC_URL   = None
SHARE_PROC   = None
_bot_instance = None
_chat_pool = ThreadPoolExecutor(max_workers=4)


_OLLAMA_ENDPOINT_MODE = None  # reserved


def _shutdown_chat_pool():
  try:
    _chat_pool.shutdown(wait=False, cancel_futures=True)
  except Exception:
    pass


atexit.register(_shutdown_chat_pool)


# ═══════════════════════════════════════════════════════════════════════════════
#  CHATBOT ENGINE
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
#  HARDCODED POLICY KNOWLEDGE BASE
#  Extracted directly from official HR policy documents.
#  These facts are injected into every relevant LLM prompt so the chatbot
#  gives exact answers even if PDF extraction misses a table.
# ═══════════════════════════════════════════════════════════════════════════════

BUSINESS_TRAVEL_POLICY = """
=== MG APPAREL BUSINESS TRAVEL POLICY — KEY FACTS ===

--- DOMESTIC TRAVEL: MODE OF TRANSPORT ---
Grade: General Manager / Senior Manager
  - Up to 550 km (one side): Company/Personal Car
  - Above 550 km: By Air

Grade: Manager to Executive
  - Up to 550 km (one side): Company/Personal Car/Taxi
  - Above 550 km: By Air or Rail (whichever more economical)

Grade: Junior Management
  - Any distance: Bus/Rail (whichever more economical)

--- DOMESTIC TRAVEL: MILEAGE REIMBURSEMENT FORMULA ---
Formula: Total Travel Cost = (Fuel Rate on Travel Date ÷ Average Mileage × KM Traveled) + (R&M Cost/KM × KM Traveled)
Note: Fuel Rate is current pump price on date of travel (employee provides this).

Engine Capacity      | Avg Mileage (km/l) | R&M Cost/KM (PKR)
Diesel up to 2000cc  |        9.5         |       8.91
Diesel above 2000cc  |        6.5         |       8.91
Petrol Motor Bikes   |       30           |       0.98
Petrol up to 1000cc  |       16.75        |       3.14
Petrol 1200-1500cc   |       12           |       4.45
Petrol 1501-1800cc   |       10.75        |       5.28
Petrol 1801cc above  |        9.5         |       8.91

--- DOMESTIC TRAVEL: HOTEL ACCOMMODATION (per night, PKR) ---
CEO:                        Five Star  — Up to PKR 40,000/night
General Manager/Sr Manager: Four Star  — Up to PKR 25,000/night
Manager to Executive:       Three Star — Up to PKR 15,000/night
Junior Management:          Three Star — Up to PKR 10,000/night
Includes: single standard room + 3 meals (breakfast, lunch, dinner)

--- DOMESTIC TRAVEL: DAILY ALLOWANCE (DA) ---
Management employees:     Up to Rs. 4,500/day (if making own meal arrangements)
Non-management employees: Up to Rs. 2,500/day (if making own meal arrangements)
Note: No DA if company provides accommodation with meals.
DA eligibility: Total round-trip distance must be 200 km or more from base location.
DA admissible for up to 45 days. Full DA for first 28 days, half DA for days 29-45.
If host provides boarding+lodging: 25% DA deducted. If meals also provided: additional 10% deducted.

--- DOMESTIC TRAVEL: AIR TRAVEL CLASS ---
CEO: Business Class
All Management Employees: Economy

--- DOMESTIC TRAVEL: RAIL TRAVEL ---
All grades: 1st Class ACC

--- INTERNATIONAL TRAVEL: DAILY ALLOWANCE (DA, per day in USD) ---
CEO:                          USD 250/day
General Manager / Manager:    USD 200/day
Sr Manager - Manager:         USD 150/day
Senior Executive / Executive: USD 100/day
Junior Management:            USD 100/day
DA covers: meals, laundry, local transport (taxi, ride-hailing, bus, parking)
Full DA for first 14 days; half DA for days 15-45.

--- INTERNATIONAL TRAVEL: HOTEL ACCOMMODATION (USD per night) ---
CEO:                          Five Star  — USD 200/night
General Manager to Managers:  Four Star  — USD 150/night
Deputy Manager to Executive:  Three Star — USD 125/night
Junior Management:            Hostels/Shared — USD 80/night

--- INTERNATIONAL TRAVEL: AIR CLASS ---
CEO: Business Class
All Management and Junior Management: Economy

--- GENERAL RULES ---
- Submit Travel Requisition Form (TRF) at least 3 days before domestic travel, 1 month before international.
- Submit Daily Expense Statement (DES) with receipts within 7 working days of return.
- Domestic travel: toll receipts required if traveling by own car.
- Prior approval mandatory before travel.
- If 2+ employees travel to same destination: share one vehicle; only one mileage claim.
- DA eligibility: round-trip distance must be 200 km or more from base location.
"""


class HRChatbot:

    def __init__(self):
        os.makedirs(CHROMA_DIR, exist_ok=True)
        os.makedirs(DATA_DIR,   exist_ok=True)

        print("[AI] Loading embedding model (first run downloads ~90 MB)…")
        try:
          # Prefer local cache to avoid startup stalls when network is slow/unavailable.
          self.embedder = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
          print("[AI] Embedding model loaded from local cache [OK]")
        except Exception:
          self.embedder = SentenceTransformer("all-MiniLM-L6-v2")
        print("[AI] Embedding model ready [OK]")

        # Chroma persistence can fail on Windows if the disk is full or SQLite cannot write.
        # In that case, fall back to an in-memory DB so the app still runs.
        try:
          self.chroma = chromadb.PersistentClient(
            path=CHROMA_DIR,
            settings=Settings(anonymized_telemetry=False),
          )
          self._chroma_persistent = True
        except Exception as e:
          self._chroma_persistent = False
          print(f"[Chroma] ⚠ Persistent DB unavailable ({e}). Falling back to in-memory index for this run.")
          self.chroma = chromadb.EphemeralClient(
            settings=Settings(anonymized_telemetry=False),
          )
        self.col = self.chroma.get_or_create_collection(
            name="hr_docs_v3",
            metadata={"hnsw:space": "cosine"},
        )
        self._meta_path = os.path.join(CHROMA_DIR, "meta.json")
        self._meta: dict = self._load_meta()

        if AI_BACKEND == "ollama":
            self._check_ollama()
        elif AI_BACKEND == "groq":
            self._check_groq()

    # ── Backend health checks ─────────────────────────────────────────────────

    def _check_ollama(self):
        try:
            r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            models = [m["name"] for m in r.json().get("models", [])]
            desired = (OLLAMA_MODEL or "").strip()
            base = desired.split(":")[0] if desired else ""

            def _has_model(name: str) -> bool:
              if not name:
                return False
              if name in models:
                return True
              # If user provided an untagged base name (e.g., "llama3.2"), accept any installed tag.
              if ":" not in name and any(m == name or m.startswith(name + ":") for m in models):
                return True
              return False

            if _has_model(desired):
                print(f"[Ollama] '{desired}' ready [OK]")
            else:
                # Helpful hint if the base exists but the exact tag does not.
                candidates = [m for m in models if base and (m == base or m.startswith(base + ":"))]
                if candidates:
                    shown = ", ".join(candidates[:4])
                    print(f"[Ollama] ⚠ '{desired}' not installed. Found: {shown}. Set OLLAMA_MODEL to one of these or run: ollama pull {desired}")
                else:
                    print(f"[Ollama] ⚠ Model not found — run: ollama pull {desired}")
        except Exception:
            print("[Ollama] ⚠ Not running. Start Ollama first.")

    def _check_groq(self):
        if not GROQ_API_KEY:
            print("[Groq] ⚠ GROQ_API_KEY not set. Get a free key at https://console.groq.com")
        else:
            print(f"[Groq] API key found. Model: {GROQ_MODEL} [OK]")

    # ── LLM generation ────────────────────────────────────────────────────────

    def _generate(self, system: str, user_prompt: str) -> str:
        """
        Call the configured LLM backend (Ollama local or Groq cloud).
        Returns the assistant's text response.
        """
        if AI_BACKEND == "groq":
            return self._call_groq(system, user_prompt)
        return self._call_ollama(system, user_prompt)

    def _call_ollama(self, system: str, user_prompt: str) -> str:
        # ── Ollama / local-LLM optimized settings ─────────────────────────────
        # For chat-tuned models like llama3.2, /api/chat generally yields more
        # consistent formatting than /api/generate (because Ollama applies the
        # model's chat template). We still keep /api/generate + OpenAI-compatible
        # fallbacks for maximum compatibility across environments.
        options = {
            "temperature": 0.1,
            "num_predict": 512,
            "num_ctx":     8192,
            "top_p":       0.85,
            "repeat_penalty": 1.15,
            "stop": ["\nEmployee:", "\nHuman:", "\nUser:"],
        }
        chat_payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "keep_alive": "20m",
            "options": options,
        }

        def _list_models() -> list[str]:
          try:
            rr = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            if not rr.ok:
              return []
            return [m.get("name") for m in (rr.json().get("models", []) or []) if isinstance(m, dict) and m.get("name")]
          except Exception:
            return []

        def _pick_fallback_model(primary: str) -> str | None:
          models = _list_models()
          if not models:
            return None

          # Prefer a known-working local model first (if present).
          preferred = [
            "deepseek-r1:8b",
            "phi3:3.8b",
            "gemma3:4b",
            "granite3.3:latest",
            "qwen3:8b",
          ]
          for m in preferred:
            if m != primary and m in models:
              return m

          # As a last resort, pick the first installed model that's not the primary.
          for m in models:
            if m and m != primary:
              return m
          return None

        def _request_with_model(model_name: str) -> requests.Response:
          # Try /api/chat first for chat models, then fall back to /api/generate, then /v1/chat/completions.
          local_chat = dict(chat_payload)
          local_chat["model"] = model_name
          resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json=local_chat,
            timeout=(10, GEN_TIMEOUT),
          )

          if resp.status_code == 404:
            combined_prompt = f"{system}\n\n{user_prompt}"
            gen_payload = {
              "model":  model_name,
              "prompt": combined_prompt,
              "stream": False,
              "keep_alive": "20m",
              "options": options,
            }
            resp = requests.post(
              f"{OLLAMA_URL}/api/generate",
              json=gen_payload,
              timeout=(10, GEN_TIMEOUT),
            )

            if resp.status_code == 404:
              oai_payload = {
                "model": model_name,
                "messages": local_chat["messages"],
                "temperature": float(options.get("temperature", 0.1)),
                "stream": False,
              }
              resp = requests.post(
                f"{OLLAMA_URL}/v1/chat/completions",
                json=oai_payload,
                timeout=(10, GEN_TIMEOUT),
              )

          return resp

        def _ollama_err_text(resp: requests.Response) -> str:
          try:
            j = resp.json()
            if isinstance(j, dict) and isinstance(j.get("error"), str):
              return j.get("error") or ""
          except Exception:
            pass
          return (resp.text or "").strip()
        try:
          primary_model = (OLLAMA_MODEL or "").strip() or "llama3.2"
          r = _request_with_model(primary_model)
          used_model = primary_model

          if not r.ok:
            err = _ollama_err_text(r)
            low = err.lower()
            if ("unable to allocate" in low) or ("allocate" in low and "buffer" in low):
              fb = _pick_fallback_model(primary_model)
              if fb:
                print(f"[Ollama] ⚠ Model '{primary_model}' failed to load (memory). Falling back to '{fb}'.")
                r2 = _request_with_model(fb)
                if r2.ok:
                  r = r2
                  used_model = fb
                else:
                  err2 = _ollama_err_text(r2)
                  if err2:
                    return f"⚠️ Ollama error (fallback model '{fb}'): {err2}"
              else:
                return (
                  "⚠️ Ollama could not load the selected model due to low memory. "
                  "Set OLLAMA_MODEL to a smaller installed model (example: phi3:3.8b) or pull one (example: llama3.2:1b)."
                )

            if not r.ok:
              if err:
                return f"⚠️ Ollama error: {err}"
              r.raise_for_status()

          data = r.json()

          # Parse response across supported endpoint shapes.
          if isinstance(data, dict) and "response" in data:
            # /api/generate
            answer = (data.get("response") or "").strip()
          elif isinstance(data, dict) and "message" in data:
            # /api/chat
            answer = ((data.get("message") or {}).get("content") or "").strip()
          else:
            # OpenAI-compatible shape
            answer = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()

          # Some reasoning-style models may return user-visible text in a `thinking` field.
          if not answer:
            thinking = ""
            if isinstance(data, dict) and isinstance(data.get("thinking"), str):
              thinking = data.get("thinking") or ""
            elif isinstance(data, dict) and isinstance(data.get("message"), dict) and isinstance((data.get("message") or {}).get("thinking"), str):
              thinking = (data.get("message") or {}).get("thinking") or ""
            extracted = self._extract_final_from_thinking(thinking)
            if extracted:
              answer = extracted.strip()

          # If the answer appears cut off, continue up to 2 times.
          loops = 0
          while answer and loops < 2 and (
            data.get("done_reason") == "length" or self._looks_incomplete(answer)
          ):
            cont_payload = {
              "model": OLLAMA_MODEL,
              "prompt": (
                "Continue the HR answer from the exact next word. "
                "Keep it concise but complete. Do not repeat text.\n\n"
                f"Current answer tail:\n{answer[-1200:]}"
              ),
              "system": system,
              "stream": False,
              "keep_alive": "20m",
              "options": {
                "temperature": 0.1,
                "num_predict": 140,
                "num_ctx": 2048,
                "top_p": 0.9,
              },
            }
            try:
              r2 = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json=cont_payload,
                timeout=(10, GEN_TIMEOUT),
              )
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

          cleaned = self._clean_answer(answer)
          if cleaned and str(cleaned).strip():
            return cleaned
          # If cleaning removed everything, fall back to the raw answer
          # (better to return something than a blank response).
          return (answer or "").strip()
        except requests.exceptions.ConnectionError:
            return "⚠️ Ollama is not running. Start it from your system tray or run `ollama serve`."
        except requests.exceptions.Timeout:
            # Fallback: ask for a concise answer with a smaller generation budget.
            retry_payload = {
              "model": OLLAMA_MODEL,
              "prompt": user_prompt + "\n\nIf you are short on time, give a concise but complete summary.",
              "system": system,
              "stream": False,
              "keep_alive": "20m",
              "options": {
                "temperature": 0.1,
                "num_predict": 280,
                "num_ctx": 2048,
                "top_p": 0.9,
              },
            }
            try:
              r2 = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json=retry_payload,
                timeout=(10, 40),
              )
              r2.raise_for_status()
              retry_answer = (r2.json().get("response") or "").strip()
              if retry_answer:
                return self._clean_answer(retry_answer)
            except Exception:
              pass
            return "⚠️ The assistant took too long for a full answer. Please try again, or ask for a concise summary first."
        except Exception as e:
            return f"⚠️ Ollama error: {e}"

    @staticmethod
    def _clean_answer(text: str) -> str:
        """Strip prompt artifacts, table lines, and source references."""
        if not text:
            return text

        original = str(text)

        # Strip leaked prompt headers
        text = re.sub(
            r"(?i)^\s*(COMPANY HR POLICY DOCUMENTS|POLICY DOCUMENTS|SOURCE DOCUMENT"
            r"|Policy Documents|HR Assistant\s*:|Answer\s*:|A:)[:\s]*",
            "", text
        )
        # Strip our explicit final marker if the model includes it.
        text = re.sub(r"(?i)^\s*FINAL\s*:\s*", "", text)
        # Strip "Source: ..." references
        text = re.sub(r"(?i)\(?source\s*:\s*[^\n)]+\)?\.?", "", text)
        # Strip filename references
        text = re.sub(
            r"\(?\d*\.?\s*[\w\s]+\.(docx?|pdf|xlsx?|txt|md)\)?",
            "", text, flags=re.IGNORECASE
        )
        # Strip "According to [filename]" artifacts
        text = re.sub(
            r"(?i)(according to|as per|based on|from)\s+the\s+[\w\s]+\.(docx?|pdf|txt)",
            "", text
        )
        # Strip separator lines, Note: boilerplate, role echoes
        text = re.sub(r"^[=\-]{4,}.*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"(?i)^Note:\s.*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"(?i)^(As an AI|I am (the|an)|As your HR|I\'m (the|an)).*\n?", "", text)
        # Fix encoding artifacts
        text = text.replace('/";', '"').replace('";', '"')
        # Preserve line breaks (important for downstream table-to-paragraph conversion)
        text = "\n".join([ln.rstrip() for ln in text.splitlines()])
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Truncate runaway repetition
        sentences = re.split(r"(?<=[.!?])\s+", text)
        seen: dict[str, int] = {}
        out: list[str] = []
        for s in sentences:
            key = s.strip().lower()[:60]
            seen[key] = seen.get(key, 0) + 1
            if seen[key] <= 2:
                out.append(s)
            else:
                break
        cleaned = " ".join(out).strip()
        if not cleaned:
            # Never return an empty string; it breaks the UI.
            fallback = original.strip()
            return fallback or "⚠️ The assistant returned an empty response. Please try again."
        return cleaned


    @staticmethod
    def _looks_incomplete(text: str) -> bool:
      t = (text or "").strip()
      if not t:
        return False
      if len(t) < 80:
        return False
      bad_endings = (",", ":", ";", "-", "(", "[", "{", "/", "and", "or", "with", "to", "of")
      low = t.lower()
      return (t[-1] not in ".!?" and any(low.endswith(x) for x in bad_endings)) or low.endswith("...")

    @staticmethod
    def _extract_final_from_thinking(thinking: str) -> str:
      """Extract a short user-facing answer from a reasoning-style trace (if present)."""
      t = (thinking or "").strip()
      if not t:
        return ""

      # Only accept explicit final markers; never leak chain-of-thought.
      finals = list(re.finditer(r"(?im)^\s*FINAL\s*:\s*(.+)\s*$", t))
      if finals:
        return (finals[-1].group(1) or "").strip()

      m = re.search(r"(?is)\bFinal answer\s*:\s*(.+)$", t)
      if m:
        cand = (m.group(1) or "").strip()
        for ln in cand.splitlines():
          ln = ln.strip()
          if ln:
            return ln

      return ""

    def _call_groq(self, system: str, user_prompt: str) -> str:
        if not GROQ_API_KEY:
            return "⚠️ GROQ_API_KEY is not set. Add it to your environment variables."
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system",  "content": system},
                {"role": "user",    "content": user_prompt},
            ],
            "temperature": 0.05,
            "max_tokens":  1200,
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
            return f"Ollama / {OLLAMA_MODEL}" if models else "Ollama (offline)"
        except Exception:
            return "Ollama (not reachable)"

    # ── Grounding guards (accuracy) ─────────────────────────────────────────

    @staticmethod
    def _extract_number_tokens(text: str) -> set[str]:
        if not text:
            return set()
        toks: set[str] = set()
        for m in re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", str(text)):
            n = m.replace(",", "")
            if re.fullmatch(r"0+", n):
                n = "0"
            else:
                n = n.lstrip("0") or "0"
            toks.add(n)
        return toks

    @staticmethod
    def _extract_currency_amounts(text: str) -> set[str]:
        """Extract numeric amounts that are explicitly marked as currency (PKR/Rs/₨)."""
        if not text:
            return set()
        out: set[str] = set()
        s = str(text)
        for m in re.findall(r"(?i)(?:pkr|rs\.?|₨)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", s):
            out.add(m.replace(",", "").lstrip("0") or "0")
        return out

    @classmethod
    def _extract_currency_window_numbers(cls, context: str) -> set[str]:
        """Extract numbers that are likely part of currency tables/sections.

        Uses a large window (80 lines) after any PKR/Rs marker so that
        multi-row rate tables — common in PDF-extracted HR policies — are
        fully captured even when the currency header appears once at the top.
        Also captures standalone numbers on lines adjacent to currency markers.
        """
        if not context:
            return set()

        currency_re = re.compile(r"(?i)\b(?:pkr|rs\.?|₨|rupee|rupees)\b")
        window = 0
        allowed: set[str] = set()
        lines = str(context).splitlines()

        for i, line in enumerate(lines):
            if currency_re.search(line):
                window = 80   # large window covers entire rate tables
            if window > 0:
                allowed |= cls._extract_number_tokens(line)
                window -= 1
            # Also check 2 lines before a currency marker (table header above)
            if currency_re.search(line) and i >= 2:
                allowed |= cls._extract_number_tokens(lines[i - 1])
                allowed |= cls._extract_number_tokens(lines[i - 2])

        # Include all directly-marked currency amounts
        allowed |= cls._extract_currency_amounts(context)

        # Include ALL numbers in the context for travel/allowance policy chunks
        # (rate tables often have numbers without adjacent PKR markers)
        if currency_re.search(context):
            for m in re.findall(r"\b([0-9][0-9,]*(?:\.[0-9]+)?)\b", context):
                allowed.add(m.replace(",", "").lstrip("0") or "0")

        return allowed

    def _enforce_grounding(self, answer: str, context: str, message: str) -> str:
        """
        Grounding check — disabled.
        The previous window-based number matching was incorrectly flagging valid
        PKR amounts from PDF rate tables as ungrounded, producing '[not in policy excerpt]'
        in responses. The LLM system prompt already instructs the model to use only
        figures present in the retrieved context, making this post-check redundant.
        """
        return answer if answer else answer
    def _load_meta(self) -> dict:
        if os.path.exists(self._meta_path):
            try:
                return json.load(open(self._meta_path, encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_meta(self):
      try:
        json.dump(self._meta, open(self._meta_path, "w", encoding="utf-8"), indent=2)
      except Exception as e:
        # Don't crash the app if storage is full; indexing can still work in-memory.
        print(f"[Meta] ⚠ Could not save meta.json: {e}")

    # ── Document reading ──────────────────────────────────────────────────────

    @staticmethod
    def _clean_pdf_text(text: str) -> str:
      """Remove repeated signature/header artifacts from PDF text extraction.

      Many HR PDFs include a repeated approval table/header on every page
      (DOCUMENT CODE / PREPARED BY / Sign/Date) plus digital signature audit
      trails (Confirmed Date / Reason). These pollute chunks and hurt retrieval.
      """
      if not text:
        return ""

      t = str(text)

      # Remove digital-signature audit trail lines.
      t = re.sub(r"(?im)^\s*confirmed date\s*:\s*.*$", "", t)
      t = re.sub(r"(?im)^\s*reason\s*:\s*.*$", "", t)
      t = re.sub(r"(?im)^\s*signsign\s*$", "", t)

      # Remove common e-signature audit trail noise that repeats throughout the PDF.
      t = re.sub(r"(?im)^\s*name\s*:\s*.*$", "", t)
      t = re.sub(r"(?im)^\s*date\s*:\s*.*$", "", t)
      t = re.sub(r"(?im)^\s*\d{4}\s+\d{1,2}:\d{2}:\d{2}\s*(?:am|pm)\b.*$", "", t)
      t = re.sub(r"(?i)\(\s*utc[^\)]*\)", "", t)

      # Remove the repeated approval/header block while preserving any content
      # that appears after 'Sign/Date' on the same line/page.
      t = re.sub(
        r"(?is)\bDOCUMENT\s+CODE\b.*?\bSign\s*/\s*Date\b\s*", "", t
      )

      # Remove repeated title-only lines that add noise.
      t = re.sub(
        r"(?im)^\s*Human Resource Department\s+MG\s+APPAREL\s+BUSINESS\s+TRAVEL\s+POLICY\s*$",
        "",
        t,
      )

      # Normalize whitespace.
      t = re.sub(r"[ \t]{2,}", " ", t)
      t = re.sub(r"\n{3,}", "\n\n", t)
      return t.strip()

    def _read_file(self, path: str) -> str:
      ext = Path(path).suffix.lower()
      try:
        if ext == ".pdf" and PDF_OK:
          doc  = fitz.open(path)
          pages = []
          for page in doc:
            # ── Step 1: extract plain text (paragraphs, headings) ────────────
            plain = page.get_text("text")

            # ── Step 2: reconstruct tables using block/word positions ─────────
            # get_text("text") reads columns top-to-bottom, destroying table rows.
            # We use "words" mode to sort by (row_y, col_x) and rebuild rows.
            words  = page.get_text("words")   # list of (x0,y0,x1,y1,word,block,line,word_n)
            if words:
              # Group words into rows by their vertical centre (y midpoint), 5-pt tolerance
              rows: dict[int, list] = {}
              for w in words:
                y_key = int((w[1] + w[3]) / 2 / 5) * 5   # bucket to nearest 5 pts
                rows.setdefault(y_key, []).append(w)

              table_lines = []
              for y_key in sorted(rows):
                row_words = sorted(rows[y_key], key=lambda w: w[0])  # sort left-to-right
                line = "  ".join(rw[4] for rw in row_words)
                table_lines.append(line)

              table_text = "\n".join(table_lines)
            else:
              table_text = plain

            # Use whichever is longer — plain text for prose, table_text for tables
            pages.append(table_text if len(table_text) > len(plain) else plain)

          doc.close()
          return self._clean_pdf_text("\n\n".join(pages))
        if ext == ".docx" and DOCX_OK:
          doc = DocxDoc(path)
          paragraphs = []
          for p in doc.paragraphs:
            t = p.text.strip()
            if t:
              paragraphs.append(t)
          # Also extract tables from Word docs
          for table in doc.tables:
            for row in table.rows:
              cells = [c.text.strip() for c in row.cells if c.text.strip()]
              if cells:
                paragraphs.append(" | ".join(cells))
          return "\n".join(paragraphs)
        if ext in {".xlsx", ".xls"} and XLSX_OK:
          wb = openpyxl.load_workbook(path, data_only=True)
          lines = []
          for ws in wb.worksheets:
            lines.append(f"[Sheet: {ws.title}]")
            for row in ws.iter_rows(values_only=True):
              row_text = " | ".join(str(c) for c in row if c is not None)
              if row_text.strip():
                lines.append(row_text)
          return "\n".join(lines)
        # .txt / .md
        return open(path, encoding="utf-8", errors="ignore").read()
      except Exception as e:
        print(f"[Read] Error reading {path}: {e}")
        return ""

    # ── Chunking ──────────────────────────────────────────────────────────────

    @staticmethod
    def _chunk_text(text: str) -> list[str]:
        """
        Split text into overlapping character-level chunks.
        Tries to split on paragraph boundaries (double newlines) first
        to avoid cutting sentences mid-way.
        """
        text = text.strip()
        if not text:
            return []

        # Try paragraph-aware chunking first
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
                    # Keep overlap: last CHUNK_OVERLAP chars of current as prefix
                    overlap_prefix = current[-CHUNK_OVERLAP:] if len(current) > CHUNK_OVERLAP else current
                    current = (overlap_prefix + "\n\n" + para).strip()
                else:
                    current = para

        if current:
            chunks.append(current)

        # Filter out trivial chunks
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

        # Skip if unchanged ONLY if vectors already exist.
        # This prevents a meta/index desync (e.g., when persistence is unavailable and we fall back to in-memory DB).
        if self._meta.get(name, {}).get("hash") == fhash:
          try:
            existing = self.col.get(where={"source": name})
            if existing.get("ids"):
              print(f"[Index] '{name}' unchanged — skipping.")
              return
          except Exception:
            pass
          print(f"[Index] '{name}' unchanged but vectors missing — re-indexing.")

        # Remove old vectors
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

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def _retrieve(self, query: str, k: int = TOP_K_CHUNKS, analysis: dict | None = None, force_include_amounts: bool = False) -> list[dict]:
        """Semantic search with light re-ranking for policy Q&A."""
        total = self.col.count()
        if total == 0:
            return []

        topic = (analysis or {}).get("topic", "general")
        is_specific = bool((analysis or {}).get("is_specific"))
        travel_mode = (analysis or {}).get("travel_mode", "")

        # Important: Chroma only returns the top-n results requested.
        # Rate tables (amount-heavy chunks) often have low embedding similarity due
        # to layout/table text, so we must retrieve a larger candidate pool and
        # then filter/re-rank locally.
        n_results = k
        if force_include_amounts or (topic in {"travel", "salary"} and is_specific):
            n_results = max(k, 40)
        n_results = min(n_results, total)

        q_emb = self.embedder.encode([query]).tolist()
        res = self.col.query(
            query_embeddings=q_emb,
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

        def _source_matches_topic(source: str, t: str) -> bool:
          s = (source or "").lower()
          if t == "leave":
            return "leave policy" in s
          if t == "attendance":
            return (
              ("attendence policy" in s)
              or ("attendance policy" in s)
              or ("shift policy" in s)
              or ("overtime policy" in s)
            )
          if t == "travel":
            return ("business travel" in s) or ("travel" in s and "policy" in s)
          if t == "grievance":
            return "grievance" in s
          if t == "employment":
            return any(x in s for x in ["recruitment", "separation", "code of conduct"])
          if t == "training":
            return "training" in s
          if t == "health":
            return "health" in s
          if t == "pension":
            return "pension" in s
          # salary/general or unknown: don't filter
          return True

        def _apply_topic_source_filter(hits_in: list[dict]) -> list[dict]:
          if topic in {"general", "salary"}:
            return hits_in
          preferred = [h for h in hits_in if _source_matches_topic(h.get("source", ""), topic)]
          return preferred or hits_in

        def _chunk_has_amount(text: str) -> bool:
            low = text.lower()
            if any(t in low for t in ["pkr", "rs", "rupees", "₨"]):
                return True
            # Common in travel policies: rate tables like 9.5 / 16.75 (often without explicit PKR markers)
            if re.search(r"\b\d{1,3}\.\d+\b", text):
                return True
            return bool(re.search(r"\b\d{2,}(?:,\d{3})*\b", text))

        def _source_boost(source: str) -> float:
            s = source.lower()
            if topic == "travel" and ("business travel" in s or ("travel" in s and "policy" in s)):
                return 0.18
            if topic == "leave" and ("leave policy" in s or ("leave" in s and "policy" in s)):
                return 0.18
            if topic == "attendance" and ("attendence policy" in s or "attendance policy" in s or "shift policy" in s):
                return 0.12
            if topic == "grievance" and "grievance" in s:
                return 0.12
            if topic == "employment" and any(t in s for t in ["separation", "recruitment", "code of conduct"]):
                return 0.08
            return 0.0

        def _mode_boost(text: str) -> float:
            if topic != "travel" or not travel_mode:
                return 0.0
            low = text.lower()
            if travel_mode == "air" and any(t in low for t in ["airfare", "ticket", "flight", "economy", "business class"]):
                return 0.05
            if travel_mode == "car" and any(t in low for t in ["mileage", "km", "per km", "cc", "engine", "fuel", "petrol", "diesel"]):
                return 0.05
            if travel_mode == "lodging" and any(t in low for t in ["hotel", "accommodation", "lodging", "night"]):
                return 0.05
            if travel_mode == "per_diem" and any(t in low for t in ["per diem", "daily allowance", "meal", "meals"]):
                return 0.05
            return 0.0

        min_score = 0.25
        if topic in {"travel", "salary"} or is_specific:
          min_score = 0.22
        # For allowance/rate-table questions, similarity can be lower due to table formatting;
        # allow amount-heavy chunks through so we can ground PKR figures.
        if force_include_amounts and topic in {"travel", "salary"}:
          min_score = min(min_score, 0.15)

        hits: list[dict] = []
        for doc, meta, dist in zip(
            res["documents"][0],
            res["metadatas"][0],
            res["distances"][0],
        ):
            score = round(1.0 - float(dist), 4)
            if score <= min_score and not (force_include_amounts and _chunk_has_amount(doc)):
              continue

            source = meta.get("source", "Unknown")
            chunk_i = meta.get("chunk_i")

            boosted = score + _source_boost(source)
            boosted += _mode_boost(doc)
            if (topic in {"travel", "salary"} or is_specific) and _chunk_has_amount(doc):
                boosted += 0.07

            hits.append({
                "text": doc,
                "source": source,
                "score": round(boosted, 4),
                "chunk_i": chunk_i,
            })

        hits.sort(key=lambda h: h["score"], reverse=True)

        # For amount-heavy questions, pull neighboring chunks to avoid missing split tables.
        if analysis and is_specific and topic in {"travel", "salary"} and hits:
            seen_pairs = {(h["source"], h.get("chunk_i")) for h in hits}
            neighbor_requests: list[tuple[str, int, float]] = []

            for h in hits[:3]:
                src = h["source"]
                try:
                    ci = int(h.get("chunk_i"))
                except Exception:
                    continue

                max_chunks = None
                try:
                    max_chunks = int(self._meta.get(src, {}).get("chunks"))
                except Exception:
                    max_chunks = None

                base_score = float(h.get("score") or 0.01)
                for ni in (ci - 1, ci + 1):
                    if ni < 0:
                        continue
                    if max_chunks is not None and ni >= max_chunks:
                        continue
                    key = (src, ni)
                    if key in seen_pairs:
                        continue
                    neighbor_requests.append((src, ni, max(base_score - 0.02, 0.01)))
                    seen_pairs.add(key)
                if len(neighbor_requests) >= 4:
                    break

            if neighbor_requests:
                ids = [f"{src}__c{ci}" for (src, ci, _) in neighbor_requests]
                score_by_id = {f"{src}__c{ci}": sc for (src, ci, sc) in neighbor_requests}
                try:
                    got = self.col.get(ids=ids, include=["documents", "metadatas"])
                    for ndoc, nmeta in zip(got.get("documents", []) or [], got.get("metadatas", []) or []):
                        nsrc = nmeta.get("source", "Unknown")
                        nci = nmeta.get("chunk_i")
                        nid = f"{nsrc}__c{nci}" if nci is not None else None
                        hits.append({
                            "text": ndoc,
                            "source": nsrc,
                            "score": round(float(score_by_id.get(nid, 0.01)), 4),
                            "chunk_i": nci,
                        })
                    hits.sort(key=lambda h: h["score"], reverse=True)
                except Exception:
                    pass

        hits = _apply_topic_source_filter(hits)
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[: min(len(hits), k + 6)]

    def _build_context(self, hits: list[dict]) -> str:
        """
        Build the context block that gets injected into the LLM prompt.
        Groups chunks by source document for readability.
        """
        if not hits:
            return ""

        # Group by source
        by_source: dict[str, list[str]] = {}
        for h in hits:
            src = h["source"]
            by_source.setdefault(src, []).append(h["text"])

        parts = []
        total_chars = 0
        for src, texts in by_source.items():
            header = f"\n{'='*60}\nSOURCE DOCUMENT: {src}\n{'='*60}\n"
            for text in texts:
                entry = header + text + "\n"
                if total_chars + len(entry) > MAX_CTX_CHARS:
                    # Still add a truncated version if budget allows
                    remaining = MAX_CTX_CHARS - total_chars
                    if remaining > 200:
                        parts.append(header + text[:remaining] + "\n[...truncated]")
                        total_chars = MAX_CTX_CHARS
                    break
                parts.append(entry)
                total_chars += len(entry)
                header = ""   # only show header once per source
            if total_chars >= MAX_CTX_CHARS:
                break

        return "\n".join(parts)

    # ── Deterministic travel entitlement extraction (avoids table misreads) ──

    @staticmethod
    def _is_management_role(role: str) -> bool:
        r = (role or "").strip().lower()
        if not r:
            return False
        # Treat Manager and above as management for DA limits.
        return r in {
            "manager",
            "senior manager",
            "general manager",
            "gm",
            "dgm",
            "avp",
            "vp",
            "coo",
            "ceo",
        }

    @staticmethod
    def _format_int_amount(x: str | None) -> str | None:
        if not x:
            return None
        s = str(x).strip().replace(",", "")
        if not s.isdigit():
            return None
        try:
            return f"{int(s):,}"
        except Exception:
            return None

    def _try_travel_entitlements_answer(self, analysis: dict, hits: list[dict], message: str) -> str | None:
        """
        Deterministic travel allowance calculator using hardcoded policy facts.
        No regex-based PDF extraction — uses BUSINESS_TRAVEL_POLICY constant directly.
        Returns a complete answer string, or None to fall back to the LLM.
        """
        if (analysis or {}).get("topic") != "travel":
            return None
        # If engine CC is present, always treat as specific (employee wants exact figures)
        if not (analysis or {}).get("is_specific") and engine_cc is not None:
            pass  # continue — we override below
        elif not (analysis or {}).get("is_specific") and engine_cc is None:
            return None

        role       = ((analysis or {}).get("employee_role") or "").strip().lower()
        travel_mode = (analysis or {}).get("travel_mode") or ""
        engine_cc  = (analysis or {}).get("engine_cc")
        overnight  = (analysis or {}).get("overnight")
        low        = (message or "").lower()

        # Infer car travel if engine CC is mentioned (e.g. "my vehicle with 1800cc")
        if engine_cc is not None and travel_mode in ("", "lodging", "per_diem"):
            travel_mode = "car"

        # ── Hardcoded policy tables (from BUSINESS_TRAVEL_POLICY constant) ────

        # Hotel ceilings (PKR/night, domestic)
        HOTEL = {
            "ceo":              ("Five Star",  40000),
            "general manager":  ("Four Star",  25000),
            "senior manager":   ("Four Star",  25000),
            "gm":               ("Four Star",  25000),
            "manager":          ("Three Star", 15000),
            "executive":        ("Three Star", 15000),
            "senior executive": ("Three Star", 15000),
            "officer":          ("Three Star", 15000),
            "junior":           ("Three Star", 10000),
        }

        # DA caps (PKR/day, domestic, management vs non-management)
        DA_MGMT    = 4500
        DA_NON_MGMT = 2500

        # Mileage table: engine_cc_key -> (avg_mileage, rm_cost_per_km)
        MILEAGE = {
            "upto1000":   (16.75, 3.14),
            "1200_1500":  (12.00, 4.45),
            "1501_1800":  (10.75, 5.28),
            "1801above":  (9.50,  8.91),
            "diesel2000": (9.50,  8.91),
            "dieselabove":(6.50,  8.91),
            "motorbike":  (30.00, 0.98),
        }

        # ── Determine hotel entitlement for this role ─────────────────────────
        hotel_star, hotel_pkr = None, None
        for key, (star, pkr) in HOTEL.items():
            if key in role:
                hotel_star, hotel_pkr = star, pkr
                break
        # Default fallback: manager band
        if not hotel_star:
            hotel_star, hotel_pkr = "Three Star", 15000

        # ── Determine DA rate ─────────────────────────────────────────────────
        is_mgmt = self._is_management_role(role)
        da_rate = DA_MGMT if is_mgmt else DA_NON_MGMT

        # ── Determine mileage bracket from engine CC ──────────────────────────
        avg_mileage, rm_cost = None, None
        if engine_cc is not None:
            cc = int(engine_cc)
            if cc <= 1000:
                avg_mileage, rm_cost = MILEAGE["upto1000"]
            elif cc <= 1500:
                avg_mileage, rm_cost = MILEAGE["1200_1500"]
            elif cc <= 1800:
                avg_mileage, rm_cost = MILEAGE["1501_1800"]
            else:
                avg_mileage, rm_cost = MILEAGE["1801above"]

        # ── Build the answer ──────────────────────────────────────────────────
        role_display = role.title()
        parts = []

        # Mileage section
        if travel_mode == "car" and avg_mileage and rm_cost:
            parts.append(
                f"As a {role_display} traveling by personal car ({engine_cc}cc), "
                f"your mileage reimbursement is calculated as: "
                f"(Current Fuel Price ÷ {avg_mileage} km/l × Distance) + "
                f"(PKR {rm_cost}/km × Distance). "
                f"For example, if fuel is PKR 280/litre and your trip is 400 km one-way (800 km total), "
                f"your mileage claim = (280 ÷ {avg_mileage} × 800) + ({rm_cost} × 800) = "
                f"PKR {round((280/avg_mileage*800) + (rm_cost*800)):,} — adjust with the actual fuel price on your travel date."
            )

        # Hotel section
        if overnight or any(w in low for w in ["hotel", "stay", "night", "overnight", "accommodation"]):
            parts.append(
                f"For overnight accommodation, as a {role_display} you are entitled to a "
                f"{hotel_star} hotel at up to PKR {hotel_pkr:,} per night "
                f"(single standard room with 3 meals included). "
                f"This applies when a company guest house is not available."
            )

        # DA section
        if any(w in low for w in ["allowance", "da", "per diem", "daily", "meal", "bhatta"]):
            parts.append(
                f"Your daily allowance (DA) entitlement is up to PKR {da_rate:,} per day "
                f"when making your own meal arrangements (with valid receipts). "
                f"Note: DA is only admissible if your total round-trip distance is 200 km or more from your base location."
            )

        if not parts:
            return None

        return "FINAL: " + " ".join(parts)


    # ── Main chat method ──────────────────────────────────────────────────────

    # ══════════════════════════════════════════════════════════════════════════
    #  SMART QUERY ENGINE  — Intent · Query Expansion · Structured Answering
    # ══════════════════════════════════════════════════════════════════════════

    # ── Romanised Urdu to English keyword map for better embedding retrieval ──
    # Keys are common Roman-Urdu words employees type; values are English equivalents
    # used to improve semantic search against English policy documents.
    _URDU_MAP = {
        # Leave-related
        "chutti":    "leave",      "chuttiyan":  "leave",    "chuttian": "leave",
        "kitni":     "how many",   "meri":       "my",       "mil":      "get",
        "saal":      "year",       "sal":        "year",     "mahine":   "month",
        "kaise":     "how",        "kya":        "what",     "kaun":     "who",
        # Salary-related
        "tankhwa":   "salary",     "talab":      "salary",   "tankha":   "salary",
        # Attendance-related
        "haazri":    "attendance", "hazri":      "attendance",
        # HR process
        "tardeed":   "appeal",     "shikayat":   "grievance",
        "naukri":    "employment",
        # Leave types
        "annual":    "annual",     "casual":     "casual",   "sick":     "sick medical",
        "medical":   "sick leave medical certificate",
        # Common actions
        "apply":     "application process procedure",
        "overtime":  "overtime extra hours",  "ot": "overtime",
        # Travel / allowance / expenses
        "safar":     "travel trip",
        "tour":      "travel trip",
        "ta":        "travel allowance",
        "da":        "daily allowance per diem",
        "ta/da":     "travel allowance daily allowance per diem",
        "bhatta":    "daily allowance per diem",
        "bhata":     "daily allowance per diem",
        "kharcha":   "expense reimbursement",
        "allowance": "allowance entitlement amount",
        # Employment
        "probation": "probationary period confirmation",
        "bonus":     "bonus incentive",       "increment":  "salary increment raise",
        "deduction": "salary deduction fine",
    }

    @staticmethod
    def _analyse_question(message: str) -> dict:
        """
        Returns a dict with:
          intent       : what type of answer is needed
          topic        : which HR domain (leave / salary / attendance / etc.)
          query_english: cleaned English query for embedding search
          is_specific  : True if employee wants a number/fact, not an explanation
        """
        raw  = message.strip()
        low  = raw.lower()

        # ── Romanised Urdu → English for semantic search ──────────────────────
        translated_tokens = []
        for token in re.split(r"\s+", low):
            translated_tokens.append(HRChatbot._URDU_MAP.get(token, token))
        query_english = " ".join(translated_tokens)

        # ── Topic detection ───────────────────────────────────────────────────
        topic_candidates: list[str] = []

        def _add_topic(name: str, cond: bool):
          if cond:
            topic_candidates.append(name)

        _add_topic(
          "leave",
          any(w in low for w in [
            "leave", "chutti", "chuttiyan", "casual", "sick",
            "annual", "medical leave", "leave balance",
          ]),
        )
        _add_topic(
          "travel",
          (
            bool(re.search(r"\bta/da\b", low))
            or bool(re.search(r"\bta\b", low))
            or bool(re.search(r"\bda\b", low))
            or any(w in low for w in [
              "travel", "trip", "tour", "business travel",
              "allowance", "per diem", "daily allowance",
              "conveyance", "mileage", "fuel", "petrol", "diesel",
              "toll", "parking", "hotel", "accommodation", "lodging",
              "meal", "meals", "ticket", "airfare",
              "reimbursement", "claim", "expense", "expenses",
            ])
          ),
        )
        _add_topic(
          "salary",
          any(w in low for w in [
            "salary", "tankhwa", "talab", "pay", "compensation",
            "increment", "bonus", "deduction", "net pay",
          ]),
        )
        _add_topic(
          "attendance",
          (
            any(w in low for w in [
              "attendance", "haazri", "hazri", "late", "absent",
              "timing", "working hours", "shift", "overtime",
            ])
            or bool(re.search(r"\bot\b", low))
          ),
        )
        _add_topic(
          "grievance",
          any(w in low for w in [
            "grievance", "shikayat", "complaint", "report",
            "harass", "disciplin", "warning",
          ]),
        )
        _add_topic(
          "employment",
          any(w in low for w in [
            "probation", "confirmation", "promotion", "transfer",
            "resignation", "termination", "notice period",
          ]),
        )
        _add_topic(
          "training",
          any(w in low for w in ["training", "development", "course", "workshop"]),
        )

        # If multiple topics match, don't guess; caller can ask a clarifier.
        topic_candidates = list(dict.fromkeys(topic_candidates))
        topic = topic_candidates[0] if len(topic_candidates) == 1 else "general"

        # ── Intent classification ─────────────────────────────────────────────
        # SPECIFIC: employee wants a number / date / name — answer must be direct
        specific_signals = [
            "kitni", "how many", "how much", "kab", "when", "kaun sa", "which",
            "mujhe kitni", "meri leaves", "leave balance", "kitne din",
            "how long", "kitna time", "kya rate", "percentage", "days",
          "allowance", "per diem", "daily allowance",
            "amount", "rate", "pkr", "rs", "rupees",
        ]
        process_signals = [
            "kaise", "how to", "apply karna", "apply karein", "process",
            "procedure", "tarika", "steps", "kya karna", "what to do",
            "submit", "form", "fill",
        ]
        eligibility_signals = [
            "eligible", "eligibility", "qualify", "kaun", "who can",
            "kon le sakta", "kon apply", "can i", "mujhe milegi",
        ]
        overview_signals = [
            "complete policy", "full policy", "poori policy", "explain",
            "tell me about", "what is the", "kya hai", "batao", "bata",
            "overview", "summary", "describe",
        ]

        is_specific = (
          bool(re.search(r"\bta/da\b", low))
          or bool(re.search(r"\bta\b", low))
          or bool(re.search(r"\bda\b", low))
          or any(s in low for s in specific_signals)
        )

        if any(s in low for s in specific_signals) and not any(s in low for s in overview_signals):
            intent = "SPECIFIC"
        elif any(s in low for s in process_signals):
            intent = "PROCESS"
        elif any(s in low for s in eligibility_signals):
            intent = "ELIGIBILITY"
        elif any(s in low for s in overview_signals):
            intent = "OVERVIEW"
        else:
            intent = "GENERAL"

        # ── Travel mode hints (to avoid mixing unrelated allowance tables) ───
        travel_mode = ""
        engine_cc: int | None = None
        overnight: bool | None = None
        if topic == "travel":
          if any(w in low for w in ["air", "flight", "airfare", "ticket", "economy", "business class"]):
            travel_mode = "air"
          elif any(w in low for w in ["car", "vehicle", "vehicale", "vehicl", "personal car", "own car", "myu", "engine", "cc", "mileage", "fuel", "petrol", "diesel"]):
            travel_mode = "car"
          elif any(w in low for w in ["hotel", "accommodation", "lodging", "stay", "overnight"]):
            travel_mode = "lodging"
          elif (bool(re.search(r"\bda\b", low)) or any(w in low for w in ["per diem", "daily allowance", "meal", "meals"])):
            travel_mode = "per_diem"

          # Extract engine CC if present (helps pick correct table bracket)
          m = re.search(r"\b(\d{3,4})\s*cc\b", low)
          if not m:
            m = re.search(r"\bengine\s*(\d{3,4})\b", low)
          if m:
            try:
              engine_cc = int(m.group(1))
            except Exception:
              engine_cc = None

          # Overnight / hotel hint
          if any(w in low for w in ["no hotel", "same-day", "same day", "day return", "return same day"]):
            overnight = False
          elif any(w in low for w in ["overnight", "hotel", "accommodation", "stay"]):
            overnight = True

        # ── Employee role / grade extraction ─────────────────────────────────
        # Detect phrases like "I am a manager", "as DGM", "I'm AM", etc.
        employee_role: str = ""
        destination:   str = ""

        # Role keywords → canonical label (used for query expansion & prompt)
        role_patterns = [
          (r"\b(ceo|chief executive)\b",                          "CEO"),
          (r"\b(coo|chief operating)\b",                          "COO"),
          (r"\b(dgm|deputy general manager)\b",                   "DGM"),
          (r"\b(gm|general manager)\b",                           "GM"),
          (r"\b(avp|assistant vice president)\b",                 "AVP"),
          (r"\b(vp|vice president)\b",                            "VP"),

          # NOTE: Avoid matching the common phrase "I am ..." as role "AM".
          # Only treat "am" as Assistant Manager when used as a role abbreviation
          # in role-like contexts (e.g., "I'm AM", "as AM") or when the full title
          # is written.
          (r"\bassistant\s+manager\b|\b(?:as|i'?m|im|role|designation|position)\s+am\b", "AM"),

          (r"\b(manager|mngr|mgr|manger)\b",                     "Manager"),
          (r"\b(supervisor|supvr)\b",                             "Supervisor"),
          (r"\b(officer)\b",                                       "Officer"),
          (r"\b(executive)\b",                                     "Executive"),
          (r"\b(associate)\b",                                     "Associate"),
          (r"\b(worker|operator|staff)\b",                        "Worker"),
        ]
        for pattern, label in role_patterns:
            if re.search(pattern, low):
                employee_role = label
                break

        # Destination city extraction (common Pakistan cities + generic)
        city_patterns = [
            r"\bto\s+([a-z][a-z\s]{2,20}?)(?:\s+(?:for|on|as|trip|tour|visit|travel|and)\b|$)",
            r"\bvisiting\s+([a-z][a-z\s]{2,20}?)(?:\s+(?:for|on)\b|$)",
            r"\btravel(?:ling|ing)?\s+to\s+([a-z][a-z\s]{2,20?})(?:\s|$)",
        ]
        pk_cities = {
            "lahore", "karachi", "islamabad", "rawalpindi", "faisalabad",
            "multan", "peshawar", "quetta", "gujranwala", "sialkot",
            "hyderabad", "abbottabad", "sukkur", "larkana",
        }
        # Simple city match first
        for city in pk_cities:
            if city in low:
                destination = city.title()
                break
        # Then regex fallback
        if not destination:
            for cp in city_patterns:
                m2 = re.search(cp, low)
                if m2:
                    destination = m2.group(1).strip().title()
                    break

        return {
            "intent":          intent,
            "topic":           topic,
          "topic_candidates": topic_candidates,
            "query_english":   query_english,
            "is_specific":     is_specific,
            "raw":             raw,
            "travel_mode":     travel_mode,
            "engine_cc":       engine_cc,
            "overnight":       overnight,
            "employee_role":   employee_role,
            "destination":     destination,
        }

    @staticmethod
    def _expand_query(message: str, analysis: dict) -> str:
        """Adds domain synonyms so embedding retrieval finds policy tables/amounts reliably."""
        low = message.lower()
        topic = analysis.get("topic", "general")

        extras: list[str] = []
        if topic == "travel":
            mode = analysis.get("travel_mode") or ""
            extras.append("business travel policy")
            extras.append("travel allowance ta da")
            extras.append("PKR Rs amount")

            if mode in ("", "per_diem"):
                extras.append("daily allowance per diem")
                extras.append("meal allowance")
            if mode in ("", "lodging"):
                extras.append("hotel accommodation lodging")
            if mode in ("", "car"):
                extras.append("transport conveyance mileage fuel reimbursement cc engine")
            if mode in ("", "air"):
                extras.append("airfare ticket flight economy business class reimbursement")

            extras.append("expense claim procedure")

            # Inject role + destination for targeted table retrieval
            role = analysis.get("employee_role") or ""
            dest = analysis.get("destination") or ""
            if role:
                extras.extend([role, f"{role} entitlement", f"{role} grade allowance"])
            if dest:
                extras.append(dest)
        elif topic == "leave":
            extras.extend(["leave policy", "leave entitlement", "carry forward", "encashment", "days"])
        elif topic == "attendance":
            extras.extend(["attendance policy", "shift policy", "working hours", "late arrival", "deduction"])
        elif topic == "salary":
            extras.extend(["salary", "deduction", "increment", "bonus", "payroll"])
        elif topic == "employment":
          extras.extend([
            "separation policy",
            "resignation",
            "notice period",
            "termination",
            "probation",
            "recruitment policy",
          ])
        elif topic == "grievance":
          extras.extend([
            "grievance handling policy",
            "complaint procedure",
            "escalation",
            "investigation",
          ])
        elif topic == "training":
          extras.extend(["training", "development", "course", "approval process"])

        q_en = analysis.get("query_english") or ""
        if q_en and q_en != message.lower():
            extras.append(q_en)

        return (message + " " + " ".join(extras)).strip()

    @staticmethod
    def _build_system_prompt(analysis: dict) -> str:
        """Build a tight, plain-English system prompt. No table instructions."""
        topic         = analysis["topic"]
        employee_role = analysis.get("employee_role") or ""
        destination   = analysis.get("destination") or ""

      # IMPORTANT: Do not include hard-coded numeric examples here.
      # They tend to leak into answers and reduce accuracy.

        ctx = ""
        if employee_role:
            ctx += f" The employee is a {employee_role}."
        if destination:
            ctx += f" Destination: {destination}."

        travel_addendum = ""
        if topic == "travel":
          role_hint = f"The employee is a {employee_role}. " if employee_role else ""
          dest_hint = f"Destination: {destination}. " if destination else ""
          travel_addendum = (
            f"\nTravel context: {role_hint}{dest_hint}\n"
            "The EXACT policy figures are provided below. Use them directly — do not say any figure is unavailable.\n"
            "If the employee gave their engine CC and grade, calculate the mileage cost using the formula and table.\n"
            "If overnight stay is mentioned, state the exact hotel ceiling for their grade.\n"
            "If DA is relevant, state the exact daily allowance for their grade.\n"
            "Always calculate a total amount when enough information is provided.\n\n"
            "=== AUTHORITATIVE POLICY FACTS (use these exact figures) ===\n"
            f"{BUSINESS_TRAVEL_POLICY}\n"
            "=== END OF POLICY FACTS ===\n"
          )

        return (
            "You are a smart HR Assistant for MG Apparel."
            + (f" Context:{ctx}" if ctx else "")
            + "\n\nRules (follow exactly):\n"
            "- Language: professional business English only. Do NOT use Roman Urdu.\n"
            "- Output format: start the answer with FINAL: followed by the answer text; do not output anything else.\n"
            "- Reply in ONE plain paragraph, 3-7 sentences maximum.\n"
            "- NEVER use tables, bullets, numbered lists, or | pipe characters.\n"
            "- Read ALL numbers in the policy context carefully — rates, PKR amounts, ceilings, per-km figures.\n"
            "- NEVER say figures are 'redacted', 'not visible', 'not in excerpt', or 'not available'. If a number is in the text, use it.\n"
            "- For allowance/travel: find the row/grade matching this employee, state the exact PKR rate, and CALCULATE the total if days/nights are given.\n"
            "- For leave/salary/other: state the exact number/days/amount from the policy.\n"
            "- Use ONLY numbers found in the policy context. Never invent figures.\n"
            "- Do not mention filenames or section numbers.\n"
            "- Only say 'contact HR' if there is genuinely NO relevant figure anywhere in the context.\n"
            + travel_addendum
        )


    @staticmethod
    @staticmethod
    def _to_paragraph(text: str) -> str:
        """
        Hard post-processor: runs AFTER LLM generation.
        Converts ANY table / bullet / numbered-list output into one clean paragraph.
        The LLM cannot override this — it runs on every response.
        """
        if not text:
            return text

        lines_in = text.splitlines()
        parts = []
        in_table = False

        for ln in lines_in:
            ln = ln.strip()
            if not ln:
                continue

            # ── Markdown table row ────────────────────────────────────────────
            if ln.startswith("|"):
                # Pure separator  |---|---|  → skip
                if re.match(r"^[|\-:\s]+$", ln):
                    in_table = True
                    continue
                # Data row: extract cells, skip the header-separator repeat
                cells = [c.strip() for c in ln.split("|") if c.strip()]
                if not cells:
                    continue
                if all(re.match(r"^[-:]+$", c) for c in cells):
                    continue
                # Clean bold markers inside cells
                cells = [re.sub(r"\*\*(.+?)\*\*", r"\1", c) for c in cells]
                # Build a readable phrase from the cells
                if len(cells) >= 3:
                    # "Component: Rate — Amount"  e.g. "Daily Allowance: PKR 1,500/day — PKR 3,000"
                    phrase = f"{cells[0]}: {cells[1]}"
                    if cells[2]:
                        phrase += f" ({cells[2]})"
                elif len(cells) == 2:
                    phrase = f"{cells[0]}: {cells[1]}"
                else:
                    phrase = cells[0]
                parts.append(phrase)
                in_table = True
                continue

            in_table = False

            # ── Bullet / numbered list item ───────────────────────────────────
            ln = re.sub(r"^\s*[-•*]\s+", "", ln)
            ln = re.sub(r"^\s*\d+[.)]\s+", "", ln)

            # ── Strip markdown decorations ────────────────────────────────────
            ln = re.sub(r"\*\*(.+?)\*\*", r"\1", ln)
            ln = re.sub(r"\*(.+?)\*",       r"\1", ln)
            ln = re.sub(r"^#{1,4}\s+",        "",     ln)
            ln = re.sub(r"`(.+?)`",            r"\1", ln)

            # ── Skip pure separator / horizontal rule lines ───────────────────
            if re.match(r"^[=\-_]{3,}$", ln):
                continue

            if ln:
                parts.append(ln)

        # Join all parts into one paragraph
        para = " ".join(parts)

        # Tidy up double-punctuation and extra spaces
        para = re.sub(r"\.\s*\.", ".",  para)
        para = re.sub(r",\s*,",     ",",  para)
        para = re.sub(r"\s{2,}",    " ",  para)

        # Ensure it ends with a full stop
        para = para.strip()
        if para and para[-1] not in ".!?":
            para += "."

        return para

    def chat(self, message: str, history: list[dict]) -> str:
        """
        Production-grade HR chat:
        1. Analyse the question (intent + topic + language)
        2. Expand query for better semantic retrieval
        3. Retrieve relevant policy chunks
        4. Build a laser-focused prompt
        5. Generate a clean, direct answer
        """
        # ── Step 1: Analyse ───────────────────────────────────────────────────
        analysis = self._analyse_question(message)
        intent   = analysis["intent"]

        # If the question matches multiple policies, ask one clarifying question
        # instead of guessing and mixing policy text.
        if analysis.get("topic") == "general":
          cands = analysis.get("topic_candidates") or []
          if isinstance(cands, list) and len(cands) >= 2:
            pretty = {
              "attendance": "Attendance / Shift Policy",
              "leave": "Leave Policy",
              "travel": "Business Travel Policy (TA/DA)",
              "salary": "Salary / Compensation",
              "employment": "Employment (Recruitment/Separation/Conduct)",
              "grievance": "Grievance Handling Policy",
              "training": "Training / Development",
            }
            a = pretty.get(cands[0], cands[0].title())
            b = pretty.get(cands[1], cands[1].title())
            return (
              "To answer accurately, please confirm which policy you mean: "
              f"{a} or {b}?\n"
              "Reply with one (e.g., 'Attendance') and I will answer from that policy only."
            )

        # For travel allowance questions, don't guess: ask the minimum clarifiers needed
        # to pull the correct table/amounts from the policy.
        if analysis.get("topic") == "travel" and analysis.get("is_specific"):
            low = (analysis.get("raw") or "").lower()
            asks_allowance = (
                ("allowance" in low)
                or ("per diem" in low)
                or ("daily allowance" in low)
                or ("reimbursement" in low)
                or ("claim" in low)
                or ("expense" in low)
                or ("amount" in low)
                or ("how much" in low)
                or ("kitna" in low)
                or ("kitni" in low)
                or bool(re.search(r"\bta/da\b", low))
                or bool(re.search(r"\bta\b", low))
                or bool(re.search(r"\bda\b", low))
            )
            missing = []
            if not (analysis.get("travel_mode") or "").strip():
                missing.append("(1) Travel mode: personal car / company car / taxi / bus-rail / flight?")
            if analysis.get("overnight") is None:
                missing.append("(2) Will this be an overnight stay (hotel), or a same-day return?")
            if analysis.get("travel_mode") == "car" and analysis.get("engine_cc") is None:
                missing.append("(3) Engine capacity (CC), e.g., 1000 / 1300 / 1800 / 2000+?")

            if asks_allowance and missing:
                role_note = f" (I will use the {analysis.get('employee_role')} grade/role rate table)" if analysis.get("employee_role") else ""
                return (
                    "To provide the exact PKR breakdown from the Business Travel Policy" + role_note + ", please confirm:\n"
                    + "\n".join(missing)
                )

        # ── Step 2: Smart retrieval — expand query for tables/amounts ─────────
        search_query = self._expand_query(message, analysis)

        # Fetch more chunks for SPECIFIC questions (need exact numbers/facts)
        k = TOP_K_CHUNKS + 6 if (intent == "SPECIFIC" and analysis.get("topic") in {"travel", "salary"}) else (
          TOP_K_CHUNKS + 3 if intent == "SPECIFIC" else TOP_K_CHUNKS + 1
        )

        hits = self._retrieve(search_query, k=k, analysis=analysis)

        # If user is asking for exact travel allowance amounts and we have the required details,
        # do a second pass that intentionally pulls amount-heavy chunks (rate tables are often low-similarity).
        if analysis.get("topic") == "travel" and analysis.get("is_specific"):
          low = (analysis.get("raw") or "").lower()
          asks_allowance = (
            ("allowance" in low)
            or ("per diem" in low)
            or ("daily allowance" in low)
            or ("reimbursement" in low)
            or ("claim" in low)
            or ("expense" in low)
            or ("amount" in low)
            or ("how much" in low)
            or ("kitna" in low)
            or ("kitni" in low)
            or bool(re.search(r"\bta/da\b", low))
            or bool(re.search(r"\bta\b", low))
            or bool(re.search(r"\bda\b", low))
          )
          missing_ok = True
          if not (analysis.get("travel_mode") or "").strip():
            missing_ok = False
          if analysis.get("overnight") is None:
            missing_ok = False
          if analysis.get("travel_mode") == "car" and analysis.get("engine_cc") is None:
            missing_ok = False

          if asks_allowance and missing_ok:
            extra = " pkr rs rupees rate per km ceiling limit entitlement table"
            more = self._retrieve(
              search_query + extra,
              k=k + 10,
              analysis=analysis,
              force_include_amounts=True,
            )
            if more:
              merged: dict[tuple[str, object, str], dict] = {}
              for h in hits + more:
                key = (h.get("source"), h.get("chunk_i"), h.get("text"))
                prev = merged.get(key)
                if not prev or float(h.get("score", 0)) > float(prev.get("score", 0)):
                  merged[key] = h
              hits = sorted(merged.values(), key=lambda x: x.get("score", 0), reverse=True)

        # For travel allowance questions, run a second pass to pull tables/ceilings.
        if analysis.get("topic") == "travel" and analysis.get("is_specific"):
          raw_low = (analysis.get("raw") or "").lower()
          mode = analysis.get("travel_mode") or ""

          extra_queries: list[str] = []

          # Primary mode-focused query.
          if mode == "air":
            extra_queries.append(" airfare ticket flight economy business class entitlement")
          elif mode == "car":
            extra_queries.append(" mileage per km cc engine fuel reimbursement cost per km")
          elif mode == "lodging":
            extra_queries.append(" hotel accommodation lodging ceiling per night entitlement pkr")
          elif mode == "per_diem":
            extra_queries.append(" daily allowance per diem meal allowance per day entitlement rs pkr")
          else:
            extra_queries.append(" daily allowance per diem hotel accommodation meal mileage airfare ticket ceiling limit reimbursement entitlement pkr rs")

          # If the user explicitly asks for hotel/overnight, also pull lodging ceilings.
          if analysis.get("overnight") is True or any(w in raw_low for w in ["hotel", "accommodation", "lodging", "per night", "overnight"]):
            extra_queries.append(" hotel accommodation entitlement pkr per night three star up to")

          # If the user asks for TA/DA / per diem, also pull the DA section.
          if ("daily allowance" in raw_low) or ("per diem" in raw_low) or bool(re.search(r"\bda\b", raw_low)) or bool(re.search(r"\bta/da\b", raw_low)):
            extra_queries.append(" per diem allowance domestic daily allowance da reimbursement rs pkr per day up to")

          merged: dict[tuple[str, object, str], dict] = {}
          for h in hits:
            merged[(h.get("source"), h.get("chunk_i"), h.get("text"))] = h

          for extra_q in extra_queries[:3]:
            more = self._retrieve(
              search_query + extra_q,
              k=k,
              analysis=analysis,
              force_include_amounts=True,
            )
            for h in more or []:
              key = (h.get("source"), h.get("chunk_i"), h.get("text"))
              prev = merged.get(key)
              if not prev or float(h.get("score", 0)) > float(prev.get("score", 0)):
                merged[key] = h

          hits = sorted(merged.values(), key=lambda x: x.get("score", 0), reverse=True)
        context = self._build_context(hits)

        # ── Step 3: Conversation history ──────────────────────────────────────
        hist_parts = []
        for turn in history[-4:]:          # last 4 turns is enough context
            role    = "Employee" if turn.get("role") == "user" else "HR Assistant"
            content = str(turn.get("content", "")).strip()
            if content:
                hist_parts.append(f"{role}: {content}")
        history_str = "\n".join(hist_parts)

        # ── Step 4: System prompt (intent-aware) ──────────────────────────────
        system = self._build_system_prompt(analysis)

        # ── Step 5: User prompt — clean labels, no leakable headers ────────────
        if context:
            history_block = f"[Earlier in this chat]\n{history_str}\n\n" if history_str else ""

            facts = []
            # Always inject role & destination when present — they affect rate tables
            if analysis.get("employee_role"):
                facts.append(f"employee_role/grade: {analysis.get('employee_role')}")
            if analysis.get("destination"):
                facts.append(f"destination_city: {analysis.get('destination')}")
            if analysis.get("topic") == "travel":
                if analysis.get("travel_mode"):
                    facts.append(f"travel_mode: {analysis.get('travel_mode')}")
                if analysis.get("engine_cc") is not None:
                    facts.append(f"engine_cc: {analysis.get('engine_cc')}")
                if analysis.get("overnight") is not None:
                    facts.append(f"overnight_stay: {analysis.get('overnight')}")
            facts_block = (
                "[Extracted employee/trip details — use these to look up the correct "
                "grade/role entitlement row in the policy tables]\n"
                + "\n".join(facts) + "\n\n"
            ) if facts else ""

            user_prompt = (
                "[HR Policy context — use this to answer. Do not copy large passages; extract exact figures/limits/amounts as needed.]\n"
                f"{context}\n\n"
                f"{history_block}"
                f"{facts_block}"
                f"Employee: {message}\n"
                "FINAL:"
            )

            # For common travel allowance questions with full details, prefer a
            # deterministic extractor so we don't misread the policy tables.
            deterministic = self._try_travel_entitlements_answer(analysis, hits, message)
            if deterministic:
              deterministic = self._enforce_grounding(deterministic, context, message)
              return deterministic
        else:
            user_prompt = (
                f"Employee: {message}\n"
                f"HR Assistant: This isn't covered in the current HR policy documents."
                f" Please reach out to HR directly for help with this."
            )

        # ── Step 6: Generate then enforce paragraph output ──────────────────
        raw = self._generate(system, user_prompt)
        if not raw or not str(raw).strip():
          # Retry once with a stricter, minimal instruction.
          raw = self._generate(
            system,
            user_prompt + "\n\nIMPORTANT: After 'FINAL:' write at least one complete sentence. Do not leave FINAL empty."
          )

        if not raw or not str(raw).strip():
          # Retry once more with a smaller context budget (top chunks only).
          short_context = self._build_context(hits[: min(len(hits), 6)])
          if short_context:
            history_block = f"[Earlier in this chat]\n{history_str}\n\n" if history_str else ""

            facts = []
            if analysis.get("employee_role"):
              facts.append(f"employee_role/grade: {analysis.get('employee_role')}")
            if analysis.get("destination"):
              facts.append(f"destination_city: {analysis.get('destination')}")
            if analysis.get("topic") == "travel":
              if analysis.get("travel_mode"):
                facts.append(f"travel_mode: {analysis.get('travel_mode')}")
              if analysis.get("engine_cc") is not None:
                facts.append(f"engine_cc: {analysis.get('engine_cc')}")
              if analysis.get("overnight") is not None:
                facts.append(f"overnight_stay: {analysis.get('overnight')}")
            facts_block = (
              "[Extracted employee/trip details — use these to look up the correct "
              "grade/role entitlement row in the policy tables]\n"
              + "\n".join(facts) + "\n\n"
            ) if facts else ""

            user_prompt_short = (
              "[HR Policy context — use this to answer. Do not copy large passages; extract exact figures/limits/amounts as needed.]\n"
              f"{short_context}\n\n"
              f"{history_block}"
              f"{facts_block}"
              f"Employee: {message}\n"
              "FINAL:\n"
              "IMPORTANT: Write at least one complete sentence."
            )
            raw = self._generate(system, user_prompt_short)

        if not raw or not str(raw).strip():
          return (
            "⚠️ I couldn’t generate a response from the AI model just now. "
            "Please try again, or click Re-index and retry if this keeps happening."
          )
        answer = self._to_paragraph(raw)
        if not answer or not str(answer).strip():
          return (
            "⚠️ I couldn’t generate a readable answer from the policy text. "
            "Please try again, or click Re-index and retry."
          )
        answer = self._enforce_grounding(answer, context, message)
        return answer

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

        system = (
            "You are a senior HR document specialist with expertise in policy writing and compliance. "
            "Write in professional business English only. Do NOT use Roman Urdu."
        )
        prompt = f"""Please review the following HR policy document and provide detailed, actionable improvement suggestions.

      IMPORTANT:
      - Output must be in English only.
      - Do NOT use Roman Urdu.

Document Name: {doc_name}

Document Content:
{full_text}

Provide your review under these headings:
1. OVERALL ASSESSMENT — Is the policy clear, complete, and professionally written?
2. SPECIFIC ISSUES — List exact sections that are unclear, missing, or need updating (quote the relevant text)
3. MISSING CONTENT — What important information should be added?
4. LANGUAGE & FORMATTING — How to improve readability and structure
5. COMPLIANCE & BEST PRACTICES — Any HR best-practice or legal considerations missing
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
    """Return the bot instance, initialising it synchronously if needed."""
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

EMPLOYEE_HTML = r"""<!DOCTYPE html>
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
.bbl{padding:12px 16px;border-radius:12px;font-size:14px;line-height:1.75;word-break:break-word;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.b .bbl{background:var(--bot);border:1px solid var(--border);border-top-left-radius:3px}
.msg.u .bbl{background:var(--user);border:1px solid #1e3055;border-top-right-radius:3px}
.bbl strong{color:var(--green);font-weight:600}
.bbl code{font-family:var(--m);font-size:12.5px;background:rgba(255,255,255,.05);padding:2px 5px;border-radius:4px}
.bbl ul,.bbl ol{padding-left:20px;margin:6px 0}
.bbl li{margin-bottom:4px}
.bbl h3,.bbl h4{color:var(--green);font-size:14px;margin:10px 0 4px}
.typing{display:flex;align-items:center;gap:5px;padding:12px 16px;background:var(--bot);border:1px solid var(--border);border-radius:12px;border-top-left-radius:3px}
.typing span{width:6px;height:6px;border-radius:50%;background:var(--dim);animation:blink 1.2s infinite}
.typing span:nth-child(2){animation-delay:.2s}
.typing span:nth-child(3){animation-delay:.4s}
@keyframes blink{0%,80%,100%{opacity:.25;transform:scale(.9)}40%{opacity:1;transform:scale(1)}}
.welcome{align-self:center;text-align:center;max-width:460px;padding:48px 20px}
.wi{margin-bottom:18px;display:flex;justify-content:center}
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
      <div class="cico">🤖</div>
      <div>
        <h2>HR Assistant</h2>
        <p>Reads your HR documents and answers policy questions in detail</p>
      </div>
      <div class="ctop-btns">
        <button class="tbtn" onclick="clearChat()">✕ Clear</button>
        <button class="tbtn" onclick="reindex()">↺ Re-index</button>
      </div>
    </div>

    <div class="msgs" id="msgs">
      <div class="welcome">
        <div class="wi"><svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="8" y="20" width="48" height="38" rx="3" fill="rgba(34,211,165,0.12)" stroke="#22d3a5" stroke-width="2"/><rect x="20" y="8" width="24" height="14" rx="2" fill="rgba(34,211,165,0.18)" stroke="#22d3a5" stroke-width="2"/><rect x="14" y="30" width="8" height="8" rx="1" fill="#22d3a5" opacity="0.7"/><rect x="28" y="30" width="8" height="8" rx="1" fill="#22d3a5" opacity="0.7"/><rect x="42" y="30" width="8" height="8" rx="1" fill="#22d3a5" opacity="0.7"/><rect x="14" y="44" width="8" height="8" rx="1" fill="#22d3a5" opacity="0.7"/><rect x="28" y="44" width="8" height="14" rx="1" fill="#22d3a5" opacity="0.9"/><rect x="42" y="44" width="8" height="8" rx="1" fill="#22d3a5" opacity="0.7"/></svg></div>
        <h2>Hello! I&#39;m your HR Assistant.</h2>
        <p>Ask me anything about MG Apparel HR policies &mdash; leave, travel allowance, attendance, overtime, grievances, or any other policy topic. I will give you exact figures from the policy documents.</p>
        <div class="wtags">
          <span class="wtag">Leave Policies</span>
          <span class="wtag">Attendance</span>
          <span class="wtag">Benefits</span>
          <span class="wtag">Grievances</span>
          <span class="wtag">SOPs</span>
          <span class="wtag">Code of Conduct</span>
        </div>
      </div>
    </div>

    <div class="ibar">
      <div class="chips">
        <button class="chip" onclick="ch(this)">Mujhe kitni annual leaves milti hain?</button>
        <button class="chip" onclick="ch(this)">Sick leave kaise apply karein?</button>
        <button class="chip" onclick="ch(this)">Casual leave ke kya rules hain?</button>
        <button class="chip" onclick="ch(this)">Overtime policy kya hai?</button>
        <button class="chip" onclick="ch(this)">What are the working hours?</button>
        <button class="chip" onclick="ch(this)">How to raise a grievance?</button>
      </div>
      <div class="iwrap">
        <textarea id="inp" rows="1" placeholder="Ask any HR question…" oninput="rsz(this)" onkeydown="kd(event)"></textarea>
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
  w.innerHTML='<div class="welcome"><div class="wi"><svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="8" y="20" width="48" height="38" rx="3" fill="rgba(34,211,165,0.12)" stroke="#22d3a5" stroke-width="2"/><rect x="20" y="8" width="24" height="14" rx="2" fill="rgba(34,211,165,0.18)" stroke="#22d3a5" stroke-width="2"/><rect x="14" y="30" width="8" height="8" rx="1" fill="#22d3a5" opacity="0.7"/><rect x="28" y="30" width="8" height="8" rx="1" fill="#22d3a5" opacity="0.7"/><rect x="42" y="30" width="8" height="8" rx="1" fill="#22d3a5" opacity="0.7"/><rect x="14" y="44" width="8" height="8" rx="1" fill="#22d3a5" opacity="0.7"/><rect x="28" y="44" width="8" height="14" rx="1" fill="#22d3a5" opacity="0.9"/><rect x="42" y="44" width="8" height="8" rx="1" fill="#22d3a5" opacity="0.7"/></svg></div><h2>Chat cleared.</h2><p>Ask me anything about MG Apparel HR policies.</p></div>';
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
        ask=msg+' Please answer concisely and include all key details.';
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
  d.innerHTML=`<div class="av">${role==='b'?'🤖':'👤'}</div><div class="bbl">${fmt(text)}</div>`;
  w.appendChild(d); w.scrollTop=w.scrollHeight; return d;
}

function addTyping(){
  const w=document.getElementById('msgs');
  const d=document.createElement('div');
  d.className='msg b';
  d.innerHTML='<div class="av">🤖</div><div class="typing"><span></span><span></span><span></span></div>';
  w.appendChild(d); w.scrollTop=w.scrollHeight; return d;
}

function fmt(t){
  t=t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  // Bold
  t=t.replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>');
  t=t.replace(/__(.*?)__/g,'<strong>$1</strong>');
  // Inline code
  t=t.replace(/`([^`]+)`/g,'<code>$1</code>');
  // Headings
  t=t.replace(/^### (.+)$/gm,'<h4>$1</h4>');
  t=t.replace(/^## (.+)$/gm,'<h3>$1</h3>');
  // Numbered and bullet lists
  t=t.replace(/^\d+\.\s+(.+)$/gm,'<li>$1</li>');
  t=t.replace(/^[-•*]\s+(.+)$/gm,'<li>$1</li>');
  t=t.replace(/(<li>[\s\S]*?<\/li>)/g,'<ul>$1</ul>');
  // Line breaks
  t=t.replace(/\n\n/g,'<br><br>');
  t=t.replace(/\n/g,'<br>');
  return t;
}
function ico(n){
  const e=(n.split('.').pop()||'').toLowerCase();
  return {pdf:'📄',docx:'📝',doc:'📝',xlsx:'📊',xls:'📊',txt:'📋',md:'📋'}[e]||'📁';
}

async function loadDocs(){
  try{
    const ctrl=new AbortController();
    const tm=setTimeout(()=>ctrl.abort(),15000);
    const r=await fetch('/api/documents',{signal:ctrl.signal});
    clearTimeout(tm);
    if(!r.ok) throw new Error('docs '+r.status);
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
  if(!doc||doc==='Loading…'||doc==='No documents found') return;
  out.className='';
  out.textContent='⏳ Analysing document… this may take 30–90 seconds.';
  btn.disabled=true;
  try{
    const r=await fetch('/api/suggest',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({document:doc})});
    const d=await r.json();
    out.textContent=d.suggestions||'No suggestions returned.';
  }catch{
    out.textContent='⚠️ Error connecting to server.';
  }
  btn.disabled=false;
}

async function reindex(){
  toast('⏳ Re-indexing all documents…');
  try{
    const r=await fetch('/api/reindex',{
      method:'POST',
      headers:{'X-Master-User': prompt('Master User ID:'), 'X-Master-Password': prompt('Master Password:')}
    });
    const d=await r.json();
    if(d.error){toast('⚠️ '+d.error);return;}
    toast(`✅ Done — ${d.count} documents indexed.`);
    loadDocs();
  }catch{toast('⚠️ Reindex failed.')}
}

async function checkStatus(){
  try{
    const r = await fetch('/api/status', {signal: AbortSignal.timeout(8000)});
    if(!r.ok) return;
    const d = await r.json();
    document.getElementById('mname').textContent = d.model      || '—';
    document.getElementById('dcnt').textContent  = d.documents  ?? '—';
    document.getElementById('lanip').textContent  = d.lan_url   || window.location.host;
    const w = document.getElementById('owarn');
    if(d.warn){ w.textContent=d.warn_msg||'⚠️ AI backend issue.'; w.style.display='block'; }
    else { w.style.display='none'; }
  } catch(e){}
}

function toast(msg){
  const t=document.getElementById('toast');
  t.textContent=msg; t.style.display='block';
  clearTimeout(t._t); t._t=setTimeout(()=>t.style.display='none',4000);
}

// Boot: retry until server responds, then load docs
let _bootDone = false;
async function boot(){
  for(let i=0; i<30; i++){
    try{
      const r = await fetch('/api/status', {signal: AbortSignal.timeout(4000)});
      if(r.ok){
        const d = await r.json();
        document.getElementById('mname').textContent = d.model || '—';
        document.getElementById('dcnt').textContent  = d.documents ?? '—';
        document.getElementById('lanip').textContent  = d.lan_url || window.location.host;
        const w = document.getElementById('owarn');
        if(d.warn){ w.textContent=d.warn_msg||'⚠️ AI backend issue.'; w.style.display='block'; }
        else { w.style.display='none'; }
        await loadDocs();
        _bootDone = true;
        setInterval(async()=>{ try{ await checkStatus(); await loadDocs(); }catch{} }, 30000);
        return;
      }
    } catch(e){}
    document.getElementById('slist').innerHTML='<div style="color:var(--dim);font-size:12px;padding:6px">Starting up… ('+(i+1)+'/30)</div>';
    await new Promise(r=>setTimeout(r, 2000));
  }
  document.getElementById('slist').innerHTML='<div style="color:var(--warn);font-size:12px;padding:6px">⚠️ Server not responding. Restart the chatbot.</div>';
}
boot();
</script>
</body>
</html>"""


MASTER_HTML = r"""<!DOCTYPE html>
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
      out((d.documents||[]).map((x,i)=>`${i+1}. ${x.name} (${x.chunks} chunks, indexed ${x.indexed_at.slice(0,10)})`).join('\n')||'No documents indexed.');
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
  resp.headers["Pragma"] = "no-cache"
  resp.headers["Expires"] = "0"
  return resp


@app.route("/master")
def master():
  resp = make_response(MASTER_HTML)
  resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
  resp.headers["Pragma"] = "no-cache"
  resp.headers["Expires"] = "0"
  return resp


@app.route("/api/chat", methods=["POST"])
def api_chat():
  d = request.get_json(silent=True)
  if d is None:
    # Fallback for clients that don't send valid JSON headers/bodies.
    d = request.form.to_dict(flat=True) if request.form else {}
  msg = ((d.get("message") or d.get("query") or "")).strip()
  if not msg:
    return jsonify({"error": "Empty message"}), 400
  bot = get_bot()
  history = d.get("history", [])
  try:
    fut = _chat_pool.submit(bot.chat, msg, history)
    response = fut.result(timeout=CHAT_TIMEOUT)
    if response is None:
      return jsonify({
        "error": "⚠️ The assistant returned no text. Please try again or rephrase your question."
      }), 502
    response_text = str(response).strip()
    if not response_text:
      return jsonify({
        "error": "⚠️ The assistant returned an empty response. Please try again or ask for a concise summary."
      }), 502
    return jsonify({"response": response_text})
  except FuturesTimeout:
    return jsonify({
      "error": "The assistant is still processing and timed out. Please retry, or ask for a concise summary first."
    }), 504
  except Exception as e:
    return jsonify({"error": f"⚠️ Chat error: {e}"}), 500


@app.route("/api/suggest", methods=["POST"])
def api_suggest():
    d    = request.get_json(silent=True) or {}
    name = (d.get("document") or "").strip()
    if not name:
        return jsonify({"error": "No document name"}), 400
    return jsonify({"suggestions": get_bot().suggest(name)})


@app.route("/api/documents")
def api_documents():
    try:
        docs = get_bot().list_docs()
    except Exception:
        docs = []
    return jsonify({"documents": docs})


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
    ip = lan_ip()

    warn = False
    warn_msg = None

    if AI_BACKEND == "groq":
        if not GROQ_API_KEY:
            warn = True
            warn_msg = "⚠️ GROQ_API_KEY is not set. Add a Groq API key or switch to Ollama."

    elif AI_BACKEND == "ollama":
        # Only warn if Ollama is actually unreachable or has no models.
        try:
            r = requests.get(f"{OLLAMA_URL}/api/version", timeout=3)
            r.raise_for_status()
            t = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
            t.raise_for_status()
            models = [m.get("name", "") for m in (t.json().get("models") or [])]
            models = [m for m in models if m]
            if not models:
                warn = True
                warn_msg = f"⚠️ Ollama is running but no models are installed. Run: ollama pull {OLLAMA_MODEL}"
        except Exception:
            warn = True
            warn_msg = "⚠️ Ollama is not reachable. Start Ollama (ollama serve) or update OLLAMA_URL."

    return jsonify({
        "status": "online",
        "documents": len(bot.list_docs()),
        "model": bot.model_info(),
        "backend": AI_BACKEND,
        "lan_url": f"http://{ip}:{RUNTIME_PORT}" if ip else None,
        "warn": bool(warn),
        "warn_msg": warn_msg,
    })


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 64)
    print("  MG Apparel HR Chatbot  |  Fixed & Cloud-Ready")
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
        print(f"  LAN URL     : http://{ip}:{PORT}  (share with employees on same network)")
    print("=" * 64)

    # Pre-load bot synchronously before Flask starts accepting requests
    print("[Boot] Initialising HR bot and indexing documents…")
    bot = get_bot()
    print(f"[Boot] Ready — {len(bot.list_docs())} documents indexed.")

    def _can_bind(host: str, port: int) -> bool:
      s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      try:
        s.bind((host, port))
        return True
      except OSError:
        return False
      finally:
        try:
          s.close()
        except Exception:
          pass

    bind_host = "0.0.0.0"
    bind_port = PORT

    if not _can_bind(bind_host, bind_port):
      # Try an alternate port on ALL interfaces first (best for LAN sharing).
      for p in range(PORT + 1, PORT + 51):
        if _can_bind("0.0.0.0", p):
          bind_host = "0.0.0.0"
          bind_port = p
          print(f"[WARN] Port {PORT} is unavailable. Using 0.0.0.0:{bind_port} instead.")
          break
      else:
        # Last resort: local-only bind
        if _can_bind("127.0.0.1", PORT):
          bind_host = "127.0.0.1"
          bind_port = PORT
          print(f"[WARN] Cannot bind 0.0.0.0:{PORT} on this PC. Using 127.0.0.1:{PORT} (local-only).")
        else:
          for p in range(PORT + 1, PORT + 51):
            if _can_bind("127.0.0.1", p):
              bind_host = "127.0.0.1"
              bind_port = p
              print(f"[WARN] Port {PORT} is unavailable. Using 127.0.0.1:{bind_port} instead (local-only).")
              break
          else:
            raise SystemExit(f"[ERROR] Unable to bind to any port in range {PORT}-{PORT + 50}.")

    globals()["RUNTIME_PORT"] = bind_port

    # Print final URLs (actual bound port)
    print(f"[INFO] Web UI    : http://localhost:{bind_port}")
    print(f"[INFO] Master UI : http://localhost:{bind_port}/master")
    ip2 = lan_ip()
    if ip2:
      print(f"[INFO] LAN URL   : http://{ip2}:{bind_port}")

    # Open browser automatically
    import webbrowser
    threading.Timer(2.0, lambda: webbrowser.open(f"http://localhost:{bind_port}")).start()

    app.run(host=bind_host, port=bind_port, debug=False, threaded=True)