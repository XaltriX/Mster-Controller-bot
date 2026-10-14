import asyncio
import logging

_tasks: set = set()
log = logging.getLogger("tasks")


def _done(task: asyncio.Task):
    _tasks.discard(task)
    if not task.cancelled() and task.exception():
        log.error("background task failed", exc_info=task.exception())


def spawn(coro) -> asyncio.Task:
    """create_task() that keeps a strong reference (Python may otherwise garbage
    collect a running task) and logs any exception instead of losing it."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_done)
    return task
