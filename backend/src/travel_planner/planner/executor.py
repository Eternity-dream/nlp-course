import asyncio
import logging
from contextlib import suppress

from starlette.concurrency import run_in_threadpool

from travel_planner.domain.errors import AppError
from travel_planner.planner.workflow import AwaitingInput

logger = logging.getLogger(__name__)


class PlanExecutor:
    def __init__(self, store, workflow, editor=None):
        self.store, self.workflow = store, workflow
        self.editor = editor
        self.wake = asyncio.Event()
        self.task = None

    async def start(self):
        await run_in_threadpool(self.store.interrupt_unfinished)
        self.task = asyncio.create_task(self._run(), name="plan-executor")

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        await run_in_threadpool(self.store.interrupt_unfinished)

    async def _run(self):
        while True:
            self.wake.clear()
            try:
                job = await run_in_threadpool(self.store.claim)
                if job:
                    await self._execute(job)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("plan_executor_storage_error")
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1)
            except TimeoutError:
                pass

    async def _execute(self, job: dict):
        try:
            handler = self.editor if job["kind"] == "edit" else self.workflow
            result = await handler.run(job)
            await run_in_threadpool(self.store.save, job, result)
        except AwaitingInput as exc:
            await run_in_threadpool(
                self.store.update,
                job["id"],
                "awaiting_input",
                "conditions",
                questions=exc.questions,
            )
        except AppError as exc:
            current = await run_in_threadpool(self.store.get, job["id"])
            await run_in_threadpool(
                self.store.update, job["id"], "failed", current["stage"], error=exc.payload()
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            current = await run_in_threadpool(self.store.get, job["id"])
            error = AppError(
                "planning_failed",
                "规划处理失败，请重新提交",
                500,
                retryable=True,
                object_id=job["id"],
            ).payload()
            await run_in_threadpool(
                self.store.update, job["id"], "failed", current["stage"], error=error
            )
