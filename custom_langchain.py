from httpx import AsyncClient
from langchain_ollama import ChatOllama

ollama_url = "http://localhost:11434"


class OllamaChatModel:
    def __init__(self, model):
        self.model = model
        self.llm = ChatOllama(model=self.model)

    @staticmethod
    async def get_chat_model():
        async with AsyncClient() as client:
            models = await client.get(ollama_url + "/api/tags")
            if models.status_code == 200:
                model_name = models.json()["models"][-1]["name"]
                return model_name
            else:
                raise Exception("Failed to get model list")

    def summarize(self, text: str):
        prompt = f"""
        You are a professional email summarizer.

        Your task is to read the given email and produce a clear, concise, and well-structured summary.

        Guidelines:
        - Capture the main purpose of the email.
        - Highlight key points, decisions, or requests.
        - Extract any important dates, deadlines, or action items.
        - Keep the summary brief (3–6 bullet points or short paragraph).
        - Maintain a professional and neutral tone.
        - Do not include unnecessary details or repetition.

        If the email contains action items, list them separately under "Action Items".

        Email:
        {text}

        Summary:
        """
        return self.llm.invoke(prompt)


async def main():
    ollama_chat = OllamaChatModel("lfm2.5-thinking:latest")
    model = await ollama_chat.get_chat_model()
    print(model)
    responce = ollama_chat.llm.invoke("hi")
    print(responce)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
