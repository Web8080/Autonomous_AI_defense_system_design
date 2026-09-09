"""
Shared test fixtures.

Each service is a top-level `main.py`, so the previous `sys.path.insert(...)` +
`from main import app` pattern in every test file collided: whichever file
imported first won, and the rest silently tested that same app. That is why the
asset and control tests were `--ignore`d in CI rather than fixed.

`load_service_app` loads each service under a unique module name so the files
are genuinely independent.
"""
import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from typing import Generator

import pytest
from fastapi import FastAPI

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SHARED = BACKEND_ROOT / "shared"

if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

# A valid-length secret so decode_jwt is exercised rather than short-circuiting.
os.environ.setdefault("JWT_SECRET", "test_secret_that_is_at_least_32_chars_long!!")
os.environ.setdefault("JWT_ISSUER", "defense-api")
os.environ.setdefault("JWT_AUDIENCE", "defense-dashboard")


def load_service_app(service: str) -> FastAPI:
    """Import <service>/main.py under a unique module name and return its app."""
    path = BACKEND_ROOT / "services" / service / "main.py"
    module_name = f"_svc_{service}"
    if module_name in sys.modules:
        return sys.modules[module_name].app

    # The service dir must be importable for its own sibling imports (e.g. middleware).
    service_dir = str(path.parent)
    if service_dir not in sys.path:
        sys.path.insert(0, service_dir)

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.app


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
