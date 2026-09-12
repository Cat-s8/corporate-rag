from search_hybrid import (
    load_data,
    search_faiss,
    search_bm25,
    reciprocal_rank_fusion,
)


QUESTIONS = [
    "Как включить процессы обработки документов?",
    "Какие права имеет оператор выгрузки данных?",
    "Какие требования предъявляются к программному обеспечению АРМ?",
    "Какие браузеры поддерживаются?",
    "Как выполняется проверка работоспособности ИС?",
    "Что такое технологическая карта?",
    "Как настроить использование электронной подписи?",
    "Какие функции выполняет администратор выгрузки данных?",
    "КриптоПро CSP 5.0",
    "Оператор выгрузки данных",
    "3.1.1.1",
    "Анализ данных средствами произвольного запроса",
    "Какие требования предъявляются к сотрудникам при работе из дома?",
    "Можно ли работать в системе с мобильного телефона?",
    "Есть ли требования к использованию VPN?",
]


def format_result(idx, metadata):
    chunk = metadata[idx]

    parts = [
        chunk["source"],
    ]

    if chunk.get("document_page") is not None:
        parts.append(
            f"стр. {chunk['document_page']}"
        )

    if chunk.get("subpoint"):
        parts.append(
            chunk["subpoint"]
        )

    elif chunk.get("point"):
        parts.append(
            chunk["point"]
        )

    elif chunk.get("subsection"):
        parts.append(
            chunk["subsection"]
        )

    elif chunk.get("section"):
        parts.append(
            chunk["section"]
        )

    return " — ".join(parts)


def print_top_results(
    title,
    results,
    metadata,
    limit=3,
):
    print()
    print(title)

    for i, result in enumerate(
        results[:limit],
        start=1,
    ):
        idx = result["idx"]

        print(
            f"  [{i}] "
            f"{format_result(idx, metadata)}"
        )

        if "score" in result:
            print(
                f"      score="
                f"{result['score']:.4f}"
            )


def main():

    print("=" * 80)
    print("ОЦЕНКА RETRIEVAL: FAISS vs HYBRID")
    print("=" * 80)

    model, index, metadata, bm25 = (
        load_data()
    )

    for number, question in enumerate(
        QUESTIONS,
        start=1,
    ):

        print()
        print("=" * 80)

        print(
            f"{number}. {question}"
        )

        print("=" * 80)

        # ------------------------------
        # FAISS
        # ------------------------------

        faiss_results = search_faiss(
            question,
            model,
            index,
            top_k=10,
        )

        print_top_results(
            "FAISS TOP-3:",
            faiss_results,
            metadata,
        )

        # ------------------------------
        # BM25
        # ------------------------------

        bm25_results = search_bm25(
            question,
            bm25,
            top_k=10,
        )

        print_top_results(
            "BM25 TOP-3:",
            bm25_results,
            metadata,
        )

        # ------------------------------
        # HYBRID RRF
        # ------------------------------

        fused = reciprocal_rank_fusion(
            faiss_results,
            bm25_results,
        )

        hybrid_results = []

        for idx, fusion in fused[:3]:

            hybrid_results.append({
                "idx": idx,
                "score": fusion["rrf_score"],
            })

        print_top_results(
            "HYBRID TOP-3:",
            hybrid_results,
            metadata,
        )

    print()
    print("=" * 80)
    print("ГОТОВО")
    print("=" * 80)


if __name__ == "__main__":
    main()