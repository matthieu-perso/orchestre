"""
LLM services - updated to modern LangChain 0.2+ / OpenAI 1.x SDK.
"""
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser

from core.config import settings
from products.base import ProductBaseService

logger = logging.getLogger(__name__)


class BaseService(ABC):
    @abstractmethod
    async def get_response(self, message: str, option: Any = None) -> str:
        pass

    def set_prompt(self, prompt: str, option: Any = None) -> None:
        pass


class OpenAIService(BaseService):
    def __init__(self) -> None:
        self.llm = ChatOpenAI(
            model=settings.OPENAI_MODEL,
            openai_api_key=settings.OPENAI_API_KEY,
            temperature=0.7,
        )
        template = "Question: {question}\nAnswer: Let's think step by step."
        self.prompt = PromptTemplate(template=template, input_variables=["question"])
        self.chain = self.prompt | self.llm | StrOutputParser()

    def set_prompt(self, prompt: str, option: Any = None) -> None:
        self.prompt = PromptTemplate.from_template(prompt)
        self.chain = self.prompt | self.llm | StrOutputParser()

    async def get_response(self, message: str, option: Any = None) -> str:
        return await self.chain.ainvoke({"question": message})

    def get_response_sync(self, message: str, option: Any = None) -> str:
        return self.chain.invoke({"question": message})


class HuggingFaceService(BaseService):
    def __init__(self) -> None:
        self.endpoint = settings.HUGGINGFACE_ENDPOINT or ""
        self.headers = {
            "Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}",
            "Content-Type": "text/plain",
        }

    async def get_response(self, message: str, option: Any = None) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.endpoint, headers=self.headers, content=message)
            resp.raise_for_status()
            return resp.json().get("generated_text", "")




# Singletons
openai_service = OpenAIService()
