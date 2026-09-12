from pathlib import Path
import json

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer


MODEL_NAME = "intfloat/multilingual-e5-small"

CHUNKS_FILE = Path("data/processed/chunks_pdf.json")

INDEX_FILE = Path("index/faiss_pdf.index")
METADATA_FILE = Path("index/metadata_pdf.json")


def load_chunks():
    with open(
        CHUNKS_FILE,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def main():

    print("=" * 70)
    print("ПОСТРОЕНИЕ FAISS-ИНДЕКСА ДЛЯ PDF")
    print("=" * 70)

    if not CHUNKS_FILE.exists():
        print(
            f"Ошибка: файл {CHUNKS_FILE} не найден."
        )
        return

    print()
    print("1. Загружаем PDF-чанки...")

    chunks = load_chunks()

    print(
        f"   Загружено чанков: {len(chunks)}"
    )

    print()
    print("2. Загружаем embedding-модель...")

    model = SentenceTransformer(
        MODEL_NAME
    )

    print(
        f"   Модель: {MODEL_NAME}"
    )

    print()
    print("3. Подготавливаем тексты...")

    documents = [
        "passage: " + chunk["text"]
        for chunk in chunks
    ]

    print()
    print("4. Строим embeddings...")

    embeddings = model.encode(
        documents,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    )

    embeddings = embeddings.astype(
        "float32"
    )

    dimension = embeddings.shape[1]

    print(
        f"   Размерность: {dimension}"
    )

    print(
        f"   Количество векторов: "
        f"{len(embeddings)}"
    )

    print()
    print("5. Создаем FAISS IndexFlatIP...")

    index = faiss.IndexFlatIP(
        dimension
    )

    index.add(
        embeddings
    )

    print(
        f"   Векторов в индексе: "
        f"{index.ntotal}"
    )

    print()
    print("6. Сохраняем индекс...")

    INDEX_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    faiss.write_index(
        index,
        str(INDEX_FILE),
    )

    print(
        f"   FAISS: {INDEX_FILE}"
    )

    print()
    print("7. Сохраняем метаданные...")

    with open(
        METADATA_FILE,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            chunks,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print(
        f"   Metadata: {METADATA_FILE}"
    )

    print()
    print("=" * 70)
    print("ГОТОВО")
    print("=" * 70)


if __name__ == "__main__":
    main()