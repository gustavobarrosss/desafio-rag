import json

import pandas as pd
import streamlit as st


def render():
    st.title("Browser de Documentos")

    def _build_query(anos, situacoes, tipos, dl_status, parse_status, chunk_status, embed_status, text_search):
        conditions = []
        params: list = []

        if anos:
            placeholders = ",".join("?" * len(anos))
            conditions.append(f"ano IN ({placeholders})")
            params.extend(anos)

        if situacoes:
            placeholders = ",".join("?" * len(situacoes))
            conditions.append(f"situacao_doc IN ({placeholders})")
            params.extend(situacoes)

        if tipos:
            placeholders = ",".join("?" * len(tipos))
            conditions.append(f"tipo_pdf IN ({placeholders})")
            params.extend(tipos)

        for col, val in [
            ("status_download", dl_status),
            ("status_parse", parse_status),
            ("status_chunk", chunk_status),
            ("status_embed", embed_status),
        ]:
            if val and val != "todos":
                conditions.append(f"{col}=?")
                params.append(val)

        if text_search.strip():
            conditions.append("(titulo LIKE ? OR autor LIKE ? OR ementa LIKE ? OR assunto LIKE ?)")
            pattern = f"%{text_search.strip()}%"
            params.extend([pattern, pattern, pattern, pattern])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = (
            f"SELECT doc_id, ano, titulo, autor, situacao_doc, tipo_pdf, "
            f"status_download, status_parse, status_chunk, status_embed, pages, updated_at "
            f"FROM docs {where} ORDER BY ano DESC, doc_id LIMIT 501"
        )
        return sql, params

    with st.form("filters_form"):
        col1, col2, col3 = st.columns(3)
        anos = col1.multiselect("Ano(s)", options=list(range(2010, 2026)), default=[])
        situacoes = col2.multiselect(
            "Situação",
            options=["vigente", "revogada", "não classificada"],
            default=[],
        )
        tipos = col3.multiselect(
            "Tipo PDF",
            options=["digital", "scanned", "mixed"],
            default=[],
        )

        col4, col5, col6, col7 = st.columns(4)
        status_opts = ["todos", "ok", "pending", "error"]
        dl_status = col4.selectbox("Download", status_opts, index=0)
        parse_status = col5.selectbox("Parse", status_opts, index=0)
        chunk_status = col6.selectbox("Chunk", status_opts, index=0)
        embed_status = col7.selectbox("Embed", status_opts, index=0)

        text_search = st.text_input("Busca por texto (titulo / autor / ementa / assunto)", "")
        submitted = st.form_submit_button("Aplicar Filtros", type="primary")

    if submitted or "doc_browser_df" not in st.session_state:
        try:
            from src.state import connection
            sql, params = _build_query(
                [int(a) for a in anos],
                situacoes, tipos,
                dl_status, parse_status, chunk_status, embed_status,
                text_search,
            )
            with connection() as conn:
                cur = conn.execute(sql, params)
                rows = cur.fetchall()
                col_names = [d[0] for d in cur.description] if rows else []
            df = pd.DataFrame(rows, columns=col_names) if rows else pd.DataFrame()
            st.session_state["doc_browser_df"] = df
        except Exception as e:
            st.error(f"Erro ao consultar banco: {e}")
            st.session_state["doc_browser_df"] = pd.DataFrame()

    df: pd.DataFrame = st.session_state.get("doc_browser_df", pd.DataFrame())

    if df.empty:
        st.info("Nenhum documento encontrado. Clique em 'Aplicar Filtros' para carregar.")
    else:
        total = len(df)
        truncated = total > 500
        display_df = df.iloc[:500] if truncated else df

        count_msg = f"{min(total, 500)} documento(s) exibidos"
        if truncated:
            st.warning(count_msg + " (mais de 500 encontrados — refine os filtros)")
        else:
            st.caption(count_msg)

        selection = st.dataframe(
            display_df,
            use_container_width=True,
            selection_mode="single-row",
            on_select="rerun",
            key="doc_table",
        )

        selected_rows = selection.selection.get("rows", []) if hasattr(selection, "selection") else []
        if selected_rows:
            idx = selected_rows[0]
            doc_id = display_df.iloc[idx]["doc_id"]
            try:
                from src.state import get_doc
                row = get_doc(doc_id)
                if row:
                    st.subheader(f"Detalhes: {doc_id}")
                    record = dict(row)
                    if record.get("metadata_json"):
                        try:
                            record["metadata_json"] = json.loads(record["metadata_json"])
                        except Exception:
                            pass
                    st.json(record)
            except Exception as e:
                st.error(f"Erro ao carregar detalhes: {e}")
