"""Regression coverage for the annotation failure seen in NAT FunctionInfo.from_fn.

The full plugin-construction test is executed when nvidia-nat is installed. The
module-level assertions still protect the release in lightweight test environments.
"""

from __future__ import annotations

import inspect
import typing
from pathlib import Path

import pytest

from recallzero.aiq.common import (
    AnalyzeVehicleToolInput,
    BacktestToolInput,
    BriefToolInput,
    EvidenceToolInput,
    VehicleToolInput,
)


def test_tool_input_models_are_runtime_types() -> None:
    for schema in (
        VehicleToolInput,
        AnalyzeVehicleToolInput,
        BacktestToolInput,
        EvidenceToolInput,
        BriefToolInput,
    ):
        assert inspect.isclass(schema)
        assert schema.model_json_schema()["type"] == "object"


def test_register_module_avoids_deferred_annotations() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "recallzero" / "aiq" / "register.py").read_text()
    assert "from __future__ import annotations" not in source
    assert "input_schema=VehicleToolInput" in source
    assert "input_schema=AnalyzeVehicleToolInput" in source
    assert "input_schema=BacktestToolInput" in source
    assert "input_schema=EvidenceToolInput" in source
    assert "input_schema=BriefToolInput" in source


def _make_eager_vehicle_tool():
    # Compile without inheriting this test module's ``from __future__ import annotations``.
    # recallzero.aiq.register deliberately does not enable deferred annotations, so NAT
    # receives concrete runtime types. Plain exec() would inherit the caller's future
    # flags and accidentally turn VehicleToolInput into a string, reproducing an old
    # NAT wrapper bug that the production registration module does not trigger.
    namespace = {"VehicleToolInput": VehicleToolInput}
    code = compile(
        "async def tool(input_data: VehicleToolInput) -> str:\n    return input_data.model\n",
        "<recallzero-nat-compat-test>",
        "exec",
        dont_inherit=True,
    )
    exec(code, namespace)
    return namespace["tool"]


def test_nat_compat_fixture_really_uses_eager_runtime_annotations() -> None:
    tool = _make_eager_vehicle_tool()
    assert tool.__annotations__["input_data"] is VehicleToolInput
    assert typing.get_type_hints(tool)["input_data"] is VehicleToolInput


def test_nat_function_info_accepts_explicit_schema_when_nat_is_installed() -> None:
    nat = pytest.importorskip("nat")
    del nat
    try:
        from nat.plugin_api import FunctionInfo
    except ImportError:
        from nat.builder.function_info import FunctionInfo

    tool = _make_eager_vehicle_tool()
    info = FunctionInfo.from_fn(tool, input_schema=VehicleToolInput, description="test")
    assert info.input_schema is VehicleToolInput
