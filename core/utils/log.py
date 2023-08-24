import logging

DEBUG = True


class BackLog:
    logger = logging.getLogger("chat-automation-backend-logger")
    console_handler = logging.StreamHandler()

    if DEBUG is True:
        logger.setLevel(logging.DEBUG)
        console_handler.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
        console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    def __init__(self) -> None:
        pass

    def debug(instance, message):
        BackLog.logger.debug(f"[{instance.__class__.__name__}] {message}")

    def info(instance, message):
        BackLog.logger.info(f"[{instance.__class__.__name__}] {message}")

    def exception(instance, message):
        BackLog.logger.exception((f"[{instance.__class__.__name__}] {message}"))
        import traceback

        print(traceback.print_exc())
