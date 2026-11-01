# GovAssist AI — Agentic Government Benefits & Application Assistant

## What changed
- Natural-language chat UI using Streamlit.
- Planner extracts explicit profile details and chooses targeted search queries.
- Search tool queries official Indian government domains and retrieves scheme pages where possible.
- Reflection step reviews search quality and retries with revised queries once.
- Response step explains relevant results and missing/unverified conditions.
- Session conversation history and profile are retained in the Streamlit session.
- Agent trace is visible for a live project demonstration.

## Setup (Windows PowerShell)
1. Copy `app.py`, `agents.py`, `tools.py`, and `requirements.txt` into your existing project folder.
2. Keep your `.env` file in the same folder. It must contain:
   `GROQ_API_KEY=your_actual_key`
   Optionally set `GROQ_MODEL=openai/gpt-oss-20b`.
3. Activate your existing virtual environment.
4. Install requirements:
   `pip install -r requirements.txt`
5. Start the UI:
   `streamlit run app.py`

## Terminal test
Run `python agents.py` for a terminal chat loop.

## Important notes
- Never share or commit your API key.
- Search snippets and successful page retrieval do not prove that every eligibility condition is verified.
- The assistant does not automatically submit applications or upload documents.
- Search results depend on the search provider and internet availability.
