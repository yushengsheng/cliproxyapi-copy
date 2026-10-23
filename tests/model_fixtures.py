"""Synthetic server capabilities; never read installed clients or live credentials."""
from copy import deepcopy


def model_row(name, owner="test", context=272000, levels=("low", "medium", "high"), **fields):
    catalog = {
        "slug": name, "display_name": name, "description": "Synthetic test model",
        "default_reasoning_level": "medium" if "medium" in levels else (levels[0] if levels else None),
        "supported_reasoning_levels": [{"effort": level, "description": level} for level in levels],
        "shell_type": "shell_command", "visibility": "list", "supported_in_api": True,
        "priority": 0, "base_instructions": "You are a coding assistant.",
        "supports_reasoning_summaries": True, "supports_reasoning_summary_parameter": True,
        "support_verbosity": False, "default_verbosity": None, "apply_patch_tool_type": "freeform",
        "truncation_policy": {"mode": "tokens", "limit": 10000}, "context_window": context,
        "experimental_supported_tools": [], "input_modalities": ["text"],
    }
    catalog.update(deepcopy(fields))
    return {"id": name, "owned_by": owner, "codex": catalog}
