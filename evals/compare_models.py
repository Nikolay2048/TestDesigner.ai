import os
import json
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)


REQUIREMENTS = """
Сценарий: бронирование автомобиля в каршеринге.

Пользователь должен:
1. Получить список доступных автомобилей.
2. Выбрать автомобиль.
3. Создать бронирование.
4. Получить созданное бронирование.
5. Оплатить бронирование.

Ограничения:
- Нельзя бронировать недоступный автомобиль.
- startDate должен быть в будущем.
- endDate должен быть позже startDate.
- Оплата возможна только для существующего бронирования.
- Нельзя оплатить бронирование дважды.
"""


SWAGGER_FRAGMENT = """
openapi: 3.0.0
paths:
  /v1/vehicles/available:
    get:
      summary: Get available vehicles
      responses:
        '200':
          description: Available vehicles
  /v1/bookings:
    post:
      summary: Create booking
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [userId, vehicleId, startDate, endDate]
              properties:
                userId:
                  type: string
                vehicleId:
                  type: string
                startDate:
                  type: string
                endDate:
                  type: string
      responses:
        '201':
          description: Booking created
        '400':
          description: Invalid dates or vehicle unavailable
  /v1/bookings/{bookingId}:
    get:
      summary: Get booking by id
      parameters:
        - name: bookingId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Booking found
        '404':
          description: Booking not found
  /v1/bookings/{bookingId}/payments:
    post:
      summary: Pay booking
      parameters:
        - name: bookingId
          in: path
          required: true
          schema:
            type: string
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [amount, paymentMethod]
              properties:
                amount:
                  type: number
                paymentMethod:
                  type: string
                  enum: [CARD, SBP]
      responses:
        '200':
          description: Payment accepted
        '400':
          description: Booking already paid or invalid amount
        '404':
          description: Booking not found
"""


PROMPT = f"""
Ты — агент тест-дизайна для REST API.

На входе есть постановка системного анализа и фрагмент Swagger/OpenAPI.

Твоя задача:
1. Построить корректный бизнес-флоу.
2. Определить REST-запросы по шагам.
3. Определить переменные, которые нужно извлекать из ответов.
4. Определить позитивные проверки.
5. Определить негативные проверки.
6. Найти возможные проблемы или неясности в постановке/API.

Ответ верни строго в JSON без markdown.

JSON-схема ответа:
{{
  "business_flow": [
    {{
      "step": 1,
      "name": "...",
      "method": "...",
      "path": "...",
      "depends_on": [],
      "extract_variables": []
    }}
  ],
  "context_variables": [
    {{
      "name": "...",
      "source_step": 1,
      "json_path": "...",
      "usage": "..."
    }}
  ],
  "positive_checks": ["..."],
  "negative_checks": ["..."],
  "risks_and_questions": ["..."]
}}

Постановка:
{REQUIREMENTS}

Swagger:
{SWAGGER_FRAGMENT}
"""


def call_openrouter(prompt: str) -> str:
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )

    response = client.chat.completions.create(
        model=os.getenv("OPENROUTER_MODEL", "qwen/qwen3-32b"),
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0.1,
    )

    return response.choices[0].message.content


def call_ollama(prompt: str) -> str:
    model = os.getenv("OLLAMA_MODEL", "qwen3:14b")

    response = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "options": {
                "temperature": 0.1
            }
        },
        timeout=300,
    )
    response.raise_for_status()

    return response.json()["message"]["content"]


def save_result(name: str, content: str) -> None:
    path = RESULTS_DIR / f"{name}.txt"
    path.write_text(content, encoding="utf-8")


def try_parse_json(content: str):
    try:
        return json.loads(content)
    except Exception:
        return None


def main():
    print("Running local Ollama model...")
    local_start = time.time()
    local_answer = call_ollama(PROMPT)
    local_time = time.time() - local_start

    print("Running OpenRouter model...")
    api_start = time.time()
    api_answer = call_openrouter(PROMPT)
    api_time = time.time() - api_start

    save_result("qwen3_14b_local", local_answer)
    save_result("qwen3_32b_openrouter", api_answer)

    local_json = try_parse_json(local_answer)
    api_json = try_parse_json(api_answer)

    print("\n=== RESULT ===")
    print(f"Local model time: {local_time:.1f} sec")
    print(f"API model time:   {api_time:.1f} sec")
    print(f"Local valid JSON: {local_json is not None}")
    print(f"API valid JSON:   {api_json is not None}")

    print("\nSaved:")
    print("results/qwen3_14b_local.txt")
    print("results/qwen3_32b_openrouter.txt")


if __name__ == "__main__":
    main()