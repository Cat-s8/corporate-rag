from pathlib import Path

from llama_cpp import Llama

from search import load_data, search

import time

MODEL_PATH = Path("models/qwen2.5-3b-instruct-q4_k_m.gguf")

TOP_K = 5


def build_context(results):
    parts = []

    MAX_CHUNK_LENGTH = 2500

    for i, result in enumerate(results, start=1):
        text = result["text"][:MAX_CHUNK_LENGTH]

        page = result.get("page")
        page_end = result.get("page_end")

        if page is not None and page_end is not None:
            if page == page_end:
                page_info = f"стр. {page}"
            else:
                page_info = f"стр. {page}–{page_end}"
        elif page is not None:
            page_info = f"стр. {page}"
        else:
            page_info = "страница не определена"

        parts.append(
            f"""ИСТОЧНИК {i}
Документ: {result['source']}
Страница: {page_info}
Раздел: {result['section']}
Подраздел: {result['subsection']}

Текст:
{text}
"""
        )

    return "\n\n".join(parts)

def generate_answer(llm, question, context):
    system_prompt = """Ты корпоративный AI-ассистент.

Отвечай только на основании предоставленного контекста из корпоративных документов.

Правила:
1. Не придумывай информацию, которой нет в контексте.
2. Если ответа в документах нет, честно скажи, что в предоставленных документах информация не найдена.
3. Отвечай на русском языке.
4. Отвечай конкретно и непосредственно на вопрос пользователя.
5. Используй только фрагменты, которые прямо относятся к заданному вопросу.
6. Игнорируй найденные фрагменты, которые относятся к другим темам и не помогают ответить на вопрос.
7. Не перечисляй дополнительную информацию, если пользователь о ней не спрашивал.
8. Если используешь информацию из контекста, указывай номер источника в формате [1], [2] и т.д.
9. Не придумывай номера страниц, названия документов, разделов или подразделов. Используй только те сведения об источнике, которые явно указаны в контексте.
10. Если информация отсутствует в контексте, не делай вывод на основе общих знаний."""

    user_prompt = f"""Контекст из корпоративных документов:

{context}

Вопрос пользователя:
{question}

Ответь на вопрос, используя только приведённый контекст."""

    start_time = time.time()

    response = llm.create_chat_completion(
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        max_tokens=500,
        temperature=0.1,
    )

    elapsed = time.time() - start_time

    usage = response.get("usage", {})
    completion_tokens = usage.get("completion_tokens", 0)

    if completion_tokens > 0:
        speed = completion_tokens / elapsed
        print(f"Время генерации: {elapsed:.2f} сек.")
        print(f"Сгенерировано токенов: {completion_tokens}")
        print(f"Скорость: {speed:.2f} ток/сек.")
    else:
        print(f"Время генерации: {elapsed:.2f} сек.")

    return response["choices"][0]["message"]["content"]

def main():
    print("=" * 60)
    print("CORPORATE RAG — ЛОКАЛЬНЫЙ AI-АССИСТЕНТ")
    print("=" * 60)
    print()

    print("Загрузка embedding-модели и FAISS...")
    embedding_model, index, metadata = load_data()

    print("Загрузка LLM...")
    llm = Llama(
        model_path=str(MODEL_PATH),
        n_ctx=4096,
        n_threads=8,
        verbose=False,
    )

    print("Все модели загружены.")
    print()
    print("Введите вопрос.")
    print("Для выхода: exit")
    print()

    while True:
        question = input("Вопрос: ").strip()

        if question.lower() in ("exit", "quit", "выход"):
            break

        if not question:
            continue

        print()
        print("Ищу информацию в документах...")

        results = search(
            question,
            embedding_model,
            index,
            metadata,
            top_k=TOP_K,
        )


        context = build_context(results)

        print("Генерирую ответ...")
        print()

        answer = generate_answer(
            llm,
            question,
            context,
        )

        print("=" * 60)
        print("ОТВЕТ")
        print("=" * 60)
        print(answer)
        print()

        print("Использованные источники:")

        for i, result in enumerate(results, start=1):
            page = result.get("page")
            page_end = result.get("page_end")

            if page is not None and page_end is not None:
                if page == page_end:
                    page_info = f"стр. {page}"
                else:
                    page_info = f"стр. {page}–{page_end}"
            elif page is not None:
                page_info = f"стр. {page}"
            else:
                page_info = "страница не определена"

            print(
                f"[{i}] {result['source']}, "
                f"{page_info}, "
                f"раздел «{result['section']}», "
                f"подраздел «{result['subsection']}» "
                f"(score={result['score']:.4f})"
            )



if __name__ == "__main__":
    main()