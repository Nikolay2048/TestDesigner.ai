import requests
import time

MODELS = [
    "qwen3:14b",
    "qwen3.6:27b-q4_K_M",
    "qwen3:30b-a3b",
    "Qwen3-Coder:30B",
    "qwen3:32b",
    "deepseek-r1:32b",
]

PROMPT = """
Расскажи про квантование нейросетей.
Ответ примерно на 300 слов.
"""

url = "http://localhost:11434/api/generate"

results = []

for model in MODELS:
    print(f"\n=== {model} ===")

    start = time.perf_counter()

    r = requests.post(
        url,
        json={
            "model": model,
            "prompt": PROMPT,
            "stream": False,
        },
        timeout=3600,
    )

    end = time.perf_counter()

    data = r.json()

    load_duration = data.get("load_duration", 0) / 1e9
    prompt_eval_duration = data.get("prompt_eval_duration", 0) / 1e9
    eval_duration = data.get("eval_duration", 0) / 1e9

    prompt_tokens = data.get("prompt_eval_count", 0)
    output_tokens = data.get("eval_count", 0)

    tps = (
        output_tokens / eval_duration
        if eval_duration > 0
        else 0
    )

    row = {
        "model": model,
        "wall_time": round(end - start, 2),
        "load_s": round(load_duration, 2),
        "prompt_s": round(prompt_eval_duration, 2),
        "gen_s": round(eval_duration, 2),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "tps": round(tps, 2),
    }

    results.append(row)

    for k, v in row.items():
        print(f"{k:15}: {v}")

print("\n\nSUMMARY")
for r in results:
    print(r)