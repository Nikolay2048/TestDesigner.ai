from openai import OpenAI


class OpenRouterClient:

    def __init__(self, api_key: str):
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )

    def invoke(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.1,
    ) -> str:

        response = self.client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        return response.choices[0].message.content