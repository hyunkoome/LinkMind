"""
LinkMind Streamlit UI — MVP.

실행:
    cd <project_root>
    streamlit run frontend/app.py

LinkMind FastAPI 서버가 LINKMIND_API_BASE 에서 떠 있어야 함.
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

API_BASE = os.getenv("LINKMIND_API_BASE", "http://localhost:8000")

st.set_page_config(page_title="LinkMind", layout="wide")

st.title("🧠 LinkMind")
st.caption("개인 AI Research OS — Search · Ask · Ingest · Settings")

tab_search, tab_ask, tab_ingest, tab_topics, tab_settings, tab_status = st.tabs(
    ["Search", "Ask", "Ingest", "Topics", "Settings", "Status"]
)


def _fetch_topics_for_item(item_id: str) -> list[dict]:
    """search hit 별 topic membership 조회 — lazy, 실패해도 검색 자체는 깨지지 않음."""
    try:
        r = httpx.get(f"{API_BASE}/topics/items/{item_id}", timeout=5.0)
        if r.status_code == 200:
            return r.json()
    except Exception:  # noqa: BLE001
        pass
    return []


# ─── Search ────────────────────────────────────────────────────
with tab_search:
    st.caption(
        "검색어에 `#SLAM`, `#3DGS` 같은 해시태그를 섞으면 해당 tag 로 자동 필터. "
        "예: `cure model #survival #statistics`. 태그만 입력하면 최신순으로 보여줌."
    )
    q = st.text_input("검색어", placeholder="예: 3DGS compression #SLAM")
    top_k = st.slider("Top-K", 1, 30, 10)
    if st.button("검색", type="primary") and q:
        with st.spinner("검색 중..."):
            r = httpx.post(f"{API_BASE}/search", json={"query": q, "top_k": top_k}, timeout=60.0)
            r.raise_for_status()
            data = r.json()
        if not data["hits"]:
            st.info("결과 없음. 다른 검색어 또는 태그를 시도해보세요.")
        for hit in data["hits"]:
            with st.container(border=True):
                st.markdown(f"**[{hit['score']:.3f}] {hit.get('title') or '(no title)'}**")
                if hit.get("source_url"):
                    url = hit["source_url"]
                    # path-only `/files/...` 는 API_BASE 와 결합해서 절대 URL 로 (PDF inline).
                    if url.startswith("/"):
                        url = f"{API_BASE}{url}"
                    st.markdown(f"🔗 [{url}]({url})")
                if hit.get("summary"):
                    st.markdown(hit["summary"])
                elif hit.get("snippet"):
                    st.caption("(요약 미생성 — raw 본문 일부)")
                    st.write(hit["snippet"])
                meta_parts = [f"source: `{hit['source_type']}`"]
                if hit.get("tags"):
                    meta_parts.append("tags: " + " ".join(f"`#{t}`" for t in hit["tags"]))
                st.caption(" · ".join(meta_parts))

                # 같은 topic 에 묶인 다른 modality item — multi-modal 컨텍스트 표시.
                topics_for_hit = _fetch_topics_for_item(str(hit["item_id"]))
                if topics_for_hit:
                    topic_chips = " ".join(
                        f"`{t['slug']}({t['role']})`" for t in topics_for_hit
                    )
                    st.caption(f"📚 topics: {topic_chips}")


# ─── Ask (RAG) ─────────────────────────────────────────────────
with tab_ask:
    st.caption("Settings 탭에서 default LLM provider / model 을 바꿀 수 있습니다.")
    question = st.text_area(
        "질문", height=100, placeholder="LiDAR GS fusion 관련해서 내가 모은 자료 요약해줘"
    )
    if st.button("질문 보내기", type="primary") and question:
        with st.spinner("LinkMind 검색 + LLM 호출 중..."):
            r = httpx.post(
                f"{API_BASE}/ask",
                json={"question": question, "top_k": 8},
                timeout=300.0,
            )
            r.raise_for_status()
            data = r.json()
        st.markdown(f"### 답변 _(via {data['llm_provider']} / {data['llm_model']})_")
        st.write(data["answer"])
        if data.get("citations"):
            st.markdown("#### 인용")
            for i, c in enumerate(data["citations"], start=1):
                st.markdown(f"[{i}] **{c.get('title') or 'untitled'}** — {c.get('source_url') or ''}")


# ─── Ingest (URL/manual) ───────────────────────────────────────
with tab_ingest:
    st.caption(
        "URL 한 줄이면 자동 분석 (본문/abstract/keywords 추출 + 한국어 요약 + 해시태그). "
        "수동 텍스트는 source_type 선택 후 raw_content 붙여넣기."
    )

    st.subheader("URL / 자동")
    st.caption(
        "host 자동 분류 — youtube.com/youtu.be → 영상/플레이리스트, github.com → repo, "
        "*.pdf 끝나면 PDF URL, 나머지는 일반 웹 페이지(논문 abstract 포함)."
    )
    url = st.text_input(
        "URL",
        placeholder="arxiv abs / github repo / *.pdf / youtube 영상·플레이리스트 / 일반 웹 페이지",
    )
    kind = st.selectbox(
        "처리 방식",
        ["auto", "url", "youtube", "github", "pdf"],
        index=0,
        help="auto 가 권장. 특정 ingester 를 강제하려면 직접 선택.",
    )
    force_url = st.checkbox(
        "force (기존 item 도 summary/tags 재계산)", value=False,
        help="동일 hash 의 기존 item 이 있으면 skip 대신 새 fetch 한 abstract/"
             "metadata 로 summary/tags 만 다시 계산. raw/chunks 는 그대로 보존.",
    )
    if st.button("URL ingest", type="primary") and url:
        endpoint = f"/ingest/{kind}" if kind != "auto" else "/ingest/auto"
        with st.spinner(f"{endpoint} 호출 중..."):
            r = httpx.post(
                f"{API_BASE}{endpoint}",
                json={"url": url, "analyze_now": True, "force": force_url},
                timeout=600.0,
            )
            if r.status_code != 200:
                st.error(f"{r.status_code}: {r.text}")
            else:
                result = r.json()
                status = (
                    "refreshed" if result.get("refreshed")
                    else ("created" if result.get("created") else "skipped (existing)")
                )
                st.success(f"item_id: `{result.get('item_id')}` · {status}")
                if result.get("title"):
                    st.markdown(f"**제목**: {result['title']}")
                if result.get("tags"):
                    st.markdown("**tags**: " + " ".join(f"`#{t}`" for t in result["tags"]))
                st.caption(
                    f"chunks_indexed={result.get('chunks_indexed')}, "
                    f"summary_generated={result.get('summary_generated')}"
                )

    st.divider()
    st.subheader("PDF 파일 업로드")
    pdf_file = st.file_uploader("PDF 선택", type=["pdf"])
    force_pdf = st.checkbox(
        "force (PDF) — 기존 item 도 summary/tags 재계산", value=False,
        key="force_pdf",
    )
    if pdf_file is not None and st.button("PDF 업로드 ingest"):
        with st.spinner("PDF 저장 → 텍스트 추출 → 분석..."):
            r = httpx.post(
                f"{API_BASE}/ingest/pdf/upload",
                files={"file": (pdf_file.name, pdf_file.getvalue(), "application/pdf")},
                params={"force": str(force_pdf).lower()},
                timeout=600.0,
            )
            if r.status_code != 200:
                st.error(f"{r.status_code}: {r.text}")
            else:
                result = r.json()
                st.success(f"item_id: `{result.get('item_id')}` · created={result.get('created')}")
                if result.get("tags"):
                    st.markdown("**tags**: " + " ".join(f"`#{t}`" for t in result["tags"]))

    st.divider()
    st.subheader("수동 텍스트")
    source_type = st.selectbox(
        "source_type",
        ["manual", "url", "telegram", "slack", "github", "arxiv",
         "youtube", "youtube_playlist", "pdf"],
    )
    raw_content = st.text_area("raw_content", height=200)
    title = st.text_input("title (옵션)")
    source_url = st.text_input("source_url (옵션)")
    if st.button("ingest 보내기") and raw_content:
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


# ─── Topics ────────────────────────────────────────────────────
with tab_topics:
    st.caption(
        "외부 식별자 (arxiv_id / github_repo / doi / yt 등) 로 자동 그룹핑된 'topic' 단위. "
        "한 topic 에 같은 주제의 paper / 코드 / 영상 등이 묶입니다. "
        "수동 link 도 가능 — 자동 매칭이 놓친 케이스용."
    )

    col_l, col_r = st.columns([1, 2])

    with col_l:
        st.markdown("### Topics")
        topic_limit = st.slider("표시 개수", 10, 200, 50)
        try:
            t_list = httpx.get(
                f"{API_BASE}/topics", params={"limit": topic_limit}, timeout=10.0
            ).json()
        except Exception as e:                      # noqa: BLE001
            st.error(f"Topics API 호출 실패: {e}")
            t_list = []

        if not t_list:
            st.info("topic 없음. ingest 가 외부 식별자를 발견하면 자동 생성됩니다.")

        # 선택 상태를 session 에 보관
        if "selected_topic_slug" not in st.session_state:
            st.session_state.selected_topic_slug = (
                t_list[0]["slug"] if t_list else None
            )

        for t in t_list:
            label = f"**{t['slug']}** ({t['item_count']})"
            if st.button(label, key=f"pick_{t['slug']}", use_container_width=True):
                st.session_state.selected_topic_slug = t["slug"]

    with col_r:
        slug = st.session_state.get("selected_topic_slug")
        if not slug:
            st.write("← 왼쪽에서 topic 을 선택하세요.")
        else:
            try:
                detail = httpx.get(
                    f"{API_BASE}/topics/{slug}", timeout=10.0
                ).json()
            except Exception as e:                      # noqa: BLE001
                st.error(f"topic 상세 조회 실패: {e}")
                detail = None
            if detail:
                st.markdown(f"### {detail['title']}")
                st.caption(f"slug: `{detail['slug']}`")
                if detail.get("primary_external_id"):
                    p = detail["primary_external_id"]
                    st.caption(f"primary: {p.get('kind')} · {p.get('value')}")
                if detail.get("description"):
                    st.markdown(detail["description"])
                if detail.get("tags"):
                    st.caption("tags: " + " ".join(f"`#{t}`" for t in detail["tags"]))

                st.markdown(f"#### Items ({len(detail.get('items') or [])})")
                for it in detail.get("items") or []:
                    with st.container(border=True):
                        head = f"**[{it['role']}]** {it.get('title') or '(no title)'}"
                        if it.get("confidence", 1.0) < 1.0:
                            head += f"  _(conf {it['confidence']:.2f}, {it['source']})_"
                        st.markdown(head)
                        if it.get("source_url"):
                            url = it["source_url"]
                            if url.startswith("/"):
                                url = f"{API_BASE}{url}"
                            st.markdown(f"🔗 [{url}]({url})")
                        if it.get("summary"):
                            with st.expander("요약 보기"):
                                st.markdown(it["summary"])
                        if it.get("tags"):
                            st.caption(
                                "tags: " + " ".join(f"`#{t}`" for t in it["tags"])
                            )

    st.divider()
    st.markdown("### 수동 link (자동이 놓친 케이스)")
    st.caption("item_id 와 대상 topic slug 를 알아내 직접 link. role = paper/code/video/pdf/blog/note 등.")
    col1, col2 = st.columns(2)
    with col1:
        link_item_id = st.text_input("item_id (UUID)")
        link_topic_slug = st.text_input("topic slug (예: arxiv:2106.09685)")
    with col2:
        link_role = st.selectbox(
            "role", ["paper", "pdf", "code", "video", "playlist", "blog", "note"]
        )
        link_note = st.text_input("note (옵션)")
    if st.button("link 만들기") and link_item_id and link_topic_slug:
        try:
            r = httpx.post(
                f"{API_BASE}/topics/items/{link_item_id}/link",
                json={
                    "topic_slug": link_topic_slug,
                    "role": link_role,
                    "note": link_note or None,
                },
                timeout=10.0,
            )
            if r.status_code == 200:
                st.success(f"link 생성됨: {r.json()}")
            else:
                st.error(f"{r.status_code}: {r.text}")
        except Exception as e:                          # noqa: BLE001
            st.error(f"실패: {e}")


# ─── Settings ──────────────────────────────────────────────────
with tab_settings:
    st.caption(
        "여기서 바꾼 default 모델 / system prompt 는 DB 에 저장되어 backend 재시작 후에도 유지됩니다. "
        "prompt 변경은 새 version 으로 저장 (Versioned analysis 원칙) — 옛 버전도 보존."
    )

    # 현재 effective 상태 + 사용 가능 모델
    try:
        llm_snap = httpx.get(f"{API_BASE}/settings/llm", timeout=10.0).json()
        models = httpx.get(f"{API_BASE}/settings/llm/models", timeout=10.0).json()
    except Exception as e:                          # noqa: BLE001
        st.error(f"Settings API 호출 실패: {e}")
        st.stop()

    eff = llm_snap["effective"]
    code_def = llm_snap["config_defaults"]
    override = llm_snap["override"]
    providers = models["providers"]

    st.subheader("Default LLM")
    col1, col2 = st.columns(2)
    with col1:
        provider_options = ["openai", "claude", "ollama"]
        provider_idx = provider_options.index(eff["default_llm_provider"]) \
            if eff["default_llm_provider"] in provider_options else 2
        new_provider = st.selectbox(
            "Default provider", provider_options, index=provider_idx
        )
    with col2:
        ollama_models = providers["ollama"]["models"] or [eff["ollama_model"]]
        current_ollama = eff["ollama_model"]
        ollama_idx = ollama_models.index(current_ollama) if current_ollama in ollama_models else 0
        new_ollama_model = st.selectbox(
            "Ollama model (default provider 가 ollama 일 때 사용)",
            ollama_models, index=ollama_idx,
        )

    with st.expander("OpenAI / Claude 모델 (선택)", expanded=False):
        new_openai_model = st.text_input("OpenAI model", value=eff["openai_model"])
        new_anthropic_model = st.text_input("Anthropic model", value=eff["anthropic_model"])

    with st.expander("현재 상태 (debug)", expanded=False):
        st.write({"effective": eff, "config_defaults": code_def, "override": override})
        if providers["ollama"].get("error"):
            st.warning(f"Ollama API 조회 에러: {providers['ollama']['error']}")

    if st.button("저장 (LLM 설정)", type="primary"):
        with st.spinner("저장 중..."):
            r = httpx.put(
                f"{API_BASE}/settings/llm",
                json={
                    "default_llm_provider": new_provider,
                    "ollama_model": new_ollama_model,
                    "openai_model": new_openai_model,
                    "anthropic_model": new_anthropic_model,
                },
                timeout=10.0,
            )
            r.raise_for_status()
            st.success("저장됨. 다음 요청부터 새 default 가 적용됩니다.")
            st.rerun()

    st.divider()

    # ─── Prompts ──────────────────────────────────────────────
    st.subheader("System Prompts")
    prompts = llm_snap["prompts"]

    for name, label in (
        ("rag_system", "Ask (RAG) system prompt"),
        ("summary_system", "Ingest summary system prompt"),
    ):
        active = prompts.get(name, {})
        with st.expander(f"{label} — 활성 버전: `{active.get('version') or '미설정'}`",
                         expanded=False):
            new_content = st.text_area(
                "내용",
                value=active.get("content") or "",
                height=300,
                key=f"prompt_content_{name}",
            )
            note = st.text_input("변경 사유 (옵션)", key=f"prompt_note_{name}")
            cols = st.columns([1, 1, 2])
            if cols[0].button(f"새 버전으로 저장", key=f"save_{name}"):
                r = httpx.post(
                    f"{API_BASE}/settings/prompts/{name}",
                    json={"content": new_content, "note": note or None},
                    timeout=15.0,
                )
                if r.status_code == 200:
                    st.success("저장 + 활성화됨")
                    st.rerun()
                else:
                    st.error(f"실패: {r.status_code} {r.text}")
            if cols[1].button(f"버전 히스토리", key=f"hist_{name}"):
                rh = httpx.get(
                    f"{API_BASE}/settings/prompts/{name}/versions", timeout=10.0,
                ).json()
                st.write(rh)


# ─── Status ────────────────────────────────────────────────────
with tab_status:
    if st.button("health 확인"):
        r = httpx.get(f"{API_BASE}/health", timeout=10.0)
        st.json(r.json())
    st.caption(f"API base: `{API_BASE}` (LINKMIND_API_BASE 환경변수로 변경 가능)")
