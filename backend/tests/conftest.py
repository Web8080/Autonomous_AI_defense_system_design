import os
import asyncio
from typing import AsyncGenerator, Generator

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure shared is on path when running tests from backend/
os.environ.setdefault("PYTHONPATH", os.path.join(os.path.dirname(__file__), ".."))

pytest_plugins = []


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
