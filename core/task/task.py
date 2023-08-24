import asyncio

from core.utils.log import BackLog
from core.utils.timestamp import get_current_timestamp


class TaskManager:
    def __init__(self):
        pass

    async def internal_func(self, task_func: any, interval: int, **kwargs):
        if interval < 0:
            return

        try:
            while True:
                start_timestamp = get_current_timestamp()
                BackLog.info(instance=self, message=f"Running Task...{start_timestamp}")

                await task_func(**kwargs)

                end_timestamp = get_current_timestamp()
                BackLog.info(instance=self, message=f"Ended Task...{end_timestamp}")

                new_interval = interval + start_timestamp - end_timestamp
                if new_interval > 0:
                    await asyncio.sleep(new_interval)

        except asyncio.CancelledError:
            BackLog.info(
                instance=self,
                message=f"task_func: Received a request to cancel",
            )

        except Exception as e:
            BackLog.exception(instance=self, message=f"Exception occurred")

        pass

    async def internal_onetime_func(self, task_func: any, **kwargs):
        try:
            await task_func(**kwargs)
        except Exception as e:
            BackLog.exception(instance=self, message=f"Exception ocurred")

    def create_task(self, task_func: any, interval: int, **kwargs):
        return asyncio.create_task(self.internal_func(task_func, interval, **kwargs))

    def create_onetime_task(self, task_func: any, **kwargs):
        return asyncio.create_task(self.internal_onetime_func(task_func, **kwargs))

    def stop_task(self, task: any):
        task.cancel()
