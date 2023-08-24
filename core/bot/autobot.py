import asyncio

from core.task.task import TaskManager
from core.utils.log import BackLog
from db.cruds.users import get_user_data
from providers.bridge import bridge


class AutoBot(TaskManager):
    def __init__(self):
        self.task_list = {}
        self.task_status_list = {}
        pass

    async def start(user_id: str, provider_name: str, identifier_name: str):
        user_data = get_user_data(user_id)

        if provider_name in user_data and identifier_name in user_data[provider_name]:
            await bridge.start_autobot(
                user_id,
                provider_name,
                identifier_name,
                user_data[provider_name][identifier_name],
                {
                    "namespace": f"{provider_name}_{user_id}_{identifier_name}",
                },
            )
        else:
            await bridge.start_autobot(
                user_id,
                provider_name,
                identifier_name,
                None,
                {
                    "namespace": f"{provider_name}_{user_id}_{identifier_name}",
                },
            )
        pass

    def start_auto_bot(
        self, user: any, provider_name: str, identifier_name: str, interval: int
    ):
        if user is None:
            return

        uid = user["uid"]

        if self.status_auto_bot(user, provider_name, identifier_name) == False:
            if not uid in self.task_list:
                self.task_list[uid] = {}

            if not uid in self.task_status_list:
                self.task_status_list[uid] = {}

            if not provider_name in self.task_list[uid]:
                self.task_list[uid][provider_name] = {}

            if not provider_name in self.task_status_list[uid]:
                self.task_status_list[uid][provider_name] = {}

            self.task_list[uid][provider_name][identifier_name] = self.create_task(
                AutoBot.start,
                interval,
                user_id=user["uid"],
                provider_name=provider_name,
                identifier_name=identifier_name,
            )
            self.task_status_list[uid][provider_name][identifier_name] = True

        pass

    async def stop_auto_bot(self, user: any, provider_name: str, identifier_name: str):
        uid = user["uid"]

        if (
            user is not None
            and uid in self.task_list
            and provider_name in self.task_list[uid]
            and identifier_name in self.task_list[uid][provider_name]
        ):
            was_cancelled = self.task_list[uid][provider_name][identifier_name].cancel()
            self.task_status_list[uid][provider_name][identifier_name] = False
            BackLog.info(instance=self, message=f"stop_auto_bot: {was_cancelled}")

        pass

    # issue happens
    def status_auto_bot(self, user: any, provider_name: str, identifier_name: str):
        if (
            user is None
            or not user["uid"] in self.task_list
            or not provider_name in self.task_list[user["uid"]]
            or not identifier_name in self.task_list[user["uid"]][provider_name]
        ):
            return False
        else:
            uid = user["uid"]
            if self.task_list[uid][provider_name][identifier_name].done() == True:
                return False

            return True

    def status_my_auto_bot(self, user: any):
        if user is None or not user["uid"] in self.task_status_list:
            return {}
        else:
            return self.task_status_list[user["uid"]]


autobot = AutoBot()
