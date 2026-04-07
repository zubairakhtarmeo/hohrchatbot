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


@st.cache_resource(show_spinner=False)
def get_bot() -> HRChatbot:
    bot = HRChatbot()
    bot.index_all()
    return bot


bot = get_bot()


if "history" not in st.session_state:
    st.session_state.history = []  # list[dict] with {role, content}


with st.sidebar:
    st.title("HR Assistant")
    st.caption("Answers from your HR policy manuals")

    st.write(f"Backend: **{os.getenv('HR_AI_BACKEND', 'ollama')}**")
    st.write(f"Model: **{bot.model_info()}**")

    try:
        docs_count = len(bot.list_docs())
    except Exception:
        docs_count = 0
    st.write(f"Documents: **{docs_count}**")

    if st.button("Re-index documents", use_container_width=True):
        with st.spinner("Re-indexing…"):
            bot.index_all()
        st.success("Re-index complete")


st.header("Chat")

for turn in st.session_state.history:
    role = "assistant" if turn.get("role") == "assistant" else "user"
    with st.chat_message(role):
        st.markdown(turn.get("content", ""))


prompt = st.chat_input("Ask any HR question…")
if prompt:
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            reply = bot.chat(prompt, st.session_state.history[-10:])
        st.markdown(reply)

    st.session_state.history.append({"role": "assistant", "content": reply})
