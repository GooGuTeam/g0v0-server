"""Background task management utilities.

This module provides utilities for running tasks asynchronously in the background,
similar to FastAPI's BackgroundTasks but for use outside request handlers.
"""

import asyncio
from collections.abc import Callable, Sequence
import functools
import inspect
from typing import Any


# Reference: https://github.com/encode/starlette/blob/master/starlette/_utils.py
def is_async_callable(obj: Any) -> bool:
    """Check if an object is an async callable.

    Args:
        obj: The object to check.

    Returns:
        True if the object is async callable, False otherwise.
    """
    while isinstance(obj, functools.partial):
        obj = obj.func

    return inspect.iscoroutinefunction(obj)


async def run_in_threadpool[**P, T](func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Run a synchronous function in a thread pool.

    Args:
        func: The synchronous function to run.
        *args: Positional arguments for the function.
        **kwargs: Keyword arguments for the function.

    Returns:
        The result of the function.
    """
    func = functools.partial(func, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(None, func)


class BackgroundTasks:
    """A simple background task manager for fire-and-forget coroutines.

    Similar to FastAPI's BackgroundTasks but for use outside request handlers.
    """

    def __init__(self, tasks: Sequence[asyncio.Task] | None = None):
        """Initialize the task manager.

        Args:
            tasks: Optional sequence of existing tasks to manage.
        """
        self.tasks = set(tasks) if tasks else set()

    def add_task[**P](self, func: Callable[P, Any], *args: P.args, **kwargs: P.kwargs) -> None:
        """Add a function to run as a background task.

        Args:
            func: The function to run (can be sync or async).
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.
        """
        coro = func(*args, **kwargs) if is_async_callable(func) else run_in_threadpool(func, *args, **kwargs)
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def stop(self) -> None:
        """Cancel all running tasks and clear the task set."""
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()


bg_tasks = BackgroundTasks()
