from pydantic import Field

# NeMo Agent Toolkit 1.8+ exposes a stable plugin-authoring facade. AI-Q 2.1
# deployments can still carry NAT 1.6/1.7, so retain the documented pre-facade
# imports as a compatibility path without requiring the deprecated ``aiq`` module.
try:
    from nat.plugin_api import Builder, FunctionBaseConfig, FunctionInfo, register_function
except ImportError:  # pragma: no cover - exercised only with older NAT runtimes
    from nat.builder.builder import Builder
    from nat.builder.function_info import FunctionInfo
    from nat.cli.register_workflow import register_function
    from nat.data_models.function import FunctionBaseConfig

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
    data_dir: str = Field(default="data", description="RecallZero local data/cache directory")
    use_nim: bool = Field(default=True, description="Use NVIDIA NIM with deterministic fallback")


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

    yield FunctionInfo.from_fn(
        _run,
        input_schema=VehicleToolInput,
        description=(
            "Fetch and cache public NHTSA complaints and recalls for an exact make, model, and set of model years. "
            "Use before analysis when data availability is uncertain."
        ),
    )


@register_function(config_type=AnalyzeConfig)
async def analyze_vehicle(config: AnalyzeConfig, _builder: Builder):
    async def _run(input_data: AnalyzeVehicleToolInput) -> str:
        return await execute_analyze(input_data, data_dir=config.data_dir, use_nim=config.use_nim)

    yield FunctionInfo.from_fn(
        _run,
        input_schema=AnalyzeVehicleToolInput,
        description=(
            "Run RecallZero's full evidence-first investigation: NHTSA ingestion, failure-signature extraction, "
            "hierarchical semantic clustering, deterministic trend/severity/risk scoring, and visible-recall cross-reference. "
            "Use this tool for questions about emerging issues in a selected vehicle population. Preserve the returned "
            "metric names and agent_grounding_rules exactly; a risk signal is not proof of a defect and low recall "
            "similarity is not proof of a recall-scope gap."
        ),
    )


@register_function(config_type=BacktestConfig)
async def run_backtest(config: BacktestConfig, _builder: Builder):
    async def _run(input_data: BacktestToolInput) -> str:
        return await execute_backtest(input_data, data_dir=config.data_dir, use_nim=config.use_nim)

    yield FunctionInfo.from_fn(
        _run,
        input_schema=BacktestToolInput,
        description=(
            "Run the leakage-safe Recall Time Machine against a known NHTSA campaign. The target recall text is "
            "withheld during detection and used only for post-hoc matching after weekly signals are frozen."
        ),
    )


@register_function(config_type=EvidenceConfig)
async def get_evidence(config: EvidenceConfig, _builder: Builder):
    async def _run(input_data: EvidenceToolInput) -> str:
        return await execute_evidence(input_data, data_dir=config.data_dir, use_nim=config.use_nim)

    yield FunctionInfo.from_fn(
        _run,
        input_schema=EvidenceToolInput,
        description="Retrieve the original cached NHTSA complaint record for a specific ODI identifier.",
    )


@register_function(config_type=BriefConfig)
async def engineering_brief(config: BriefConfig, _builder: Builder):
    async def _run(input_data: BriefToolInput) -> str:
        return await execute_brief(input_data, data_dir=config.data_dir, use_nim=config.use_nim)

    yield FunctionInfo.from_fn(
        _run,
        input_schema=BriefToolInput,
        description=(
            "Render a traceable engineering investigation brief for a saved RecallZero signal and run. "
            "The brief includes a deterministic evidence-critic consistency check."
        ),
    )
