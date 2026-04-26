import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib
import streamlit as st

st.set_page_config(
    page_title="ANEEL RAG",
    page_icon="bolt",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGES = [
    ("Pipeline",   "app_pages.pipeline"),
    ("RAG Tester", "app_pages.rag_tester"),
    ("Documentos", "app_pages.doc_browser"),
]

labels = [p[0] for p in PAGES]

with st.sidebar:
    st.markdown("## ANEEL RAG")
    choice = st.radio("Pagina", labels, label_visibility="collapsed")

module_path = next(mod for label, mod in PAGES if label == choice)
module = importlib.import_module(module_path)
module.render()
