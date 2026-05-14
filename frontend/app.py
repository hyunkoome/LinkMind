"""
LinkMind Streamlit UI — MVP.

실행:
    cd <project_root>
    streamlit run frontend/app.py

LinkMind FastAPI 서버가 LINKMIND_HOST:LINKMIND_PORT 에서 떠 있어야 함.
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

API_BASE = os.getenv("LINKMIND_API_BASE", "http://localhost:8000")

st.set_page_config(page_title="LinkMind", layout="wide")

st.title("🧠 LinkMind")
st.caption("개인 AI Research OS — Search · Ask · Ingest")

tab_search, tab_ask, tab_ingest, tab_status = st.tabs(["Search", "Ask", "Ingest", "Status"])

# ─── Search ────────────────────────────────────────────────────
with tab_search:
    q = st.text_input("검색어", placeholder="예: 3DGS compression, reflector localization")
    top_k = st.slider("Top-K", 1, 30, 10)
    if st.button("검색", type="primary") and q:
        with st.spinner("검색 중..."):
            r = httpx.post(f"{API_BASE}/search", json={"query": q, "top_k": top_k}, timeout=60.0)
            r.raise_for_status()
            data = r.json()
        for hit in data["hits"]:
            with st.container(border=True):
                st.markdown(f"**[{hit['score']:.3f}] {hit.get('title') or '(no title)'}**")
                if hit.get("source_url"):
                    st.markdown(f"🔗 {hit['source_url']}")
                # summary (AI 요약 한국어) 가 있으면 우선 표시 — 잡음 적고 보기 좋음.
                # 없으면 snippet (raw chunk 텍스트) 로 fallback.
                if hit.get("summary"):
                    st.markdown(hit["summary"])
                elif hit.get("snippet"):
                    st.caption("(요약 미생성 — raw 본문 일부)")
                    st.write(hit["snippet"])
                st.caption(f"source: `{hit['source_type']}` · tags: {hit.get('tags') or '-'}")

# ─── Ask (RAG) ─────────────────────────────────────────────────
with tab_ask:
    question = st.text_area("질문", height=100, placeholder="LiDAR GS fusion 관련해서 내가 모은 자료 요약해줘")
    provider = st.selectbox("LLM Provider", ["(default)", "openai", "claude", "ollama"], index=0)
    if st.button("질문 보내기", type="primary") and question:
        with st.spinner("LinkMind 검색 + LLM 호출 중..."):
            req: dict = {"question": question, "top_k": 8}
            if provider != "(default)":
                req["llm_provider"] = provider
            r = httpx.post(f"{API_BASE}/ask", json=req, timeout=120.0)
            r.raise_for_status()
            data = r.json()
        st.markdown(f"### 답변 _(via {data['llm_provider']} / {data['llm_model']})_")
        st.write(data["answer"])
        if data.get("citations"):
            st.markdown("#### 인용")
            for i, c in enumerate(data["citations"], start=1):
                st.markdown(f"[{i}] **{c.get('title') or 'untitled'}** — {c.get('source_url') or ''}")

# ─── Ingest (manual 입력) ──────────────────────────────────────
with tab_ingest:
    st.caption("수동 텍스트 ingest. 자동 수집은 OpenClaw 또는 별도 스크립트에서 처리.")
    source_type = st.selectbox(
        "source_type",
        ["manual", "url", "telegram", "slack", "github", "arxiv", "youtube", "pdf"],
    )
    raw_content = st.text_area("raw_content", height=200)
    title = st.text_input("title (옵션)")
    source_url = st.text_input("source_url (옵션)")
    if st.button("ingest 보내기", type="primary") and raw_content:
        with st.spinner("ingest 중..."):
            req = {
                "source_type": source_type,
                "raw_content": raw_content,
                "title": title or None,
                "source_url": source_url or None,
                "analyze_now": True,
            }
            r = httpx.post(f"{API_BASE}/ingest", json=req, timeout=300.0)
            r.raise_for_status()
            st.success(r.json())

# ─── Status ────────────────────────────────────────────────────
with tab_status:
    if st.button("health 확인"):
        r = httpx.get(f"{API_BASE}/health", timeout=10.0)
        st.json(r.json())
    st.caption(f"API base: `{API_BASE}` (LINKMIND_API_BASE 환경변수로 변경 가능)")
