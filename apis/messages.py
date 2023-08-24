from fastapi import APIRouter, Depends

from core.utils.message import MessageErr, MessageOK
from providers.bridge import bridge

from .users import User, get_current_user

router = APIRouter()


@router.get(
    "/get_last_message",
    summary="Get last message of the account",
    description="This endpoint is used to get last message of the account which specified by provider and identifier name.<br><br>"
    "<i>provider_name</i> : indicates the provider name such as 'gmailprovider' or 'whatsappprovider'<br>"
    "<i>identifier_name</i> : indicates the account name<br>"
    "<i>access_token</i> : indicates the access_token of the account<br>"
    "<i>option</i> : indicates the additional option as JSON parsable string<br>",
)
async def get_last_message(
    provider_name: str = "gmailprovider",
    identifier_name: str = "",
    access_token: str = "",
    option: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        result = bridge.get_last_message(
            provider_name, identifier_name, access_token, option=option
        )
        return MessageOK(data={"message": result})
    except Exception as e:
        return MessageErr(reason=str(e))


@router.get(
    "/get_full_messages",
    summary="Get some messages of the account",
    description="This endpoint is used to get some messages of the account which specified by provider and identifier name.<br><br>"
    "<i>provider_name</i> : indicates the provider name such as 'gmailprovider' or 'whatsappprovider'<br>"
    "<i>identifier_name</i> : indicates the account name<br>"
    "<i>access_token</i> : indicates the access_token of the account<br>"
    "<i>of_what</i> : indicates the message_id in the provider<br>"
    "<i>option</i> : indicates the additional option as JSON parsable string<br>",
)
async def get_full_message(
    provider_name: str = "gmailprovider",
    identifier_name: str = "",
    access_token: str = "",
    of_what: str = "message_id",
    option: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        result = bridge.get_full_messages(
            provider_name, identifier_name, access_token, of_what, option
        )
        return MessageOK(data={"message": result})
    except Exception as e:
        return MessageErr(reason=str(e))


@router.get(
    "/get_messages",
    summary="Get some messages of the account",
    description="This endpoint it used to get some messages of the account which specified by provider and identifier name.<br><br>"
    "<i>provider_name</i> : indicates the provider name such as 'gmailprovider' or 'whatsappprovider'<br>"
    "<i>identifier_name</i> : indicates the account name<br>"
    "<i>access_token</i> : indicates the access_token of the account<br>"
    "<i>from_when</i> : indicates the time of the first message<br>"
    "<i>count</i> : indicates the message count<br>"
    "<i>option</i> : indicates the additional option as JSON parsable string<br>",
)
async def get_messages(
    provider_name: str = "gmailprovider",
    identifier_name: str = "",
    access_token: str = "",
    from_when: str = "2023/05/27 03:00:00",
    count: int = 1,
    option: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        result = bridge.get_messages(
            provider_name, identifier_name, access_token, from_when, count, option
        )
        return MessageOK(data={"message": result})
    except Exception as e:
        return MessageErr(reason=str(e))


@router.post(
    "/reply_to_message",
    summary="Reply message to the account",
    description="This endpoint is usded to reply message to the account which specified by provider and identifier name<br><br>"
    "<i>provider_name</i> : indicates the provider name such as 'gmailprovider' or 'whatsappprovider'<br>"
    "<i>identifier_name</i> : indicates the account name<br>"
    "<i>access_token</i> : indicates the access_token of the account<br>"
    "<i>to</i> : indicates something is used to specify the message, i,e) messageId in GMailProvider<br>"
    "<i>option</i> : indicates the additional option as JSON parsable string<br>",
)
async def reply_to_message(
    provider_name: str = "gmailprovider",
    identifier_name: str = "",
    access_token: str = "",
    to: str = "",
    message: str = "",
    option: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        result = bridge.reply_to_message(
            provider_name, identifier_name, access_token, to, message, option
        )
        return MessageOK(data={"message": result})
    except Exception as e:
        return MessageErr(reason=str(e))
