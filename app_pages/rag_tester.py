import time

import pandas as pd
import streamlit as st


def render():
    st.title("RAG Tester")

    if "last_answer" not in st.session_state:
        st.session_state["last_answer"] = None
    if "last_chunks" not in st.session_state:
        st.session_state["last_chunks"] = []
    if "last_elapsed" not in st.session_state:
        st.session_state["last_elapsed"] = 0.0

    with st.expander("Filtros", expanded=False):
        top_k = st.slider("top_k (chunks retornados)", min_value=1, max_value=20, value=6)
        anos = st.multiselect(
            "Ano(s)",
            options=[str(y) for y in range(2010, 2026)],
            default=[],
            help="Deixe vazio para buscar em todos os anos",
        )
        situacoes = st.multiselect(
            "Situação do documento",
            options=["vigente", "revogada", "não classificada"],
            default=[],
        )

    question = st.text_area(
        "Pergunta",
        placeholder="Ex: O que é microgeração distribuída?",
        height=100,
    )
    col1, col2 = st.columns([1, 5])
    ask_btn = col1.button("Perguntar", type="primary", use_container_width=True)
    retrieval_only = col2.checkbox("Somente recuperação (sem LLM)", value=False)

    if ask_btn and question.strip():
        filters: dict = {}
        if anos:
            filters["ano"] = [int(a) for a in anos]
        if situacoes:
            filters["situacao_doc"] = situacoes

        if retrieval_only:
            try:
                from src.retriever import search
                with st.spinner("Buscando chunks..."):
                    t0 = time.perf_counter()
                    chunks = search(question, filters=filters if filters else None, top_k=top_k)
                    elapsed = time.perf_counter() - t0
                st.session_state["last_answer"] = None
                st.session_state["last_chunks"] = chunks
                st.session_state["last_elapsed"] = elapsed
            except Exception as e:
                st.error(f"Erro na recuperação: {e}")
        else:
            try:
                from src.qa import ask_sync
                with st.spinner("Buscando e gerando resposta..."):
                    t0 = time.perf_counter()
                    answer = ask_sync(question, filters=filters if filters else None, top_k=top_k)
                    elapsed = time.perf_counter() - t0
                st.session_state["last_answer"] = answer
                st.session_state["last_chunks"] = []
                st.session_state["last_elapsed"] = elapsed
            except Exception as e:
                if "ConnectError" in type(e).__name__ or "connect" in str(e).lower():
                    st.error("Qdrant não está disponível. Verifique se o container qdrant está rodando.")
                else:
                    st.error(f"Erro inesperado: {e}")

    elif ask_btn:
        st.warning("Digite uma pergunta antes de clicar em Perguntar.")

    answer = st.session_state.get("last_answer")
    chunks = st.session_state.get("last_chunks", [])
    elapsed = st.session_state.get("last_elapsed", 0.0)

    if answer is not None:
        st.divider()
        col_model, col_time = st.columns(2)
        col_model.caption(f"Modelo: `{answer.model}`")
        col_time.caption(f"Tempo: {elapsed:.1f}s")

        st.subheader("Resposta")
        st.markdown(answer.answer)

        if answer.citations:
            st.subheader("Citações")
            df_cit = pd.DataFrame(answer.citations)
            col_order = [
                c for c in
                ["doc_id", "article_ref", "page_start", "page_end", "situacao_doc", "score", "url"]
                if c in df_cit.columns
            ]
            df_cit = df_cit[col_order]
            if "score" in df_cit.columns:
                df_cit["score"] = df_cit["score"].map(
                    lambda x: f"{x:.3f}" if x is not None else ""
                )
            cfg = {}
            if "url" in df_cit.columns:
                cfg["url"] = st.column_config.LinkColumn("URL")
            st.dataframe(df_cit, column_config=cfg, use_container_width=True)

            st.subheader("Chunks recuperados")
            for i, cit in enumerate(answer.citations, 1):
                label = (
                    f"[{i}] {cit.get('doc_id', '')} | "
                    f"art={cit.get('article_ref', '')} | "
                    f"p.{cit.get('page_start', '')}-{cit.get('page_end', '')} | "
                    f"score={cit.get('score', 0):.3f}"
                )
                with st.expander(label):
                    st.caption("Texto completo disponível no modo 'Somente recuperação'.")

    elif chunks:
        st.divider()
        st.caption(f"Tempo: {elapsed:.1f}s | {len(chunks)} chunks")
        st.subheader("Chunks recuperados")
        for i, chunk in enumerate(chunks, 1):
            label = (
                f"[{i}] {chunk.doc_id} | art={chunk.article_ref or '-'} | "
                f"p.{chunk.payload.get('page_start', '')}-{chunk.payload.get('page_end', '')} | "
                f"score={chunk.score:.3f} | {chunk.payload.get('situacao_doc', '')}"
            )
            with st.expander(label):
                st.markdown(chunk.text)
                meta = {k: chunk.payload.get(k) for k in ["titulo", "ano", "autor", "url"] if chunk.payload.get(k)}
                if meta:
                    st.caption(" | ".join(f"{k}: {v}" for k, v in meta.items()))
