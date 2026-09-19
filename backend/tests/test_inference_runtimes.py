"""Tests for inference runtime adapters (Phase 4 serving contract)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "inference_service"))

from runtimes import StubRuntime, resolve_runtime, TensorRTRuntime  # noqa: E402
import pytest


def test_missing_path_is_stub():
    rt = resolve_runtime("", "auto")
    assert isinstance(rt, StubRuntime)
    assert not rt.loaded()


def test_tensorrt_without_engine_fails_closed():
    with pytest.raises(RuntimeError, match="TensorRT"):
        TensorRTRuntime("/tmp/no-such-model.engine")


def test_unknown_runtime_raises():
    # need a real file so we don't short-circuit to stub
    p = Path(__file__).resolve()
    with pytest.raises(ValueError, match="unknown INFERENCE_RUNTIME"):
        resolve_runtime(str(p), "not-a-runtime")
