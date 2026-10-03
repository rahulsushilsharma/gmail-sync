import os
from asyncio import run
from enum import Enum
from typing import Annotated, List, TypedDict

from dotenv import load_dotenv
from httpx import AsyncClient
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.messages.human import HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_openrouter import ChatOpenRouter
from langgraph.graph import END, START
from langgraph.graph.message import add_messages
from langgraph.graph.state import StateGraph
from langgraph.prebuilt.tool_node import ToolNode

load_dotenv()
models = []
open_router_model = "nvidia/nemotron-nano-9b-v2:free"
groq_model = "openai/gpt-oss-20b"
open_router_key = os.getenv("OPENROUTER_API_KEY")


class Provider(Enum):
    ollama = "ollama"
    open_router = "open_router"
    groq = "groq"


class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage | ToolMessage], add_messages]


@tool
def mul(a: int, b: int) -> int:
    """use this funcion to multiply two int numbers"""
    return a * b


def get_llm(provider: Provider, model: str):
    if provider.value == "ollama":
        return ChatOllama(model=model)
    elif provider.value == "groq":
        return ChatGroq(model=model)
    elif provider.value == "open_router":
        return ChatOpenRouter(model=model)


def llm_chain_node(state: AgentState) -> AgentState:
    prompt = PromptTemplate.from_template("""
        system : reply in a funny mannar
        user: {querry}

        """)

    llm = get_llm(Provider.groq, groq_model)
    if not llm:
        return state

    chain = prompt | llm
    res = chain.invoke({"history": state["messages"]})

    state["messages"] = [res]
    return state


def get_avilable_tools():
    return [mul]


def router(state: AgentState):
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        return "tools"
    else:
        return "end"


def create_agent():
    graph = StateGraph(AgentState)
    tools = get_avilable_tools()

    tool_node = ToolNode(tools=tools)

    graph.add_node("llm", llm_chain_node)
    graph.add_node("tools", tool_node)

    graph.add_edge(START, "llm")
    graph.add_conditional_edges("llm", router, {"tools": "tools", "end": END})
    graph.add_edge("tools", "llm")
    return graph.compile()


async def get_models():
    async with AsyncClient() as client:
        res = await client.get("http://localhost:11434/api/tags")
        return res.json()


async def main():
    # models = await get_models()

    # print(models)
    # llm = ChatOllama(model=models["models"][-1]["model"])
    # res = llm.invoke("hi")
    # print(res)
    #
    # llm = ChatOpenRouter(model=open_router_model)
    #
    prompt = PromptTemplate.from_template("""
        system : reply in a funny mannar
        user: {history}

        """)

    llm = get_llm(Provider.groq, groq_model)
    if not llm:
        return

    # chain = prompt | llm | StrOutputParser()

    # print(chain.invoke({"querry": "hi"}))
    # if not llm:
    #     return
    # res = chain.astream({"history": "hi"})
    # async for chunk in res:
    #     if chunk:
    #         print("content: ", chunk)

    agent = create_agent()
    agent.invoke()


if __name__ == "__main__":
    run(main())
# llm = ChatOllama()
