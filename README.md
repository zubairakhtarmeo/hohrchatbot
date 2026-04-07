# HR Chatbot (Flask + Streamlit)

This project can run locally (Flask) and can also be deployed publicly using Streamlit Community Cloud.

## Local run (Flask)

```powershell
cd "<your-repo-folder>"
.\.venv\Scripts\python.exe .\hr_chatbot.py
```

Open the URL printed in the console (often `http://localhost:5001` if `5000` is already in use).

## Local run (Streamlit)

```powershell
cd "<your-repo-folder>"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run .\streamlit_app.py
```

## Configuration (Groq)

Recommended: create a local `.env` file (this is git-ignored):

```
HR_AI_BACKEND=groq
GROQ_API_KEY=YOUR_KEY
```

## Deploy on Streamlit Cloud

1. Push this folder to GitHub.
2. Go to https://share.streamlit.io and create a new app.
3. Set **Main file path** to `streamlit_app.py`.
4. In **Secrets**, add:

```
HR_AI_BACKEND = "groq"
GROQ_API_KEY = "YOUR_KEY"
```

> Important: if your policy manuals are confidential, do not make the app public.
