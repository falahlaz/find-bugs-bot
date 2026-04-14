import asyncio
import logging

logger = logging.getLogger(__name__)


class JobQueue:
    def __init__(self):
        self._queue: asyncio.Queue | None = None
        self._worker_task: asyncio.Task | None = None
        self._process_callback = None
        self._bot = None
        self.current_job: dict | None = None
        self._paused = False
        self._draining = False
        self._drain_event: asyncio.Event | None = None

    @property
    def queue_depth(self) -> int:
        if self._queue is None:
            return 0
        return self._queue.qsize()

    def initialize(self, bot):
        self._queue = asyncio.Queue(maxsize=10)
        self._bot = bot
        self._paused = False
        self._draining = False
        self._drain_event = asyncio.Event()
        self.current_job = None

    async def start_worker(self, process_callback):
        self._process_callback = process_callback
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("Job queue worker started")

    async def enqueue(self, job: dict) -> bool:
        if self._queue is None:
            return False
        if self._draining:
            return False
        try:
            self._queue.put_nowait(job)
            logger.info("Enqueued job for transaction_id=%s", job["transaction_id"])
            return True
        except asyncio.QueueFull:
            logger.warning("Queue full, rejected job for transaction_id=%s", job["transaction_id"])
            return False

    async def _worker(self):
        while True:
            if self._draining and self._queue.empty():
                break

            if self._paused:
                await asyncio.sleep(1)
                continue

            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            self.current_job = job
            logger.info("Processing job for transaction_id=%s", job["transaction_id"])

            try:
                await self._process_callback(job, self._bot)
            except Exception:
                logger.exception("Error processing job for transaction_id=%s", job["transaction_id"])
            finally:
                self.current_job = None
                self._queue.task_done()

        if self._drain_event:
            self._drain_event.set()

    async def drain(self) -> bool:
        if self._queue is None:
            return True

        pending = self._queue.qsize()
        if self.current_job:
            pending += 1

        logger.info("Draining %d remaining jobs...", pending)
        self._draining = True

        if self._drain_event:
            self._drain_event.clear()

        try:
            await asyncio.wait_for(self._drain_event.wait(), timeout=300)
            logger.info("Queue drained successfully")
            return True
        except asyncio.TimeoutError:
            logger.warning("Drain timed out after 5 minutes, forcing exit")
            return False
        finally:
            if self._worker_task:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass

    def pause(self):
        self._paused = True
        logger.info("Queue paused")

    def resume(self):
        self._paused = False
        logger.info("Queue resumed")

    @property
    def is_paused(self) -> bool:
        return self._paused


job_queue = JobQueue()