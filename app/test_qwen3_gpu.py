from pathlib import Path
import time

from llama_cpp import Llama


MODEL_PATH = Path(
    "models/Qwen3-8B-Q4_K_M.gguf"
)


print("Загрузка модели...")

llm = Llama(
    model_path=str(MODEL_PATH),

    # Полностью пытаемся выгрузить модель в GPU.
    n_gpu_layers=-1,

    n_ctx=4096,

    # CPU всё равно участвует
    # в части операций.
    n_threads=6,

    verbose=True,
)


prompt = """
Ответь кратко на русском языке.

Что такое RAG и зачем он используется
при работе с корпоративными документами?
"""


start = time.time()

response = llm.create_chat_completion(
    messages=[
        {
            "role": "user",
            "content": prompt,
        }
    ],
    max_tokens=200,
    temperature=0.1,
)


elapsed = time.time() - start

text = (
    response["choices"][0]
    ["message"]["content"]
)

tokens = (
    response.get("usage", {})
    .get("completion_tokens", 0)
)


print()
print("=" * 60)
print("ОТВЕТ")
print("=" * 60)

print(text)

print()
print(f"Время: {elapsed:.2f} сек.")

if tokens:
    print(f"Токенов: {tokens}")
    print(
        f"Скорость: "
        f"{tokens / elapsed:.2f} ток/сек."
    )