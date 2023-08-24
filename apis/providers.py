import requests
from fastapi import APIRouter, Depends, Request

from core.bot.autobot import autobot
from core.utils.message import MessageErr, MessageOK
from db.cruds.users import get_user_data, get_user_providers, update_user
from db.schemas.users import UsersSchema
from providers.bridge import bridge

from .users import User, get_current_user

router = APIRouter()


@router.get(
    "/get_my_providers",
    summary="Get the provider information for end-user",
    description="This endpoint gets all provider associated information for end-user.<br>"
    "This will return the registered accounts for the end-user, those AI bot's status, system support provider information",
)
async def get_my_providers(curr_user: User = Depends(get_current_user)):
    try:
        user_id = curr_user["uid"]
        my_providers = get_user_providers(id=user_id)
        all_providers = bridge.get_all_providers()
        status_autobot = autobot.status_my_auto_bot(curr_user)
        return MessageOK(
            data={
                "my_providers": my_providers,
                "providers": all_providers,
                "status_autobot": status_autobot,
            }
        )
    except Exception as e:
        return MessageErr(reason=str(e))


@router.get(
    "/get_providers",
    summary="Get the system support provider information",
    description="This endpoint will return all provider information registered in the system",
)
async def get_providers(curr_user: User = Depends(get_current_user)):
    try:
        return MessageOK(data=bridge.get_all_providers())
    except Exception as e:
        return MessageErr(reason=str(e))


@router.get(
    "/google_auth",
    summary="The endpoint for Google authentication",
    description="This endpoint is registered on the Google Cloud platform.<br>"
    "When new Gmail provider account is authenticated, this endpoint is called by Google cloud platform with authenticate code",
)
async def google_auth(request: Request):
    try:
        return await bridge.get_access_token("gmailprovider", request)
    except Exception as e:
        return MessageErr(reason=str(e))


@router.get(
    "/link_provider",
    summary="Link the account for specific provider",
    description="This endpoint is used to link the account which supported by provider.<br><br>"
    "<i>provider_name</i> : indicates the provider such as 'gmailprovider' or 'whatsappprovider'<br>"
    "<i>redirect_url</i> : indicates the url which returns with <i>access_token</i> and <i>refresh_token</i><br>",
)
async def link_Provider(
    provider_name: str = "gmailprovider",
    redirect_url: str = "http://localhost:3000/callback/oauth",
    request: Request = None,
):
    try:
        return await bridge.link_provider(provider_name, redirect_url, request)
    except Exception as e:
        return MessageErr(reason=str(e))


@router.get(
    "/unlink_provider",
    summary="Unlink the account for specific provider",
    description="This endpoint is used to unlink the account which specified by provider and identifier name.<br><br>"
    "<i>provider_name</i> : indicates the provider such as 'gmailprovider' or 'whatsappprovider' <br>"
    "<i>identifier_name</i> : indicates the account name<br>",
)
async def unlink_Provider(
    provider_name: str = "gmailprovider",
    identifier_name: str = "john doe",
    request: Request = None,
    curr_user: User = Depends(get_current_user),
):
    try:
        await autobot.stop_auto_bot(
            user=curr_user, provider_name=provider_name, identifier_name=identifier_name
        )
        await bridge.disconnect(provider_name, identifier_name, request)

        return MessageOK(
            data=update_user(
                user=UsersSchema(id=curr_user["uid"], email=curr_user["email"]),
                provider_name=provider_name,
                key=identifier_name,
                content="",
            )
        )
    except Exception as e:
        return MessageErr(reason=str(e))


@router.post(
    "/update_provider_info",
    summary="Update the account's information",
    description="This endpoint is used to update information for specific account.<br><br>"
    "<i>provider_name</i> : indicates the provider such as 'gmailprovider' or 'whatsappprovider' <br>"
    "<i>identifier_name</i> : indicates the account name<br>"
    "<i>social_info</i> : indicates the account information, JSON-parseable string",
)
async def update_provider_info(
    provider_name: str = "gmailprovider",
    identifier_name: str = "john doe",
    social_info: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        user_id = curr_user["uid"]
        result = update_user(
            user=UsersSchema(id=user_id, email=curr_user["email"]),
            provider_name=provider_name,
            key=identifier_name,
            content=social_info,
        )

        user_data = get_user_data(user_id)
        bridge.update_provider_info(
            user_id,
            provider_name,
            identifier_name,
            user_data[provider_name][identifier_name],
        )

        return MessageOK(data=result)
    except Exception as e:
        return MessageErr(reason=str(e))
