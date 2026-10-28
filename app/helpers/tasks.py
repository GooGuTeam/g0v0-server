"""Single utility methods for concurrent tasks."""

import asyncio
from collections.abc import Awaitable


async def gather_limited[T](coroutines: list[Awaitable[T]], limit: int = 8) -> list[T]:
    """Run awaits concurrently, with a bound on how many are in flight.

    Args:
        coroutines: The awaits to run.
        limit: Maximum number of awaits running at the same time.

    Returns:
        list[T]: The results, in the order of `coroutines`.
    """
    semaphore = asyncio.Semaphore(limit)

    async def run(coroutine: Awaitable[T]) -> T:
        async with semaphore:
            return await coroutine

    return list(await asyncio.gather(*(run(coroutine) for coroutine in coroutines)))
