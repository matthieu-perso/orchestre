import sys
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    PROJECT_NAME: str = "Orchestre"
    PROJECT_VERSION: str = "0.2.0"

    SESSION_KEY: str

    # --- Production security ---
    PRODUCTION: bool = False
    # Comma-separated allowed origins for CORS (e.g. "https://app.example.com,https://admin.example.com")
    # In production, set this; leave empty to allow all (dev only).
    CORS_ORIGINS: str = ""
    # Reject Shopify webhooks when secret is not configured
    STRICT_WEBHOOK_VERIFICATION: bool = False

    # --- Database ---
    POSTGRES_USER: str = "orchestre"
    POSTGRES_PASSWORD: str = "orchestre"
    POSTGRES_DB: str = "orchestre"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def DATABASE_URL_SYNC(self) -> str:
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # --- Redis / Queue ---
    REDIS_URL: str = "redis://localhost:6379"

    # --- Firebase ---
    FIREBASE_SERVICE_ACCOUNT: Path = Path("firebase-serviceaccount.json")
    FIREBASE_CONFIG: Path = Path("firebase.json")
    FIREBASE_STORAGE_BUCKET: str = ""
    # Web API key for Auth REST API (or set in firebase.json as "apiKey")
    FIREBASE_API_KEY: Optional[str] = None

    # --- Google / Gmail OAuth ---
    GOOGLE_CREDENTIAL: Path = Path("oauth2-credentials.json")

    # --- LLMs ---
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o"

    HUGGINGFACE_API_KEY: Optional[str] = None
    HUGGINGFACE_ENDPOINT: Optional[str] = None

    # --- Vector store ---
    PINECONE_API_KEY: Optional[str] = None
    PINECONE_PRODUCT_INDEX: Optional[str] = None
    PINECONE_PRODUCT_ENVIRONMENT: Optional[str] = None

    # --- Shopify ---
    SHOPIFY_API_KEY: Optional[str] = None
    SHOPIFY_API_SECRET: Optional[str] = None
    SHOPIFY_WEBHOOK_SECRET: Optional[str] = None
    # API version to use (e.g. "2024-01")
    SHOPIFY_API_VERSION: str = "2024-01"

    # --- Amazon SP-API ---
    AMAZON_CLIENT_ID: Optional[str] = None
    AMAZON_CLIENT_SECRET: Optional[str] = None
    # AWS IAM role for SP-API (optional, used for role assumption)
    AMAZON_AWS_ACCESS_KEY: Optional[str] = None
    AMAZON_AWS_SECRET_KEY: Optional[str] = None
    AMAZON_AWS_ROLE_ARN: Optional[str] = None
    AMAZON_REGION: str = "us-east-1"
    # SP-API endpoint (NA/EU/FE)
    AMAZON_ENDPOINT: str = "https://sellingpartnerapi-na.amazon.com"

    # --- Amazon Advertising API ---
    AMAZON_ADS_CLIENT_ID: Optional[str] = None
    AMAZON_ADS_CLIENT_SECRET: Optional[str] = None
    AMAZON_ADS_ENDPOINT: str = "https://advertising-api.amazon.com"

    # --- Meta / Facebook Ads ---
    META_APP_ID: Optional[str] = None
    META_APP_SECRET: Optional[str] = None
    META_ADS_API_VERSION: str = "v19.0"

    # --- Google Ads ---
    GOOGLE_ADS_DEVELOPER_TOKEN: Optional[str] = None
    GOOGLE_ADS_CLIENT_ID: Optional[str] = None
    GOOGLE_ADS_CLIENT_SECRET: Optional[str] = None
    GOOGLE_ADS_API_VERSION: str = "v15"

    # --- Webhook base URL (for registering with providers) ---
    WEBHOOK_BASE_URL: str = "https://your-domain.com"

    # --- EasyPost (carrier API for self-fulfilled Shopify orders) ---
    # Sign up at https://www.easypost.com — free test API key available
    EASYPOST_API_KEY: Optional[str] = None

    # --- Alerts: Slack ---
    # Create an Incoming Webhook in your Slack workspace:
    # Slack > Apps > Incoming Webhooks > Add to Slack > copy URL
    SLACK_WEBHOOK_URL: Optional[str] = None

    # --- Alerts: Email (SMTP) ---
    # Works with any SMTP provider: Gmail, SendGrid, Postmark, AWS SES, etc.
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USE_TLS: bool = True
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM_EMAIL: Optional[str] = None
    # Recipient for all alerts (can be the same as SMTP_USERNAME)
    ALERT_EMAIL_TO: Optional[str] = None

    def validate_critical(self) -> None:
        if not self.SESSION_KEY:
            print("SESSION_KEY is required. Use a random 32+ char string (e.g. openssl rand -hex 32)")
            sys.exit(1)
        if self.PRODUCTION and len(self.SESSION_KEY) < 32:
            print("SESSION_KEY should be at least 32 characters in production. Use: openssl rand -hex 32")
            sys.exit(1)
        if not self.FIREBASE_SERVICE_ACCOUNT.exists():
            print("firebase-serviceaccount.json not found. Download from Firebase Console > Project Settings > Service Accounts")
            sys.exit(1)
        if not self.FIREBASE_CONFIG.exists():
            print("firebase.json not found. Download from Firebase Console > Project Settings > General")
            sys.exit(1)


settings = Settings()
