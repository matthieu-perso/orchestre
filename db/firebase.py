import json

import firebase_admin
import httpx
from firebase_admin import auth, credentials, firestore, storage

from core.config import settings

# Initialize Firestore credentials
cred = credentials.Certificate(settings.FIREBASE_SERVICE_ACCOUNT)
firebase_admin.initialize_app(cred, {"storageBucket": settings.FIREBASE_STORAGE_BUCKET})

# Load Firebase web config for REST API (apiKey)
def _get_firebase_api_key() -> str:
    if settings.FIREBASE_API_KEY:
        return settings.FIREBASE_API_KEY
    if settings.FIREBASE_CONFIG.exists():
        cfg = json.loads(settings.FIREBASE_CONFIG.read_text())
        return cfg.get("apiKey", "")
    return ""


FIREBASE_API_KEY = _get_firebase_api_key()

# Create Firestore client instance
db = firestore.client()

_AUTH_BASE = "https://identitytoolkit.googleapis.com/v1/accounts"


def authenticate_user(email: str, password: str) -> str:
    """Sign in with email/password via Firebase Auth REST API. Returns idToken."""
    resp = httpx.post(
        f"{_AUTH_BASE}:signInWithPassword",
        params={"key": FIREBASE_API_KEY},
        json={"email": email, "password": password, "returnSecureToken": True},
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["idToken"]


def decode_access_token(token: str):
    return auth.verify_id_token(token)


def create_user(email: str, password: str):
    """Create user with email/password and send verification email via Firebase REST API."""
    resp = httpx.post(
        f"{_AUTH_BASE}:signUp",
        params={"key": FIREBASE_API_KEY},
        json={"email": email, "password": password, "returnSecureToken": True},
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    id_token = data.get("idToken")
    if id_token:
        # Send email verification
        httpx.post(
            f"{_AUTH_BASE}:sendOobCode",
            params={"key": FIREBASE_API_KEY},
            json={"requestType": "VERIFY_EMAIL", "idToken": id_token},
            timeout=10.0,
        )
    return data


def load_json_from_storage(file_name):
    try:
        bucket = storage.bucket()
        blob = bucket.blob(file_name)
        json_data = blob.download_as_text()
        print(f"Succeeded to load {file_name}")
        return json.loads(json_data)
    except Exception as e:
        print(f"Failed to load {file_name}: {e}")
        return None


def save_json_to_storage(json_data, file_name):
    try:
        bucket = storage.bucket()
        blob = bucket.blob(file_name)
        blob.upload_from_string(json.dumps(json_data), content_type="application/json")
        print(f"Succeeded to save {file_name}")
    except Exception as e:
        print(f"Failed to save {file_name}: {e}")
