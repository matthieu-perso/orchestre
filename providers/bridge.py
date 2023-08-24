import os

from starlette.requests import Request

from core.loader.loader import Loader
from products.base import ProductBaseService
from providers.base import BaseProvider

PROVIDERS_PATH = os.path.join(os.path.dirname(__file__), "plugins")


class Bridge:
    def __init__(self):
        self.loader = Loader()
        self.providers = self.loader.load_plugins(
            PROVIDERS_PATH, BaseProvider, recursive=True
        )
        self.shared_provider_list = {}
        self.system_provider_list = {}

        # load provider instances
        for key in self.providers:
            Provider = self.providers[key]
            self.shared_provider_list[key] = Provider()
            self.system_provider_list[key] = {}

    def get_all_providers(self):
        # load provider instances
        provider_info = []
        for key in self.shared_provider_list:
            if key == "dummyprovider" or key == "baseprovider":
                continue

            provider = self.shared_provider_list[key]
            provider_info.append(provider.get_provider_info())
        return provider_info

    # link provider
    async def link_provider(
        self, provider_name: str, redirect_url: str, request: Request
    ):
        provider = self.shared_provider_list[provider_name.lower()]
        if not provider:
            raise NotImplementedError

        return await provider.link_provider(redirect_url, request)

    # get access token
    async def get_access_token(self, provider_name: str, request: Request):
        provider = self.shared_provider_list[provider_name.lower()]
        if not provider:
            raise NotImplementedError

        return await provider.get_access_token(request)

    # get access token from refresh_token
    async def get_access_token_from_refresh_token(
        self, provider_name: str, refresh_token: str
    ) -> str:
        provider = self.shared_provider_list[provider_name.lower()]
        if not provider:
            raise NotImplementedError

        return await provider.get_access_token_from_refresh_token(refresh_token)

    # get last message
    def get_last_message(
        self, provider_name: str, identifier_name: str, access_token: str, option: str
    ):
        key = provider_name.lower()
        if identifier_name not in self.system_provider_list[key]:
            self.system_provider_list[key][identifier_name] = self.providers[key]()

        return self.system_provider_list[key][identifier_name].get_last_message(
            access_token, option
        )

    def get_full_messages(
        self,
        provider_name: str,
        identifier_name: str,
        access_token: str,
        of_what: str,
        option: str,
    ):
        key = provider_name.lower()
        if identifier_name not in self.system_provider_list[key]:
            self.system_provider_list[key][identifier_name] = self.providers[key]()

        return self.system_provider_list[key][identifier_name].get_full_messages(
            access_token, of_what, option
        )

    # get messages
    def get_messages(
        self,
        provider_name: str,
        identifier_name: str,
        access_token: str,
        from_when: str,
        count: int,
        option: str,
    ):
        key = provider_name.lower()
        if identifier_name not in self.system_provider_list[key]:
            self.system_provider_list[key][identifier_name] = self.providers[key]()

        return self.system_provider_list[key][identifier_name].get_messages(
            access_token, from_when, count, option
        )

    # reply to message
    def reply_to_message(
        self,
        provider_name: str,
        identifier_name: str,
        access_token: str,
        to: str,
        message: str,
        option: str,
    ):
        key = provider_name.lower()
        if identifier_name not in self.system_provider_list[key]:
            self.system_provider_list[key][identifier_name] = self.providers[key]()

        return self.system_provider_list[key][identifier_name].reply_to_message(
            access_token, to, message, option
        )

    # disconnect
    async def disconnect(
        self, provider_name: str, identifier_name: str, request: Request
    ):
        key = provider_name.lower()
        if identifier_name in self.system_provider_list[key]:
            await self.system_provider_list[key][identifier_name].disconnect(request)

        return

    async def start_autobot(
        self,
        user_id: str,
        provider_name: str,
        identifier_name: str,
        user_data: any,
        option: any = None,
    ):
        key = provider_name.lower()
        if identifier_name not in self.system_provider_list[key]:
            self.system_provider_list[key][identifier_name] = self.providers[key]()

        self.system_provider_list[key][identifier_name].set_base_info(
            user_id, identifier_name
        )
        return await self.system_provider_list[key][identifier_name].start_autobot(
            user_data, option
        )

    def update_provider_info(
        self,
        user_id: str,
        provider_name: str,
        identifier_name: str,
        user_data: any,
        option: any = None,
    ):
        key = provider_name.lower()
        if identifier_name not in self.system_provider_list[key]:
            self.system_provider_list[key][identifier_name] = self.providers[key]()

        return self.system_provider_list[key][identifier_name].update_provider_info(
            user_data, option
        )

    async def get_purchased_products(
        self,
        user_id: str,
        provider_name: str,
        identifier_name: str,
        user_data: any,
        option: any,
    ):
        key = provider_name.lower()
        if identifier_name not in self.system_provider_list[key]:
            self.system_provider_list[key][identifier_name] = self.providers[key]()

        self.system_provider_list[key][identifier_name].set_base_info(
            user_id, identifier_name
        )
        return await self.system_provider_list[key][
            identifier_name
        ].get_purchased_products(user_data, option)

    async def get_all_products(
        self,
        user_id: str,
        provider_name: str,
        identifier_name: str,
        user_data: any,
        option=any,
    ):
        key = provider_name.lower()
        if identifier_name not in self.system_provider_list[key]:
            self.system_provider_list[key][identifier_name] = self.providers[key]()

        self.system_provider_list[key][identifier_name].set_base_info(
            user_id, identifier_name
        )
        return await self.system_provider_list[key][identifier_name].get_all_products(
            user_data, option=option
        )

    async def scrapy_all_chats(
        self,
        user_id: str,
        provider_name: str,
        identifier_name: str,
        user_data: any,
        option=any,
    ):
        key = provider_name.lower()
        if identifier_name not in self.system_provider_list[key]:
            self.system_provider_list[key][identifier_name] = self.providers[key]()

        self.system_provider_list[key][identifier_name].set_base_info(
            user_id, identifier_name
        )

        return await self.system_provider_list[key][identifier_name].scrapy_all_chats(
            user_data, option
        )


bridge = Bridge()
