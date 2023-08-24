import base64
import json
import sys
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuth
from fastapi.responses import RedirectResponse
from google.auth.exceptions import RefreshError
from google.auth.transport import requests
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import httplib2
from googleapiclient.discovery import build
from starlette.config import Config
from starlette.requests import Request

from core.config import settings
from core.utils.log import BackLog
from core.utils.timestamp import get_current_timestamp
from providers.base import BaseProvider
from services.llm.services import openai_service

REDIRECT_URL = "redirect_url"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.addons.current.action.compose",
]
MAX_MESSAGES_COUNT = 10

oauth2_crendential_path = settings.GOOGLE_CREDENTIAL
oauth2_credentials = json.load(open(oauth2_crendential_path))
oauth = OAuth()
oauth.register(
    name="google",
    client_id=oauth2_credentials["web"]["client_id"],
    client_secret=oauth2_credentials["web"]["client_secret"],
    access_token_url=oauth2_credentials["web"]["token_uri"],
    access_token_params=None,
    authorize_url=oauth2_credentials["web"]["auth_uri"],
    authorize_params=None,
    api_base_url="https://www.googleapis.com/oauth2/v1/",
    userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
    jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "prompt": "consent",
        "scope": "openid email profile https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.compose",
    },
)


class GMailProvider(BaseProvider):
    def __init__(self):
        self.sync_time = -1
        self.access_token = None
        self.refresh_token = None

    def get_provider_info(self):
        return {
            "provider": GMailProvider.__name__.lower(),
            "short_name": "Gmail",
            "provider_description": "GMail Provider",
            "provider_icon_url": "/gmail.svg",
        }

    async def link_provider(self, redirect_url: str, request: Request):
        request.session.clear()
        request.session[REDIRECT_URL] = redirect_url

        redirect_uri = request.url_for("google_auth")
        return await oauth.google.authorize_redirect(
            request, str(redirect_uri).replace("http:", "https:"), access_type="offline"
        )

    async def get_access_token(self, request: Request):
        token = await oauth.google.authorize_access_token(request)
        response_tokens = {
            "access_token": token["access_token"],
            "refresh_token": token["refresh_token"],
        }
        response = RedirectResponse(
            url=request.session[REDIRECT_URL]
            + "?provider=gmailprovider&"
            + urlencode(response_tokens)
        )
        return response

    async def get_access_token_from_refresh_token(self, refresh_token: str) -> str:
        creds = Credentials.from_authorized_user_info(
            info={
                "client_id": oauth2_credentials["web"]["client_id"],
                "client_secret": oauth2_credentials["web"]["client_secret"],
                "refresh_token": refresh_token,
            }
        )
        creds.refresh(requests.Request())
        return creds.token

    def get_gmail_service(self, access_token: str):
        creds = Credentials(
            token=access_token,
            client_id=oauth2_credentials["web"]["client_id"],
            client_secret=oauth2_credentials["web"]["client_secret"],
            token_uri=oauth2_credentials["web"]["token_uri"],
            scopes=SCOPES,
        )
        gmail_service = build("gmail", "v1", credentials=creds)
        return gmail_service

    def get_last_message(self, access_token: str, option: any):
        gmail_service = self.get_gmail_service(access_token)

        message_list = (
            gmail_service.users()
            .messages()
            .list(userId="me", maxResults=MAX_MESSAGES_COUNT, q=f"from:!me")
            .execute()
        )
        messages = message_list.get("messages", [])
        next_page_token = message_list.get("nextPageToken")

        # get last message
        message_id = messages[0]["id"]
        message = (
            gmail_service.users().messages().get(userId="me", id=message_id).execute()
        )

        # get senders
        senders = []
        subject = ""
        for header in message["payload"]["headers"]:
            if header["name"].lower() == "from":
                senders.append(header["value"])
            if header["name"].lower() == "subject":
                subject = header["value"]

        # get content
        snippet = message["snippet"]
        if "parts" in message["payload"]:
            for part in message["payload"]["parts"]:
                if part["body"] and part["mimeType"] == "text/plain":
                    content = base64.urlsafe_b64decode(part["body"]["data"]).decode()
                    break
        else:
            content = base64.urlsafe_b64decode(
                message["payload"]["body"]["data"]
            ).decode()

        return {
            "messageId": message_id,
            "sender": senders,
            "subject": subject,
            "option": {"nextPageToken": next_page_token},
            "snippet": snippet,
            "html": content,
        }

    def get_full_messages(self, access_token: str, of_what: str, option: any):
        gmail_service = self.get_gmail_service(access_token)
        message = (
            gmail_service.users()
            .messages()
            .get(userId="me", id=of_what, format="full")
            .execute()
        )

        # Print the message history
        if "threadId" in message:
            threads = (
                gmail_service.users()
                .threads()
                .get(userId="me", id=message["threadId"])
                .execute()
            )
            history = []
            one_message = ""

            for _message in threads["messages"]:
                senders = []

                for header in _message["payload"]["headers"]:
                    if header["name"].lower() == "from":
                        senders.append(header["value"])

                # get content
                snippet = _message["snippet"]
                history.append(
                    {
                        "senders": senders,
                        "snippet": snippet,
                        "messageId": _message["id"],
                    }
                )
                one_message = one_message + f"sender: {senders[0]} : {snippet}\n"

            return {
                "messageId": of_what,
                "messages": history,
                "one_message": one_message,
            }

        else:
            BackLog.info(instance=self, message="No message history found.")
            return {"messageId": of_what, "messages": []}

    # sample from_when='timestamp'
    def get_messages(self, access_token: str, from_when: str, count: int, option: any):
        gmail_service = self.get_gmail_service(access_token)
        message_list = (
            gmail_service.users()
            .messages()
            .list(userId="me", maxResults=count, q=f"after:{from_when} from:!me")
            .execute()
        )
        messages = message_list.get("messages", [])
        next_page_token = message_list.get("nextPageToken")

        results = []
        for m in messages:
            message_id = m["id"]
            message = (
                gmail_service.users()
                .messages()
                .get(userId="me", id=message_id)
                .execute()
            )

            # get senders
            senders = []
            subject = ""
            for header in message["payload"]["headers"]:
                if header["name"].lower() == "from":
                    senders.append(header["value"])
                if header["name"].lower() == "subject":
                    subject = header["value"]

            # get content
            snippet = message["snippet"]

            if "parts" in message["payload"]:
                for part in message["payload"]["parts"]:
                    if part["body"] and part["mimeType"] == "text/plain":
                        content = base64.urlsafe_b64decode(
                            part["body"]["data"]
                        ).decode()
                        break
            else:
                content = base64.urlsafe_b64decode(
                    message["payload"]["body"]["data"]
                ).decode()

            results.append(
                {
                    "messageId": message_id,
                    "sender": senders,
                    "subject": subject,
                    "snippet": snippet,
                    "html": content,
                }
            )

        return {
            "option": {"nextPageToken": next_page_token},
            "messages": results,
        }

    def reply_to_message(self, access_token: str, to: str, message: str, option: any):
        gmail_service = self.get_gmail_service(access_token)

        message_id = to
        old_message = (
            gmail_service.users().messages().get(userId="me", id=message_id).execute()
        )

        # get senders
        senders = []
        subject = ""
        for header in old_message["payload"]["headers"]:
            if header["name"].lower() == "from":
                senders.append(header["value"])
            if header["name"].lower() == "subject":
                subject = header["value"]

        reply_message = f"Replying to {senders}\n\n{message}"
        new_message = MIMEText(reply_message)
        new_message["to"] = senders[0]
        new_message["subject"] = f"Re: {subject}"
        new_message["Reference"] = message_id
        new_message["In-Reply-To"] = message_id
        create_message = {
            "raw": base64.urlsafe_b64encode(new_message.as_bytes()).decode()
        }
        send_message = (
            gmail_service.users()
            .messages()
            .send(userId="me", body=create_message)
            .execute()
        )
        return {"message": "Email sent!"}

    async def disconnect(self, request: Request):
        pass

    async def start_autobot(self, user_data: any, option: any):
        try:
            if self.access_token is None:
                self.access_token = user_data["access_token"]

            if self.refresh_token is None:
                self.refresh_token = user_data["refresh_token"]

            if self.sync_time == -1:
                last_message = self.get_last_message(
                    access_token=self.access_token,
                    option="",
                )
                ai_response = openai_service.get_response(
                    message=last_message["snippet"],
                    option="",
                )
                self.reply_to_message(
                    access_token=self.access_token,
                    to=last_message["messageId"],
                    message=ai_response,
                    option="",
                )
                BackLog.info(
                    instance=self,
                    message=f"LastMessage: {last_message['snippet']}, response: {ai_response}",
                )
            else:
                last_messages = self.get_messages(
                    access_token=self.access_token,
                    from_when=self.sync_time,
                    count=MAX_MESSAGES_COUNT,
                    option="",
                )

                for message in last_messages["messages"]:
                    ai_response = openai_service.get_response(
                        message=message["snippet"], option=""
                    )
                    self.reply_to_message(
                        access_token=self.access_token,
                        to=message["messageId"],
                        message=ai_response,
                        option="",
                    )
                    BackLog.info(
                        instance=self,
                        message=f"Message: {message['snippet']}, response: {ai_response}",
                    )

            self.sync_time = get_current_timestamp()
        except NotImplementedError:
            BackLog.info(
                instance=self, message=f"Error: GMailProvider is Not implemented"
            )
            pass
        except RefreshError as e:
            self.access_token = await self.get_access_token_from_refresh_token(
                refresh_token=self.refresh_token
            )
            BackLog.info(
                instance=self,
                message=f"access_token is expired, rescheduled it next time after regenerate access_token",
            )
            pass
        except Exception as e:
            BackLog.exception(instance=self, message=f"Exception occurred {str(e)}")
            pass

        pass

    def update_provider_info(self, user_data: any, option: any = None):
        raise NotImplementedError

    async def get_purchased_products(self, user_data: any, option: any = None):
        pass

    async def get_all_products(self, user_data: any, option: any = None):
        pass

    async def scrapy_all_chats(self, user_data: any, option: any = None):
        pass
