from recallzero.aiq.blueprint_patch import patch_config


def test_patch_stock_aiq_config() -> None:
    source = {
        "functions": {
            "intent_classifier": {
                "_type": "intent_classifier",
                "llm": "model",
                "tools": ["web_search"],
            },
            "shallow_research_agent": {
                "_type": "shallow_research_agent",
                "llm": "model",
                "tools": ["web_search"],
            },
            "deep_research_agent": {
                "_type": "deep_research_agent",
                "orchestrator_llm": "model",
                "tools": ["paper_search"],
            },
        },
        "workflow": {"_type": "chat_deepresearcher_agent"},
    }
    patched, notes = patch_config(source)
    assert "recallzero_analyze_vehicle" in patched["functions"]
    for agent_name in ("intent_classifier", "shallow_research_agent", "deep_research_agent"):
        assert "recallzero_analyze_vehicle" in patched["functions"][agent_name]["tools"]
        assert "recallzero_run_backtest" in patched["functions"][agent_name]["tools"]
    assert notes


def test_patch_react_workflow() -> None:
    source = {"functions": {}, "workflow": {"_type": "react_agent", "tool_names": ["current_datetime"]}}
    patched, _ = patch_config(source)
    assert "recallzero_get_evidence" in patched["workflow"]["tool_names"]


def test_patch_current_aiq_data_source_registry_without_breaking_inheritance() -> None:
    source = {
        "functions": {
            "data_sources": {
                "_type": "data_source_registry",
                "sources": [
                    {
                        "id": "web_search",
                        "name": "Web Search",
                        "description": "Search the web.",
                        "tools": ["web_search_tool"],
                    }
                ],
            },
            "shallow_research_agent": {
                "_type": "shallow_research_agent",
                "llm": "model",
            },
            "deep_research_agent": {
                "_type": "deep_research_agent",
                "orchestrator_llm": "model",
                "exclude_tools": ["web_search_tool"],
            },
        },
        "workflow": {"_type": "chat_deepresearcher_agent"},
    }

    patched, notes = patch_config(source)
    sources = patched["functions"]["data_sources"]["sources"]
    recallzero_source = next(item for item in sources if item["id"] == "recallzero_vehicle_safety")
    assert "recallzero_analyze_vehicle" in recallzero_source["tools"]
    assert "recallzero_run_backtest" in recallzero_source["tools"]
    # Omitted tools means inherit from the registry in current AI-Q. The patcher must
    # not create a narrow list that disables existing web-source inheritance.
    assert "tools" not in patched["functions"]["shallow_research_agent"]
    assert "tools" not in patched["functions"]["deep_research_agent"]
    assert any("data_sources" in note for note in notes)


def test_patch_is_idempotent_for_registry_and_explicit_tools() -> None:
    source = {
        "functions": {
            "data_sources": {"_type": "data_source_registry", "sources": []},
            "shallow_research_agent": {
                "_type": "shallow_research_agent",
                "llm": "model",
                "tools": ["web_search_tool"],
            },
        }
    }
    once, _ = patch_config(source)
    twice, _ = patch_config(once)
    sources = twice["functions"]["data_sources"]["sources"]
    assert sum(item.get("id") == "recallzero_vehicle_safety" for item in sources) == 1
    tools = twice["functions"]["shallow_research_agent"]["tools"]
    assert tools.count("recallzero_analyze_vehicle") == 1
    assert tools.count("recallzero_engineering_brief") == 1
