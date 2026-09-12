from pathlib import Path

import streamlit as st
from llama_cpp import Llama

from search_hybrid import (
    load_data,
    hybrid_search,
)

from chat_hybrid import (
    MODEL_PATH,
    TOP_K,
    build_context,
    generate_answer,
    format_source,
    select_sources,
)


# ============================================================
# НАСТРОЙКА СТРАНИЦЫ
# ============================================================

st.set_page_config(
    page_title="Корпоративная RAG-система",
    page_icon="📚",
    layout="wide",
)


# ============================================================
# CSS
# ============================================================

st.markdown(
    """
    <style>

    .block-container {
        max-width: 1400px;
        padding-top: 2rem;
        padding-bottom: 2rem;
    }

    .system-status {
        padding: 10px 14px;
        border-radius: 8px;
        border: 1px solid #d0d0d0;
        margin-bottom: 15px;
    }

    .source-card {
        padding: 12px;
        border-radius: 8px;
        border: 1px solid #d0d0d0;
        margin-bottom: 8px;
    }

    .small-text {
        font-size: 0.85rem;
        opacity: 0.8;
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# ЗАГРУЗКА RAG
# ============================================================

@st.cache_resource
def load_rag():
    """
    Загружается один раз:
    - embedding model;
    - FAISS;
    - metadata;
    - BM25;
    - Qwen3.
    """

    model, index, metadata, bm25 = (
        load_data()
    )

    llm = Llama(
        model_path=str(MODEL_PATH),
        n_gpu_layers=-1,
        n_ctx=4096,
        n_threads=6,
        verbose=False,
    )

    return (
        model,
        index,
        metadata,
        bm25,
        llm,
    )


# ============================================================
# ВЫБОР ИСТОЧНИКОВ
# ============================================================

def get_used_sources(
    results,
    used_source_numbers,
    sources_declared,
):
    """
    Логика такая же, как в chat_hybrid.py.

    Если LLM указала SOURCES:
        используем их.

    Если написала SOURCES: none:
        ничего не выводим.

    Если вообще забыла SOURCES:
        используем retrieval fallback.
    """

    used_results = []

    if sources_declared:

        for source_number in (
            used_source_numbers
        ):

            index_number = (
                source_number - 1
            )

            if (
                0
                <= index_number
                < len(results)
            ):

                used_results.append(
                    results[index_number]
                )

    else:

        used_results = select_sources(
            results,
            limit=2,
        )

        if (
            not used_results
            and results
        ):

            used_results = [
                results[0]
            ]

    # Убираем дубли

    unique_results = []

    seen = set()

    for result in used_results:

        key = (
            result["source"],
            result.get(
                "document_page"
            ),
            result.get(
                "section"
            ),
            result.get(
                "subsection"
            ),
            result.get(
                "point"
            ),
            result.get(
                "subpoint"
            ),
        )

        if key in seen:
            continue

        seen.add(key)

        unique_results.append(
            result
        )

    return unique_results


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title(
    "📚 База знаний"
)

DOCUMENTS = [
    "appendix_1.pdf",
    "appendix_2.pdf",
    "appendix_3.pdf",
    "quide.pdf",
]

for document in DOCUMENTS:

    st.sidebar.write(
        f"✅ {document}"
    )

st.sidebar.divider()

st.sidebar.markdown(
    """
    **Режим работы**

    🟢 Локальная обработка

    🔒 Без внешних API
    """
)

st.sidebar.divider()

st.sidebar.caption(
    "FAISS + BM25 + RRF"
)

st.sidebar.caption(
    "Qwen3-8B"
)


# ============================================================
# ЗАГОЛОВОК
# ============================================================

st.title(
    "Корпоративная информационно-справочная система"
)

st.caption(
    "Локальная RAG-система поиска "
    "и анализа корпоративной документации"
)


# ============================================================
# ЗАГРУЗКА МОДЕЛЕЙ
# ============================================================

with st.spinner(
    "Загрузка базы знаний и языковой модели..."
):

    (
        embedding_model,
        faiss_index,
        metadata,
        bm25,
        llm,
    ) = load_rag()


# ============================================================
# СТАТУС
# ============================================================

st.markdown(
    f"""
    <div class="system-status">
        🟢 <b>Система готова</b><br>
        <span class="small-text">
        Индексировано фрагментов: {len(metadata)}
        </span>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# ИСТОРИЯ
# ============================================================

if "messages" not in st.session_state:

    st.session_state.messages = []


# ============================================================
# ВЫВОД ИСТОРИИ
# ============================================================

for message in (
    st.session_state.messages
):

    with st.chat_message(
        message["role"]
    ):

        st.markdown(
            message["content"]
        )

        if (
            message["role"]
            == "assistant"
            and message.get(
                "sources"
            )
        ):

            with st.expander(
                "Источники"
            ):

                for i, source in enumerate(
                    message["sources"],
                    start=1,
                ):

                    st.markdown(
                        f"**[{i}] "
                        f"{format_source(source)}**"
                    )

                    text = source.get(
                        "text",
                        ""
                    )

                    if text:

                        st.caption(
                            text[:1200]
                        )


# ============================================================
# ВВОД ВОПРОСА
# ============================================================

question = st.chat_input(
    "Введите вопрос по корпоративной документации..."
)


# ============================================================
# ОБРАБОТКА ВОПРОСА
# ============================================================

if question:

    # --------------------------------------------------------
    # Пользователь
    # --------------------------------------------------------

    st.session_state.messages.append(
        {
            "role": "user",
            "content": question,
        }
    )

    with st.chat_message(
        "user"
    ):

        st.markdown(
            question
        )

    # --------------------------------------------------------
    # Ассистент
    # --------------------------------------------------------

    with st.chat_message(
        "assistant"
    ):

        with st.status(
            "Поиск информации...",
            expanded=False,
        ) as status:

            # ----------------------------------------------
            # 1. HYBRID SEARCH
            # ----------------------------------------------

            results = hybrid_search(
                question,
                embedding_model,
                faiss_index,
                metadata,
                bm25,
                top_k=TOP_K,
            )

            status.update(
                label="Анализ найденных документов..."
            )

            # ----------------------------------------------
            # Нет retrieval результатов
            # ----------------------------------------------

            if not results:

                answer = (
                    "В предоставленных документах "
                    "информация не найдена."
                )

                used_sources = []

            else:

                # ------------------------------------------
                # 2. CONTEXT
                # ------------------------------------------

                context = build_context(
                    results
                )

                status.update(
                    label="Формирование ответа..."
                )

                # ------------------------------------------
                # 3. QWEN
                # ------------------------------------------

                (
                    answer,
                    used_source_numbers,
                    sources_declared,
                ) = generate_answer(
                    llm,
                    question,
                    context,
                )

                # ------------------------------------------
                # 4. SOURCES
                # ------------------------------------------

                used_sources = (
                    get_used_sources(
                        results,
                        used_source_numbers,
                        sources_declared,
                    )
                )

            status.update(
                label="Готово",
                state="complete",
            )

        # ----------------------------------------------------
        # Ответ
        # ----------------------------------------------------

        st.markdown(
            answer
        )

        # ----------------------------------------------------
        # Источники
        # ----------------------------------------------------

        if used_sources:

            with st.expander(
                f"📄 Источники ({len(used_sources)})"
            ):

                for i, source in enumerate(
                    used_sources,
                    start=1,
                ):

                    st.markdown(
                        f"### [{i}] "
                        f"{format_source(source)}"
                    )

                    # Показываем иерархию
                    if source.get(
                        "section"
                    ):

                        st.caption(
                            f"Раздел: "
                            f"{source['section']}"
                        )

                    if source.get(
                        "subsection"
                    ):

                        st.caption(
                            f"Подраздел: "
                            f"{source['subsection']}"
                        )

                    if source.get(
                        "point"
                    ):

                        st.caption(
                            f"Пункт: "
                            f"{source['point']}"
                        )

                    if source.get(
                        "subpoint"
                    ):

                        st.caption(
                            f"Подпункт: "
                            f"{source['subpoint']}"
                        )

                    # Показываем сам фрагмент
                    text = source.get(
                        "text",
                        ""
                    )

                    if text:

                        st.text_area(
                            "Фрагмент документа",
                            text[:1600],
                            height=180,
                            disabled=True,
                            key=(
                                f"source_"
                                f"{len(st.session_state.messages)}_"
                                f"{i}"
                            ),
                        )

        else:

            st.caption(
                "Прямой источник для ответа отсутствует."
            )

    # --------------------------------------------------------
    # Записываем ответ в историю
    # --------------------------------------------------------

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": answer,
            "sources": used_sources,
        }
    )