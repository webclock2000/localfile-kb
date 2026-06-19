"""Streamlit UI package for FileKB.

All pages communicate with the FastAPI backend at http://localhost:9494.
No page directly imports store.py, llm.py, or other core engine modules.
"""

# Each page is a standalone Streamlit script (no shared imports between pages)
