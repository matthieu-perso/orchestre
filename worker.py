"""
ARQ worker entrypoint.

Run with:
  python worker.py

Or via Docker:
  docker-compose up worker
"""
import logging
import sys

from core.queue.worker import WorkerSettings  # noqa: F401 - imported for arq discovery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    import asyncio
    from arq import run_worker

    logger.info("Starting Orchestre ARQ worker...")
    asyncio.run(run_worker(WorkerSettings))
