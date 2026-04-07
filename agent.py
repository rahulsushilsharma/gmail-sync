from asyncio import run

from httpx import AsyncClient
from langchain_ollama import ChatOllama

models = []


async def get_models():
    async with AsyncClient() as client:
        res = await client.get("http://localhost:11434/api/tags")
        return res.json()


async def main():
    models = await get_models()

    print(models)
    llm = ChatOllama(model=models["models"][-1]["model"])
    res = llm.invoke("hi")
    print(res)
    

if __name__ == "__main__":
    run(main())
# llm = ChatOllama()


