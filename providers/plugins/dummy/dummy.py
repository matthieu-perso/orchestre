import sys

from starlette.requests import Request

from providers.base import BaseProvider


class DummyProvider(BaseProvider):
    def get_provider_info(self):
        return {
            "provider": DummyProvider.__name__.lower(),
            "provider_description": "Dummy Provider",
            "provider_icon_url": "",
        }

    async def link_provider(self, redirect_url: str, request: Request):
        print(
            "[%s]: link_provider: %s | %s" % (self.plugin_name, redirect_url, request),
            file=sys.stdout,
        )

    async def get_access_token(self, request: Request) -> str:
        print(
            "[%s]: get_access_token: %s | %s" % (self.plugin_name, request),
            file=sys.stdout,
        )

    async def get_access_token_from_refresh_token(self, refresh_token: str) -> str:
        print(
            "[%s]: get_access_token_from_refresh_token: %s | %s"
            % (self.plugin_name, refresh_token),
            file=sys.stdout,
        )

    def get_last_message(self, access_token: str, option: any):
        print(
            "[%s]: get_last_message: %s " % (self.plugin_name, access_token),
            file=sys.stdout,
        )

    def get_full_messages(self, access_token: str, of_what: str, option: any):
        print(
            "[%s]: get_full_messages: %s %s"
            % (self.plugin_name, access_token, of_what),
            file=sys.stdout,
        )

    def get_messages(self, access_token: str, from_when: str, count: int, option: any):
        print(
            "[%s]: get_messages: %s %s %d"
            % (self.plugin_name, access_token, from_when, count),
            file=sys.stdout,
        )

    def reply_to_message(self, access_token: str, to: str, message: str, option: any):
        print(
            "[%s]: reply_to_message: %s %s %s"
            % (self.plugin_name, access_token, to, message),
            file=sys.stdout,
        )

    async def disconnect(self, request: Request):
        print("[%s]: disconnect: %s" % (self.plugin_name), file=sys.stdout)

    async def start_autobot(self, user_data: any, option: any):
        print("[%s]: start_autobot: %s" % (self.plugin_name), file=sys.stdout)

    pass
