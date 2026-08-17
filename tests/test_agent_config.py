from pathlib import Path

import yaml


def test_modern_nat_config_uses_native_tool_calling_without_thinking_key() -> None:
    config = yaml.safe_load(Path("configs/aiq/recallzero_agent.yml").read_text(encoding="utf-8"))
    assert config["workflow"]["_type"] == "tool_calling_agent"
    assert config["workflow"]["max_iterations"] == 8
    assert "thinking" not in config["llms"]["investigator_llm"]
    assert "additional_instructions" not in config["workflow"]
