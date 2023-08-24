import os
import random
import uuid
from pathlib import Path

import pandas as pd
import pinecone
from core.config import settings
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores import Pinecone

from core.utils.log import BackLog
from products.base import ProductBaseService

# Loading OPENAI, PINECONE API KEYS
OPENAI_API_KEY = settings.OPENAI_API_KEY
PINECONE_API_KEY = settings.PINECONE_API_KEY
PINECONE_PRODUCT_INDEX = settings.PINECONE_PRODUCT_INDEX
PINECONE_PRODUCT_ENVIRONMENT = settings.PINECONE_PRODUCT_ENVIRONMENT

class PineconeService(ProductBaseService):
    def __init__(self) -> None:
        self.initialized = False
        pass

    def initialize(self):
        if self.initialized == True:
            return

        try:
            # Pinecone initialize index
            pinecone.init(
                api_key=PINECONE_API_KEY, environment=PINECONE_PRODUCT_ENVIRONMENT
            )

            # Get the vectorstore and add new products
            dimension = 1536
            self.index = pinecone.Index(PINECONE_PRODUCT_INDEX)
            self.openai = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
            self.vectorstore = Pinecone(
                index=self.index,
                embedding_function=self.openai.embed_query,
                text_key="id",
            )

        except Exception as e:
            BackLog.exception(instance=self, message=f"Exception occurred")

        self.initialized = True
        pass

    def match_product(self, messages: str, option: any):
        self.initialize()

        if self.vectorstore is None:
            return "Couldn't connect to pinecone vector db"

        print("Searching namespace", option["namespace"])
        docs = self.vectorstore.similarity_search(
            messages, k=1, namespace=option["namespace"]
        )
        if docs is not None and type(docs) == list and len(docs) > 0:
            try:
                print("docs", docs)
                return [int(float(docs[0].page_content))]
            except:
                return []
        else:
            return []

    def update_products(self, products_info, option):
        self.initialize()

        if self.vectorstore is None:
            return "Couldn't connect to pinecone vector db"

        """ 
        batch = []
        for item in products_info["products"]:
            id = str(item["id"])
            value = self.openai.embed_query(item["label"])
            metadata = {"id": item["id"], "label": item["label"]}
            batch.append({"id": id, "texts": value, "metadata": metadata})

        #self.index.upsert(vectors=batch, namespace=option["namespace"])
        # dataset = pd.DataFrame(products_info["products"])
        # meta = [{"id": x} for x in dataset["id"]]
        """

        ids = []
        texts = []
        metadatas = []

        for item in products_info["products"]:
            id = str(item["id"])
            metadata = {"id": item["id"], "label": item["label"]}

            ids.append(id)
            texts.append(item["label"])
            metadatas.append(metadata)

        self.vectorstore.add_texts(
            ids=ids, namespace=option["namespace"], texts=texts, metadatas=metadatas
        )

        # BackLog.info(self, f"Import Done")
        pass


pinecone_service = PineconeService()
