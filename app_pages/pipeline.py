import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def render():
    st.title("Pipeline — Status")

    def _load_counts():
        from src.state import counts
        try:
            return counts()
        except Exception as e:
            st.error(f"Erro ao ler state.sqlite: {e}")
            return {}

    def _render_counts(data: dict):
        stages = ["download", "parse", "chunk", "embed"]
        labels = {"download": "Download", "parse": "Parse", "chunk": "Chunk", "embed": "Embed"}
        cols = st.columns(len(stages))

        for col, stage in zip(cols, stages):
            s = data.get(stage, {})
            ok = s.get("ok", 0)
            pending = s.get("pending", 0)
            error = s.get("error", 0)
            total = ok + pending + error
            with col:
                st.subheader(labels[stage])
                st.metric("OK", ok)
                st.metric("Pendente", pending)
                st.metric("Erro", error, delta=f"-{error}" if error else None, delta_color="inverse")
                st.progress(ok / total if total else 0)

    def _render_chart(data: dict):
        stages = ["download", "parse", "chunk", "embed"]
        ok_vals = [data.get(s, {}).get("ok", 0) for s in stages]
        pending_vals = [data.get(s, {}).get("pending", 0) for s in stages]
        error_vals = [data.get(s, {}).get("error", 0) for s in stages]

        fig = go.Figure(data=[
            go.Bar(name="OK", x=stages, y=ok_vals, marker_color="#2ecc71"),
            go.Bar(name="Pendente", x=stages, y=pending_vals, marker_color="#f39c12"),
            go.Bar(name="Erro", x=stages, y=error_vals, marker_color="#e74c3c"),
        ])
        fig.update_layout(
            barmode="stack",
            title="Progresso por estágio",
            xaxis_title="Estágio",
            yaxis_title="Documentos",
            legend_title="Status",
            height=350,
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)

    def _render_errors():
        from src.state import connection
        with st.expander("Documentos com erro"):
            try:
                with connection() as conn:
                    rows = conn.execute(
                        "SELECT doc_id, error, updated_at FROM docs "
                        "WHERE status_download='error' OR status_parse='error' "
                        "OR status_chunk='error' OR status_embed='error' "
                        "ORDER BY updated_at DESC LIMIT 200"
                    ).fetchall()
                if rows:
                    df = pd.DataFrame(rows, columns=["doc_id", "error", "updated_at"])
                    st.dataframe(df, use_container_width=True)
                else:
                    st.info("Nenhum erro encontrado.")
            except Exception as e:
                st.error(f"Erro ao consultar erros: {e}")

    auto_refresh = st.checkbox("Auto-refresh (30s)", value=False)

    data = _load_counts()
    if data:
        _render_counts(data)
        st.divider()
        _render_chart(data)
        st.divider()
        _render_errors()
    else:
        st.info("Nenhum dado encontrado. Execute `python run_pipeline.py init` primeiro.")

    if auto_refresh:
        time.sleep(30)
        st.rerun()
