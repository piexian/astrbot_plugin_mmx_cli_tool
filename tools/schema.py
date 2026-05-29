"""Provider-neutral LLM tool parameter schema helpers."""

from __future__ import annotations

from typing import Any


_UNSAFE_SCHEMA_KEYS = {
    "additionalProperties",
    "allOf",
    "anyOf",
    "default",
    "dependentRequired",
    "dependentSchemas",
    "else",
    "if",
    "not",
    "oneOf",
    "then",
}


def string_param(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def integer_param(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


def number_param(description: str) -> dict[str, Any]:
    return {"type": "number", "description": description}


def boolean_param(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}


def object_parameters(
    properties: dict[str, dict[str, Any]],
    *,
    required: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build a schema subset accepted by common LLM tool adapters and gateways."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = list(required)

    validate_tool_schema(schema)
    return schema


def validate_tool_schema(schema: dict[str, Any]) -> None:
    _validate_node(schema, path="parameters")


def _validate_node(node: Any, *, path: str) -> None:
    if isinstance(node, dict):
        unsafe = sorted(_UNSAFE_SCHEMA_KEYS.intersection(node))
        if unsafe:
            raise ValueError(
                f"{path} uses unsupported schema keys: {', '.join(unsafe)}"
            )

        properties = node.get("properties")
        required = node.get("required")
        if required is not None:
            if not isinstance(required, list):
                raise ValueError(f"{path}.required must be a list")
            if not isinstance(properties, dict):
                raise ValueError(f"{path}.required is set without properties")
            missing = [name for name in required if name not in properties]
            if missing:
                raise ValueError(
                    f"{path}.required references undeclared keys: {', '.join(missing)}"
                )

        for key, value in node.items():
            _validate_node(value, path=f"{path}.{key}")
        return

    if isinstance(node, list):
        for index, value in enumerate(node):
            _validate_node(value, path=f"{path}[{index}]")
