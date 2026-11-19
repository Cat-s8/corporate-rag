from pathlib import Path
import re
import time

from llama_cpp import Llama

try:
    from .search_hybrid import load_data, hybrid_search
except ImportError:
    from search_hybrid import load_data, hybrid_search


# ============================================================
# НАСТРОЙКИ
# ============================================================

MODEL_PATH = Path(__file__).resolve().parents[1] / "models/Qwen3-8B-Q4_K_M.gguf"

TOP_K = 5

# Ограничиваем размер каждого чанка,
# передаваемого языковой модели.
MAX_CHUNK_LENGTH = 1600


# ============================================================
# ПОСТРОЕНИЕ КОНТЕКСТА
# ============================================================

def build_context(results):
    """
    Формирует контекст для LLM
    из результатов гибридного поиска.

    В контекст передаются:
    - номер источника;
    - документ;
    - страница;
    - раздел;
    - подраздел;
    - пункт;
    - текст чанка.
    """

    parts = []

    for i, result in enumerate(
        results,
        start=1
    ):

        text = result["text"][
            :MAX_CHUNK_LENGTH
        ]

        source_info = [
            f"Источник {i}",
            f"Документ: {result['source']}",
        ]

        if result.get(
            "document_page"
        ) is not None:

            source_info.append(
                f"Страница: "
                f"{result['document_page']}"
            )

        if result.get(
            "section"
        ):

            source_info.append(
                f"Раздел: "
                f"{result['section']}"
            )

        if result.get(
            "subsection"
        ):

            source_info.append(
                f"Подраздел: "
                f"{result['subsection']}"
            )

        if result.get(
            "point"
        ):

            source_info.append(
                f"Пункт: "
                f"{result['point']}"
            )

        if result.get(
            "subpoint"
        ):

            source_info.append(
                f"Подпункт: "
                f"{result['subpoint']}"
            )

        source_block = "\n".join(
            source_info
        )

        parts.append(
            f"""{source_block}

Текст:
{text}
"""
        )

    return "\n\n".join(
        parts
    )


# ============================================================
# ГЕНЕРАЦИЯ ОТВЕТА
# ============================================================

def build_messages(question, context):
    system_prompt = """
Ты корпоративный AI-ассистент,
работающий с документацией организации.

Отвечай только на основании предоставленного
контекста из корпоративных документов.

ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА:

1. Не придумывай информацию,
   которой нет в предоставленном контексте.

2. Отвечай только тогда, когда найденный
   контекст ПРЯМО содержит информацию,
   необходимую для ответа.

3. Тематически похожий фрагмент
   не является достаточным основанием
   для ответа.

4. Если вопрос содержит конкретное условие,
   это условие также должно прямо присутствовать
   в документации либо однозначно следовать
   из приведенного текста.

Например:
- работа из дома;
- конкретная роль пользователя;
- конкретная версия программного обеспечения;
- определенный режим работы;
- конкретная функция;
- конкретный документ;
- конкретное ограничение.

5. Нельзя переносить общее правило
   на частный случай, если этот частный случай
   явно не описан.

Пример:
если в документе описан уровень подготовки
пользователя вообще, это НЕ является ответом
на вопрос о требованиях к сотруднику
при работе из дома.

6. Если прямого ответа нет,
   напиши точно:

"В предоставленных документах информация не найдена."

7. Отвечай на русском языке.

8. Отвечай кратко, конкретно и непосредственно
   на вопрос пользователя.

9. Не перечисляй сведения,
   которые пользователь не спрашивал.

10. Если вопрос просит инструкцию,
    сформируй последовательность действий.

11. Если вопрос касается прав,
    роли или ограничений,
    перечисли только относящиеся
    к этой роли права и ограничения.

12. Не выводи техническую информацию:
    FAISS score,
    BM25 score,
    RRF score,
    rank и другие внутренние показатели.

13. Не придумывай номера страниц,
    разделов, пунктов или документов.

14. Используй только те источники,
    которые действительно подтверждают
    сформированный ответ.

15. В самом конце ответа обязательно
    добавь отдельную служебную строку:

[SOURCES: номера источников]

Например:

[SOURCES: 1]

или:

[SOURCES: 1,3]

16. Указывай только источники,
    фактически использованные для ответа.

17. Если информации для ответа нет,
    обязательно напиши:

[SOURCES: none]

18. Не объясняй пользователю,
    почему был выбран тот или иной источник.

19. Не выводи свои рассуждения.
"""

    user_prompt = f"""
/no_think

Контекст из корпоративных документов:

{context}

Вопрос пользователя:

{question}

Сформируй краткий точный ответ
исключительно по приведенному контексту.

Если найденные фрагменты только тематически
похожи, но прямо не отвечают на вопрос,
скажи, что информация не найдена.

В конце обязательно добавь:

[SOURCES: ...]
"""

    return [{"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}]


def generate_answer(
    llm,
    question,
    context,
    temperature=0.2,
):
    """
    Генерирует ответ Qwen3.

    Возвращает:

    answer
        Финальный текст без <think>
        и без служебной строки SOURCES.

    used_source_numbers
        Номера источников,
        которые модель указала как использованные.

    sources_declared
        True:
            модель явно вернула [SOURCES: ...]
            или [SOURCES: none]

        False:
            модель вообще забыла вернуть
            служебную строку.
    """

    messages = build_messages(question, context)

    start_time = time.time()

    response = llm.create_chat_completion(
        messages=messages,

        max_tokens=300,

        temperature=temperature,

        top_p=0.8,

        top_k=20,
    )

    elapsed = (
        time.time()
        - start_time
    )

    usage = response.get(
        "usage",
        {}
    )

    completion_tokens = (
        usage.get(
            "completion_tokens",
            0
        )
    )

    print()

    print(
        f"Время генерации: "
        f"{elapsed:.2f} сек."
    )

    if completion_tokens > 0:

        speed = (
            completion_tokens
            / elapsed
        )

        print(
            f"Токенов: "
            f"{completion_tokens}"
        )

        print(
            f"Скорость: "
            f"{speed:.2f} ток/сек."
        )

    answer = (
        response["choices"][0]
        ["message"]["content"]
    )

    # --------------------------------------------------------
    # Удаляем thinking-блок Qwen3
    # --------------------------------------------------------

    answer = re.sub(
        r"<think>.*?</think>",
        "",
        answer,
        flags=(
            re.DOTALL
            | re.IGNORECASE
        ),
    ).strip()

    # --------------------------------------------------------
    # Извлекаем [SOURCES: ...]
    # --------------------------------------------------------

    source_match = re.search(
        r"\[SOURCES:\s*([^\]]+)\]",
        answer,
        flags=re.IGNORECASE,
    )

    used_source_numbers = []

    # Показывает, что модель
    # действительно сформировала
    # служебную строку SOURCES.
    sources_declared = (
        source_match is not None
    )

    if source_match:

        source_text = (
            source_match
            .group(1)
            .strip()
            .lower()
        )

        # [SOURCES: none]
        # означает намеренное отсутствие
        # источников.
        if source_text != "none":

            for value in (
                source_text.split(",")
            ):

                value = (
                    value.strip()
                )

                if value.isdigit():

                    used_source_numbers.append(
                        int(value)
                    )

    # --------------------------------------------------------
    # Удаляем служебную строку
    # из текста, который видит пользователь
    # --------------------------------------------------------

    answer = re.sub(
        r"\s*\[SOURCES:\s*[^\]]+\]\s*",
        "",
        answer,
        flags=re.IGNORECASE,
    ).strip()

    return (
        answer,
        used_source_numbers,
        sources_declared,
    )


# ============================================================
# ФОРМАТИРОВАНИЕ ИСТОЧНИКА
# ============================================================

def format_source(result):
    """
    Делает компактный человекочитаемый
    источник.

    Пример:

    appendix_1.pdf — стр. 13 —
    2.2 Подготовительные действия
    """

    parts = [
        result["source"]
    ]

    if result.get(
        "document_page"
    ) is not None:

        parts.append(
            f"стр. "
            f"{result['document_page']}"
        )

    # Выбираем самый точный
    # уровень иерархии.
    if result.get(
        "subpoint"
    ):

        parts.append(
            result["subpoint"]
        )

    elif result.get(
        "point"
    ):

        parts.append(
            result["point"]
        )

    elif result.get(
        "subsection"
    ):

        parts.append(
            result["subsection"]
        )

    elif result.get(
        "section"
    ):

        parts.append(
            result["section"]
        )

    return " — ".join(
        parts
    )


# ============================================================
# FALLBACK-ВЫБОР ИСТОЧНИКОВ
# ============================================================

def select_sources(
    results,
    limit=2
):
    """
    Используется только если Qwen
    вообще забыла вывести [SOURCES: ...].

    Берем несколько сильных результатов
    гибридного retrieval.
    """

    selected = []

    seen = set()

    for result in results:

        faiss_rank = (
            result.get(
                "faiss_rank"
            )
        )

        bm25_rank = (
            result.get(
                "bm25_rank"
            )
        )

        # Результат считаем сильным,
        # если он входит хотя бы
        # в TOP-3 одного из поисков.
        strong_result = (
            (
                faiss_rank is not None
                and faiss_rank <= 3
            )
            or
            (
                bm25_rank is not None
                and bm25_rank <= 3
            )
        )

        if not strong_result:
            continue

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

        seen.add(
            key
        )

        selected.append(
            result
        )

        if len(selected) >= limit:
            break

    return selected


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        "CORPORATE RAG — "
        "PDF + FAISS + BM25 + QWEN3"
    )

    print("=" * 70)

    print()
    print(
        "Загрузка поисковых компонентов..."
    )

    (
        model,
        index,
        metadata,
        bm25,
    ) = load_data()

    print()
    print(
        "Загрузка локальной LLM..."
    )

    llm = Llama(
        model_path=str(
            MODEL_PATH
        ),

        # Все возможные слои
        # отправляем на GPU через Vulkan.
        n_gpu_layers=-1,

        # Контекст модели.
        n_ctx=4096,

        # CPU используется
        # во вспомогательных операциях.
        n_threads=6,

        verbose=False,
    )

    print()
    print(
        "Система готова."
    )

    print(
        "Для выхода: exit"
    )

    # ========================================================
    # ЦИКЛ ВОПРОСОВ
    # ========================================================

    while True:

        print()

        question = input(
            "Вопрос: "
        ).strip()

        if question.lower() in (
            "exit",
            "quit",
            "выход",
        ):
            break

        if not question:
            continue

        # ----------------------------------------------------
        # 1. HYBRID RETRIEVAL
        # ----------------------------------------------------

        results = hybrid_search(
            question,
            model,
            index,
            metadata,
            bm25,
            top_k=TOP_K,
        )

        if not results:

            print()
            print("=" * 70)
            print("ОТВЕТ")
            print("=" * 70)

            print()
            print(
                "В предоставленных документах "
                "информация не найдена."
            )

            print()
            print(
                "ИСТОЧНИКИ"
            )

            print(
                "Нет."
            )

            continue

        # ----------------------------------------------------
        # 2. ФОРМИРУЕМ КОНТЕКСТ
        # ----------------------------------------------------

        context = build_context(
            results
        )

        # ----------------------------------------------------
        # 3. ГЕНЕРАЦИЯ
        # ----------------------------------------------------

        (
            answer,
            used_source_numbers,
            sources_declared,
        ) = generate_answer(
            llm,
            question,
            context,
        )

        # ----------------------------------------------------
        # 4. ВЫВОД ОТВЕТА
        # ----------------------------------------------------

        print()
        print("=" * 70)
        print("ОТВЕТ")
        print("=" * 70)

        print()
        print(
            answer
        )

        # ----------------------------------------------------
        # 5. ВЫБОР ИСТОЧНИКОВ
        # ----------------------------------------------------

        used_results = []

        # ------------------------------------------
        # Вариант А:
        # модель явно написала
        # [SOURCES: ...]
        # ------------------------------------------

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
                        results[
                            index_number
                        ]
                    )

            # Если модель написала:
            #
            # [SOURCES: none]
            #
            # used_source_numbers будет пустым.
            #
            # В этом случае fallback
            # НЕ используется.

        # ------------------------------------------
        # Вариант Б:
        # модель вообще забыла
        # строку SOURCES.
        # ------------------------------------------

        else:

            used_results = (
                select_sources(
                    results,
                    limit=2,
                )
            )

            # Последняя страховка.
            if (
                not used_results
                and results
            ):

                used_results = [
                    results[0]
                ]

        # ----------------------------------------------------
        # 6. ВЫВОД ИСТОЧНИКОВ
        # ----------------------------------------------------

        print()
        print(
            "ИСТОЧНИКИ"
        )

        # [SOURCES: none]
        if (
            sources_declared
            and not used_results
        ):

            print(
                "Нет прямого источника "
                "для ответа."
            )

            print()

            continue

        seen = set()

        display_number = 1

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

            seen.add(
                key
            )

            print(
                f"[{display_number}] "
                f"{format_source(result)}"
            )

            display_number += 1

        print()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
