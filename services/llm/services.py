import os
import traceback
from abc import ABC, abstractmethod
from getpass import getpass

import requests
from langchain import LLMChain, PromptTemplate
from langchain.chat_models import ChatOpenAI
from langchain.llms import Banana, CerebriumAI

from core.config import settings
from products.base import ProductBaseService


# Define a base class for all services
class BaseService(ABC):
    @abstractmethod
    def get_response(self, message: str, option:any = None):
        pass
    
    def set_prompt(self, prompt: str, option: any = None):
        pass

"""
openai_service.set_prompt("Question {question}, show me some examples")
result = openai_service.get_response("I hope to know more about bitcoin price")
"""
class OpenAIService(BaseService):
    def __init__(self):
        os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY

        template = """Question: {question}
        Answer: Let's think step by step."""

        self.prompt = PromptTemplate(template=template, input_variables=["question"])
        self.llm = ChatOpenAI()
        self.llm_chain = LLMChain(prompt=self.prompt, llm=self.llm)

    def set_prompt(self, prompt: str, option: any = None):
        self.prompt = PromptTemplate.from_template(prompt)
        self.llm_chain = LLMChain(prompt=self.prompt, llm=self.llm)

    def get_response(self, message: str, option: any = None):
        return self.llm_chain.run(message)


class BananaService(BaseService):
    def __init__(self):
        os.environ["BANANA_API_KEY"] = settings.BANANA_MODEL_KEY

        try:
            template = """Question: {question}
            Answer: Let's think step by step."""
            self.prompt = PromptTemplate(
                template=template, input_variables=["question"]
            )
            self.llm = Banana(model_key=os.environ["BANANA_MODEL_KEY"])
            self.llm_chain = LLMChain(prompt=self.prompt, llm=self.llm)
        except:
            pass

    def set_prompt(self, prompt: str, option: any = None):
        self.prompt = PromptTemplate.from_template(prompt)
        self.llm_chain = LLMChain(prompt=self.prompt, llm=self.llm)

    def get_response(self, message: str, option: any = None):
        return self.llm_chain.run(message)


class RunPodService(BaseService, ProductBaseService):
    def __init__(self):
        os.environ["RUNPOD_API_KEY"] = settings.RUNPOD_API_KEY
        os.environ["RUNPOD_ENDPOINT"] = settings.RUNPOD_ENDPOINT

        self.endpoint = os.environ["RUNPOD_ENDPOINT"]
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']} ",
        }

    def get_response(self, message: str, option: any = None):
        response = requests.post(self.endpoint, headers=self.headers, json=option)
        if response.status_code == 200:
            return response.json()["output"]
        else:
            raise Exception("Failed to get response")

    def suggest_product(self, messages: any, option: any = None):
        response = requests.post(self.endpoint, headers=self.headers, json=option)
        if response.status_code == 200:
            return response.json()["output"]
        else:
            raise Exception(traceback.format_exc())


"""
class CerebriumService(BaseService):
    def __init__(self):
        from cerebrium import Conduit, model_type
        os.environ["CEREBRIUM_API_KEY"] = settings.CEREBRIUM_API_KEY

        self.api_key = os.environ["CEREBRIUM_API_KEY"]
        self.c = Conduit(
            'hf-gpt',
            self.api_key,
            [
                (model_type.HUGGINGFACE_PIPELINE, {"task": "text-generation", "model": "EleutherAI/gpt-neo-125M", "max_new_tokens": 100}),
            ],
        )

        self.c.deploy()
        # Assume the endpoint is stored in self.c.endpoint after calling c.deploy()
        self.endpoint = self.c.endpoint

    def get_response(self, message: str, option: any):
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json"
        }
        response = requests.post(
            self.endpoint, headers=headers, json=[message]
        )
        if response.status_code == 200:
            return response.json()[0]["generated_text"]
        else:
            raise Exception("Failed to get response")
"""


class HuggingFaceService(BaseService):
    def __init__(self):
        os.environ["HUGGINGFACE_API_KEY"] = settings.HUGGINGFACE_API_KEY
        os.environ["HUGGINGFACE_ENDPOINT"] = settings.HUGGINGFACE_ENDPOINT

        self.endpoint = (os.environ["HUGGINGFACE_ENDPOINT"])
        self.headers = {
            "Authorization": f"Bearer {os.environ['HUGGINGFACE_API_KEY']}",
            "Content-Type": "text/plain",
        }

    def get_response(self, message: str, option: any = None):
        response = requests.post(self.endpoint, headers=self.headers, data=message)
        if response.status_code == 200:
            return response.json()["generated_text"]
        else:
            raise Exception("Failed to get response")


class HttpService(BaseService):
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def get_response(self, message: str, option: any = None):
        headers = {"Content-Type": "application/json"}
        response = requests.post(
            self.endpoint, headers=headers, json={"input_text": message}
        )
        if response.status_code == 200:
            return response.json()["result"]
        else:
            raise Exception("Failed to get response")


# Instances of our deployed LLM models
openai_service = OpenAIService()
banana_service = BananaService()
runpod_service = RunPodService()
http_service = HttpService(settings.HTTP_ENDPOINT)

# huggingface_service = HuggingFaceService()
# cerebrium_service = CerebriumService()
