from pathlib import Path
from llama_cpp import Llama


MODEL_PATH = Path("models/qwen2.5-3b-instruct-q4_k_m.gguf")


def main():
    print("=" * 60)
    print("ТЕСТ ЛОКАЛЬНОЙ LLM")
    print("=" * 60)

    print(f"Модель: {MODEL_PATH}")
    print("Загрузка модели...")

    llm = Llama(
        model_path=str(MODEL_PATH),
        n_ctx=4096,
        n_threads=8,
        verbose=False,
    )

    print("Модель загружена.")
    print()

    response = llm.create_chat_completion(
        messages=[
            {
                "role": "system",
                "content": "Ты полезный ассистент. Отвечай на русском языке."
            },
            {
                "role": "user",
                "content": "Кратко объясни, что такое корпоративная информационная система."
            }
        ],
        max_tokens=200,
        temperature=0.2,
    )

    answer = response["choices"][0]["message"]["content"]

    print("Ответ модели:")
    print("-" * 60)
    print(answer)
    print("-" * 60)


if __name__ == "__main__":
    main()