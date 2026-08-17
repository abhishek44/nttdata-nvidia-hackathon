from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import typer
import yaml

TOOL_CONFIGS: dict[str, dict[str, Any]] = {
    "recallzero_fetch_vehicle_data": {
        "_type": "recallzero_fetch_vehicle_data",
        "data_dir": "${RECALLZERO_DATA_DIR:-data}",
        "use_nim": True,
    },
    "recallzero_analyze_vehicle": {
        "_type": "recallzero_analyze_vehicle",
        "data_dir": "${RECALLZERO_DATA_DIR:-data}",
        "use_nim": True,
    },
    "recallzero_run_backtest": {
        "_type": "recallzero_run_backtest",
        "data_dir": "${RECALLZERO_DATA_DIR:-data}",
        "use_nim": True,
    },
    "recallzero_get_evidence": {
        "_type": "recallzero_get_evidence",
        "data_dir": "${RECALLZERO_DATA_DIR:-data}",
        "use_nim": True,
    },
    "recallzero_engineering_brief": {
        "_type": "recallzero_engineering_brief",
        "data_dir": "${RECALLZERO_DATA_DIR:-data}",
        "use_nim": True,
    },
}

TOOL_NAMES = list(TOOL_CONFIGS)
DATA_SOURCE_ID = "recallzero_vehicle_safety"
DATA_SOURCE = {
    "id": DATA_SOURCE_ID,
    "name": "RecallZero Vehicle Safety",
    "description": (
        "Investigate public NHTSA owner complaints, emerging failure patterns, visible recalls, "
        "source ODI evidence, and leakage-safe historical recall backtests."
    ),
    "tools": TOOL_NAMES,
    "requires_auth": False,
    "default_enabled": True,
}


def _append_unique(target: list[Any], values: list[str]) -> int:
    added = 0
    for value in values:
        if value not in target:
            target.append(value)
            added += 1
    return added


def _install_data_source_registry(functions: dict[str, Any]) -> tuple[bool, str | None]:
    """Add RecallZero to an AI-Q 2.x data-source registry when one exists.

    Newer stock AI-Q configurations let agents inherit tools through a central
    ``data_source_registry``. Adding an explicit ``tools`` list to such an agent would
    disable that inheritance, so the patcher updates the registry instead.
    """
    for function_name, value in functions.items():
        if not isinstance(value, dict) or value.get("_type") != "data_source_registry":
            continue
        sources = value.setdefault("sources", [])
        if not isinstance(sources, list):
            raise ValueError(f"functions.{function_name}.sources must be a list")
        existing = next(
            (item for item in sources if isinstance(item, dict) and item.get("id") == DATA_SOURCE_ID),
            None,
        )
        if existing is None:
            sources.append(deepcopy(DATA_SOURCE))
        else:
            existing.setdefault("name", DATA_SOURCE["name"])
            existing.setdefault("description", DATA_SOURCE["description"])
            existing.setdefault("requires_auth", DATA_SOURCE["requires_auth"])
            existing.setdefault("default_enabled", DATA_SOURCE["default_enabled"])
            tools = existing.setdefault("tools", [])
            if not isinstance(tools, list):
                raise ValueError(
                    f"functions.{function_name}.sources[{DATA_SOURCE_ID}].tools must be a list"
                )
            _append_unique(tools, TOOL_NAMES)
        return True, function_name
    return False, None


def patch_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    result = deepcopy(config)
    notes: list[str] = []
    functions = result.setdefault("functions", {})
    if not isinstance(functions, dict):
        raise ValueError("Top-level 'functions' must be a mapping")
    for name, value in TOOL_CONFIGS.items():
        functions[name] = deepcopy(value)

    registry_present, registry_name = _install_data_source_registry(functions)
    if registry_present:
        notes.append(
            f"Added RecallZero as a default-enabled source in functions.{registry_name}; "
            "agents without explicit tools lists will inherit it."
        )

    research_agent_types = {"shallow_research_agent", "deep_research_agent"}
    context_agent_types = {"intent_classifier", "clarifier_agent"}
    patched_agents = 0
    for name, value in functions.items():
        if not isinstance(value, dict):
            continue
        component_type = str(value.get("_type", ""))
        is_research_agent = component_type in research_agent_types or name in research_agent_types
        is_context_agent = component_type in context_agent_types or name in context_agent_types
        if not (is_research_agent or is_context_agent):
            continue

        # With a data-source registry, an omitted tools field means "inherit all".
        # Preserve that behavior. Explicit lists are still extended safely.
        if "tools" not in value and registry_present:
            continue
        # Older AI-Q configurations have no registry. Research agents need an explicit
        # list; context agents are only extended when they already expose one.
        if "tools" not in value and not registry_present and is_context_agent:
            continue

        tools = value.setdefault("tools", [])
        if not isinstance(tools, list):
            raise ValueError(f"functions.{name}.tools must be a list")
        _append_unique(tools, TOOL_NAMES)
        patched_agents += 1

    workflow = result.get("workflow")
    if isinstance(workflow, dict) and workflow.get("_type") in {"react_agent", "tool_calling_agent"}:
        tool_names = workflow.setdefault("tool_names", [])
        if not isinstance(tool_names, list):
            raise ValueError("workflow.tool_names must be a list")
        _append_unique(tool_names, TOOL_NAMES)
        patched_agents += 1

    if patched_agents:
        notes.append(f"Added RecallZero tools to {patched_agents} explicit agent/workflow configuration(s).")
    elif not registry_present:
        notes.append(
            "Registered RecallZero functions, but no recognized research agent, data-source registry, "
            "ReAct workflow, or tool-calling workflow was found. Add the tool names to the desired agent."
        )
    return result, notes


def patch(
    source: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    output: Path = typer.Option(..., "--output", "-o", help="Write the patched AI-Q/NAT YAML here."),
) -> None:
    """Merge RecallZero custom tools into a stock AI-Q Blueprint or NeMo Agent Toolkit config."""
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise typer.BadParameter("The source YAML must contain a top-level mapping")
    patched, notes = patch_config(raw)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(patched, sort_keys=False), encoding="utf-8")
    typer.echo(f"Wrote {output}")
    for note in notes:
        typer.echo(note)


def main() -> None:
    typer.run(patch)


if __name__ == "__main__":
    main()
