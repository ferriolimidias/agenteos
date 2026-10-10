from app.agents.tools.registry import ToolRegistry


def test_tags_tool_is_available_with_database_scoped_permission():
    registry = ToolRegistry()
    assert "tags" in registry.available_tools({})


def test_legacy_tag_names_are_not_a_permission_source():
    registry = ToolRegistry()
    assert "tags" in registry.available_tools({"tags_ia_habilitadas": ["DC - Pediu informações"]})
