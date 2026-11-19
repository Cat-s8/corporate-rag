from pathlib import Path
import json
import re

import faiss
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer


MODEL_NAME = "intfloat/multilingual-e5-small"

ROOT = Path(__file__).resolve().parents[1]
INDEX_FILE = ROOT / "index/faiss_pdf.index"
METADATA_FILE = ROOT / "index/metadata_pdf.json"

TOP_K_FAISS = 10
TOP_K_BM25 = 10
TOP_K_FINAL = 5

RRF_K = 60


def tokenize(text):
    """
    Простая токенизация для BM25.
    Сохраняем цифры и русские/латинские слова.
    """

    text = text.lower()

    tokens = re.findall(
        r"[a-zа-яё0-9]+(?:[._/-][a-zа-яё0-9]+)*",
        text,
        flags=re.IGNORECASE,
    )

    return tokens


def load_metadata():
    with open(
        METADATA_FILE,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def build_bm25(metadata):
    """
    Строим BM25-корпус из текста и структурных метаданных.
    """

    corpus = []

    for chunk in metadata:

        searchable_text = " ".join([
            chunk.get("document_title", ""),
            chunk.get("section", ""),
            chunk.get("subsection", ""),
            chunk.get("point", ""),
            chunk.get("subpoint", ""),
            chunk.get("text", ""),
        ])

        corpus.append(
            tokenize(searchable_text) or ["__empty_document__"]
        )

    return BM25Okapi(corpus)


def load_data():
    print("Загрузка embedding-модели...")

    model = SentenceTransformer(
        MODEL_NAME
    )

    print("Загрузка FAISS PDF-индекса...")

    index = faiss.read_index(
        str(INDEX_FILE)
    )

    print("Загрузка PDF-метаданных...")

    metadata = load_metadata()

    print("Построение BM25...")

    bm25 = build_bm25(
        metadata
    )

    return model, index, metadata, bm25


def search_faiss(
    query,
    model,
    index,
    top_k=TOP_K_FAISS,
):
    query_text = "query: " + query

    vector = model.encode(
        [query_text],
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype("float32")

    scores, indices = index.search(
        vector,
        top_k,
    )

    results = []

    for rank, (score, idx) in enumerate(
        zip(scores[0], indices[0]),
        start=1,
    ):

        if idx < 0:
            continue

        results.append({
            "idx": int(idx),
            "rank": rank,
            "score": float(score),
        })

    return results


def search_bm25(
    query,
    bm25,
    top_k=TOP_K_BM25,
):
    query_tokens = tokenize(query)

    scores = bm25.get_scores(
        query_tokens
    )

    ranked_indices = np.argsort(
        scores
    )[::-1][:top_k]

    results = []

    for rank, idx in enumerate(
        ranked_indices,
        start=1,
    ):

        score = float(
            scores[idx]
        )

        results.append({
            "idx": int(idx),
            "rank": rank,
            "score": score,
        })

    return results


def reciprocal_rank_fusion(
    faiss_results,
    bm25_results,
):
    """
    Объединяем два рейтинга через RRF.

    RRF score =
        1 / (K + rank_faiss)
        +
        1 / (K + rank_bm25)
    """

    fused = {}

    for result in faiss_results:

        idx = result["idx"]

        if idx not in fused:
            fused[idx] = {
                "rrf_score": 0.0,
                "faiss_rank": None,
                "faiss_score": None,
                "bm25_rank": None,
                "bm25_score": None,
            }

        fused[idx]["faiss_rank"] = (
            result["rank"]
        )

        fused[idx]["faiss_score"] = (
            result["score"]
        )

        fused[idx]["rrf_score"] += (
            1.0 /
            (RRF_K + result["rank"])
        )

    for result in bm25_results:

        idx = result["idx"]

        if idx not in fused:
            fused[idx] = {
                "rrf_score": 0.0,
                "faiss_rank": None,
                "faiss_score": None,
                "bm25_rank": None,
                "bm25_score": None,
            }

        fused[idx]["bm25_rank"] = (
            result["rank"]
        )

        fused[idx]["bm25_score"] = (
            result["score"]
        )

        fused[idx]["rrf_score"] += (
            1.0 /
            (RRF_K + result["rank"])
        )

    ranked = sorted(
        fused.items(),
        key=lambda item: (
            item[1]["rrf_score"]
        ),
        reverse=True,
    )

    return ranked


def hybrid_search(
    query,
    model,
    index,
    metadata,
    bm25,
    top_k=TOP_K_FINAL,
):
    faiss_results = search_faiss(
        query,
        model,
        index,
    )

    bm25_results = search_bm25(
        query,
        bm25,
    )

    fused_results = (
        reciprocal_rank_fusion(
            faiss_results,
            bm25_results,
        )
    )

    final_results = []

    for idx, fusion in fused_results[:top_k]:

        chunk = metadata[idx]

        final_results.append({
            "chunk_id": chunk["chunk_id"],

            "source": chunk["source"],
            "document_title": chunk.get(
                "document_title",
                "",
            ),
            "document_code": chunk.get(
                "document_code",
                "",
            ),

            "pdf_page": chunk.get(
                "pdf_page"
            ),

            "document_page": chunk.get(
                "document_page"
            ),

            "section": chunk.get(
                "section",
                "",
            ),

            "subsection": chunk.get(
                "subsection",
                "",
            ),

            "point": chunk.get(
                "point",
                "",
            ),

            "subpoint": chunk.get(
                "subpoint",
                "",
            ),

            "text": chunk["text"],

            "rrf_score": fusion[
                "rrf_score"
            ],

            "faiss_rank": fusion[
                "faiss_rank"
            ],

            "faiss_score": fusion[
                "faiss_score"
            ],

            "bm25_rank": fusion[
                "bm25_rank"
            ],

            "bm25_score": fusion[
                "bm25_score"
            ],
        })

    return final_results


def print_source(result):
    print(
        f"Документ: {result['source']}"
    )

    if result["document_page"] is not None:
        print(
            f"Страница документа: "
            f"{result['document_page']}"
        )

    if (
        result["pdf_page"] is not None
        and result["pdf_page"]
        != result["document_page"]
    ):
        print(
            f"Физическая страница PDF: "
            f"{result['pdf_page']}"
        )

    if result["section"]:
        print(
            f"Раздел: "
            f"{result['section']}"
        )

    if result["subsection"]:
        print(
            f"Подраздел: "
            f"{result['subsection']}"
        )

    if result["point"]:
        print(
            f"Пункт: "
            f"{result['point']}"
        )

    if result["subpoint"]:
        print(
            f"Подпункт: "
            f"{result['subpoint']}"
        )


def main():

    if not INDEX_FILE.exists():

        print(
            "Ошибка: PDF FAISS-индекс "
            "не найден."
        )

        print(
            "Запусти:"
        )

        print(
            "python app\\build_index_pdf.py"
        )

        return

    model, index, metadata, bm25 = (
        load_data()
    )

    print()
    print("=" * 70)
    print("HYBRID SEARCH — FAISS + BM25")
    print("=" * 70)

    print()
    print("Введите вопрос.")
    print("Для выхода: exit")
    print()

    while True:

        query = input(
            "Вопрос: "
        ).strip()

        if query.lower() in (
            "exit",
            "quit",
            "выход",
        ):
            break

        if not query:
            continue

        results = hybrid_search(
            query,
            model,
            index,
            metadata,
            bm25,
        )

        print()
        print("-" * 70)

        print(
            f"ТОП-{len(results)} "
            f"ГИБРИДНЫХ РЕЗУЛЬТАТОВ"
        )

        print("-" * 70)

        for i, result in enumerate(
            results,
            start=1,
        ):

            print()
            print(f"[{i}]")

            print_source(
                result
            )

            print(
                f"RRF score: "
                f"{result['rrf_score']:.6f}"
            )

            print(
                f"FAISS: "
                f"rank={result['faiss_rank']}, "
                f"score="
                f"{result['faiss_score']}"
            )

            print(
                f"BM25: "
                f"rank={result['bm25_rank']}, "
                f"score="
                f"{result['bm25_score']}"
            )

            print()
            print(
                result["text"][:1800]
            )

        print()


if __name__ == "__main__":
    main()
