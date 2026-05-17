"""
Autonomous Trading Framework — Multi-page Streamlit app.

Run from repo root: streamlit run ui/app.py
"""
import streamlit as st

st.set_page_config(
    page_title="Trading Framework",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    [data-testid="stSidebar"] { background: #0d1117; }
    [data-testid="stSidebar"] * { color: #e6edf3 !important; }
</style>
""", unsafe_allow_html=True)

st.title("📈 Autonomous Trading Framework")
st.markdown("""
**Agent-orchestrated equity trading for the Indian (NSE) market.**

Use the sidebar to navigate:

| Page | What's there |
|------|-------------|
| 🔧 **Setup** | Configure API keys and validate connections |
| 🗺️ **How It Works** | Interactive visual of the full pipeline |
| 📊 **Dashboard** | Portfolio, signals, backtests, news |
""")

st.info("👈 Select a page from the sidebar to get started.")
