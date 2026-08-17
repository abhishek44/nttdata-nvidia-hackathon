"""Compatibility registrations for NVIDIA Agent Intelligence Toolkit 1.1 (`aiq` CLI).

Modern environments should install the `aiq` extra and use `recallzero.aiq.register`, which targets
NVIDIA NeMo Agent Toolkit and the `nat` CLI.
"""
from __future__ import annotations

from pydantic import Field

from aiq.builder.builder import Builder
from aiq.builder.function_info import FunctionInfo
from aiq.cli.register_workflow import register_function
from aiq.data_models.function import FunctionBaseConfig

from recallzero.aiq.common import (
    AnalyzeVehicleToolInput,
    BacktestToolInput,
    BriefToolInput,
    EvidenceToolInput,
    VehicleToolInput,
    execute_analyze,
    execute_backtest,
    execute_brief,
    execute_evidence,
    execute_fetch,
)


class RecallZeroToolConfig(FunctionBaseConfig):
    data_dir: str = Field(default="data")
    use_nim: bool = Field(default=True)


class FetchConfig(RecallZeroToolConfig, name="recallzero_fetch_vehicle_data"):
    pass


class AnalyzeConfig(RecallZeroToolConfig, name="recallzero_analyze_vehicle"):
    pass


class BacktestConfig(RecallZeroToolConfig, name="recallzero_run_backtest"):
    pass


class EvidenceConfig(RecallZeroToolConfig, name="recallzero_get_evidence"):
    pass


class BriefConfig(RecallZeroToolConfig, name="recallzero_engineering_brief"):
    pass


@register_function(config_type=FetchConfig)
async def fetch_vehicle_data(config: FetchConfig, _builder: Builder):
    async def _run(input_data: VehicleToolInput) -> str:
        return await execute_fetch(input_data, data_dir=config.data_dir, use_nim=config.use_nim)

    yield FunctionInfo.from_fn(_run, description="Fetch and cache public NHTSA complaints and recalls for a vehicle.")


@register_function(config_type=AnalyzeConfig)
async def analyze_vehicle(config: AnalyzeConfig, _builder: Builder):
    async def _run(input_data: AnalyzeVehicleToolInput) -> str:
        return await execute_analyze(input_data, data_dir=config.data_dir, use_nim=config.use_nim)

    yield FunctionInfo.from_fn(
        _run,
        description="Run RecallZero complaint normalization, semantic clustering, deterministic risk, and recall-gap analysis.",
    )


@register_function(config_type=BacktestConfig)
async def run_backtest(config: BacktestConfig, _builder: Builder):
    async def _run(input_data: BacktestToolInput) -> str:
        return await execute_backtest(input_data, data_dir=config.data_dir, use_nim=config.use_nim)

    yield FunctionInfo.from_fn(_run, description="Run the leakage-safe Recall Time Machine historical backtest.")


@register_function(config_type=EvidenceConfig)
async def get_evidence(config: EvidenceConfig, _builder: Builder):
    async def _run(input_data: EvidenceToolInput) -> str:
        return await execute_evidence(input_data, data_dir=config.data_dir, use_nim=config.use_nim)

    yield FunctionInfo.from_fn(_run, description="Retrieve one cached NHTSA complaint by ODI identifier.")


@register_function(config_type=BriefConfig)
async def engineering_brief(config: BriefConfig, _builder: Builder):
    async def _run(input_data: BriefToolInput) -> str:
        return await execute_brief(input_data, data_dir=config.data_dir, use_nim=config.use_nim)

    yield FunctionInfo.from_fn(_run, description="Render an evidence-grounded engineering brief for a saved signal.")
