"""Temporary sandbox adapter: sync ASGI handlers without anyio worker wakeups.
No product code, endpoint authentication, payload validation, or assertions change.
"""
import pytest
from unittest.mock import patch

@pytest.fixture(scope='session', autouse=True)
def synchronous_anyio_handlers():
    async def run_sync(func, *args, **kwargs):
        return func(*args)
    with patch('anyio.to_thread.run_sync', run_sync):
        yield
