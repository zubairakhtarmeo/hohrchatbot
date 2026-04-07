import os

import streamlit as st


def _apply_streamlit_secrets_to_env() -> None:
    """Map Streamlit Cloud secrets into env vars expected by hr_chatbot.py."""
    try:
        secrets = st.secrets
    except Exception:
        return

    for key in (
        "HR_AI_BACKEND",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "OLLAMA_URL",
        "OLLAMA_MODEL",
        "HR_DATA_DIR",
        "HR_BOT_DIR",
    ):
        if key in secrets and not os.getenv(key):
            os.environ[key] = str(secrets[key])


_apply_streamlit_secrets_to_env()

# Import after secrets/env are applied (hr_chatbot.py reads env at import time)
from hr_chatbot import HRChatbot  # noqa: E402


st.set_page_config(page_title="HR Assistant", layout="wide")


SUGGESTED_QUESTIONS = [
    "How many annual leaves do I get?",
    "How to apply for sick leave?",
    "What are the rules for casual leave?",
    "What is the overtime policy?",
    "What are the working hours?",
    "How to raise a grievance?",
]


@st.cache_resource(show_spinner=False)
def get_bot() -> HRChatbot:
    bot = HRChatbot()
    bot.index_all()
    return bot


bot = get_bot()


if "history" not in st.session_state:
    st.session_state.history = []  # list[dict] with {role, content}


def _clear_chat() -> None:
    st.session_state.history = []


def _render_sidebar(bot: HRChatbot) -> None:
    st.title("HR Assistant")
    st.caption("Answers from your HR policy manuals")

    st.write(f"Backend: **{os.getenv('HR_AI_BACKEND', 'ollama')}**")
    st.write(f"Model: **{bot.model_info()}**")

    try:
        docs = sorted(bot.list_docs(), key=lambda d: str(d.get("name", "")).lower())
    except Exception:
        docs = []

    st.write(f"Documents: **{len(docs)}**")

    if docs:
        st.subheader("Indexed files")
        for d in docs:
            name = str(d.get("name", ""))
            chunks = d.get("chunks")
            left, right = st.columns([6, 1])
            left.write(name)
            right.write(str(chunks) if chunks is not None else "")


with st.sidebar:
    _render_sidebar(bot)

title_col, clear_col, reindex_col = st.columns([6, 1, 1])
with title_col:
    st.header("Chat")
with clear_col:
    if st.button("Clear", use_container_width=True):
        _clear_chat()
        st.rerun()
with reindex_col:
    if st.button("Re-index", use_container_width=True):
        with st.spinner("Re-indexing…"):
            bot.index_all()
        st.success("Re-index complete")
        st.rerun()

for turn in st.session_state.history:
    role = "assistant" if turn.get("role") == "assistant" else "user"
    with st.chat_message(role):
        st.markdown(turn.get("content", ""))

st.caption("Quick questions")
quick_prompt = None
chips_cols = st.columns(3)
for i, q in enumerate(SUGGESTED_QUESTIONS):
    if chips_cols[i % 3].button(q, key=f"chip_{i}", use_container_width=True):
        quick_prompt = q

prompt = st.chat_input("Ask any HR question…")
if not prompt and quick_prompt:
    prompt = quick_prompt

if prompt:
    # Pass only the PRIOR turns to the bot (avoid duplicating the current prompt).
    prior_history = st.session_state.history[-10:]

    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            reply = bot.chat(prompt, prior_history)
        st.markdown(reply)

    st.session_state.history.append({"role": "assistant", "content": reply})
