import os
import sys
from pathlib import Path

from dotenv import load_dotenv

env_path = Path(".") / ".env"
load_dotenv(dotenv_path=env_path)


class Settings:
    PROJECT_NAME: str = "Orchestre"
    PROJECT_VERSION: str = "0.1.0"

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY")
    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY is not undefined")

    BANANA_MODEL_KEY: str = os.getenv("BANANA_MODEL_KEY")
    if not BANANA_MODEL_KEY:
        print("BANANA_MODEL_KEY is not undefined")

    PINECONE_API_KEY: str = os.getenv("PINECONE_API_KEY")
    if not PINECONE_API_KEY:
        print("PINECONE_API_KEY is not undefined")

    PINECONE_PRODUCT_INDEX: str = os.getenv("PINECONE_PRODUCT_INDEX")
    if not PINECONE_PRODUCT_INDEX:
        print("PINECONE_PRODUCT_INDEX is not undefined")

    PINECONE_PRODUCT_ENVIRONMENT: str = os.getenv("PINECONE_PRODUCT_ENVIRONMENT")
    if not PINECONE_PRODUCT_ENVIRONMENT:
        print("PINECONE_PRODUCT_ENVIRONMENT is not undefined")

    RUNPOD_API_KEY: str = os.getenv("RUNPOD_API_KEY")
    if not RUNPOD_API_KEY:
        print("RUNPOD_API_KEY is not undefined")

    RUNPOD_ENDPOINT: str = os.getenv("RUNPOD_ENDPOINT")
    if not RUNPOD_ENDPOINT:
        print("RUNPOD_ENDPOINT is not undefined")

    CEREBRIUM_API_KEY: str = os.getenv("CEREBRIUM_API_KEY")
    if not CEREBRIUM_API_KEY:
        print("CEREBRIUM_API_KEY is not undefined")

    HUGGINGFACE_API_KEY: str = os.getenv("HUGGINGFACE_API_KEY")
    if not HUGGINGFACE_API_KEY:
        print("HUGGINGFACE_API_KEY is not undefined")

    HUGGINGFACE_ENDPOINT: str = os.getenv("HUGGINGFACE_ENDPOINT")
    if not HUGGINGFACE_ENDPOINT:
        print("HUGGINGFACE_ENDPOINT is not undefined")

    HTTP_ENDPOINT: str = os.getenv("HTTP_ENDPOINT")
    if not HTTP_ENDPOINT:
        print("HTTP_ENDPOINT is not undefined")

    SESSION_KEY: str = os.getenv("SESSION_KEY")
    if not SESSION_KEY:
        print("Please input SESSION_KEY in .env file. This is used for middleware")
        sys.exit()

    FIREBASE_SERVICE_ACCOUNT = Path(".") / "firebase-serviceaccount.json"
    if not os.path.exists(FIREBASE_SERVICE_ACCOUNT):
        print(
            "Firebase service account is not indiciated. Please configure firebase project and download serviceaccount.json"
        )
        sys.exit()

    FIREBASE_CONFIG = Path(".") / "firebase.json"
    if not os.path.exists(FIREBASE_CONFIG):
        print(
            "Firebase configure json is not indicated. Please configure firebase project and download firebase.json"
        )
        sys.exit()

    FIREBASE_STORAGE_BUCKET: str = os.getenv("FIREBASE_STORAGE_BUCKET")
    if not FIREBASE_STORAGE_BUCKET:
        print("Please input STORAGE_BUCKET in .env file.")
        sys.exit()

    GOOGLE_CREDENTIAL = Path(".") / "oauth2-credentials.json"
    if not os.path.exists(GOOGLE_CREDENTIAL):
        print(
            "oauth2_credentials.json is not existed. Please configure and download it from Google Cloud console"
        )
        sys.exit()


settings = Settings()
