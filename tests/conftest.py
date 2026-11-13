import asyncio
import inspect


def pytest_pyfunc_call(pyfuncitem):
    """Run ``async def`` tests with asyncio.run (no pytest-asyncio dependency)."""
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        funcargs = pyfuncitem.funcargs
        kwargs = {n: funcargs[n] for n in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(asyncio.wait_for(pyfuncitem.obj(**kwargs), 20))
        return True
    return None
