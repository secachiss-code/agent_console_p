"""Write JSON Schema, OpenAPI, and the Markdown type index from the structs."""

import json
from pathlib import Path

import msgspec
from pydantic import BaseModel

from aconsole.contracts import ingest, methods
from aconsole.contracts.catalog import STRUCTS
from aconsole.contracts.mcp_models import (
    McpErrorBody,
    NodeAttachIn,
    NodeClaimIn,
    NodeSpec,
    NodeUpdateIn,
    PlanAddNodesIn,
    PlanCreateIn,
)

_SCHEMA = "https://json-schema.org/draft/2020-12/schema"


def struct_types() -> tuple[type[msgspec.Struct], ...]:
    return STRUCTS


def pydantic_models() -> tuple[type[BaseModel], ...]:
    return (
        McpErrorBody,
        NodeAttachIn,
        NodeClaimIn,
        NodeSpec,
        NodeUpdateIn,
        PlanAddNodesIn,
        PlanCreateIn,
    )


def _group(module_name: str) -> str:
    leaf = module_name.rsplit(".", maxsplit=1)[-1]
    groups = {"ingest": "ingest", "vault": "vault", "mcp_models": "mcp"}
    return groups.get(leaf, "ipc")


def schema_documents() -> dict[str, str]:
    documents: dict[str, str] = {}
    for struct in struct_types():
        refs, components = msgspec.json.schema_components((struct,))
        body = {"$schema": _SCHEMA, "$defs": components}
        body.update(refs[0])
        relative = f"contracts/schema/{_group(struct.__module__)}/{struct.__name__}.schema.json"
        documents[relative] = json.dumps(body, indent=2, sort_keys=True) + "\n"
    for model in pydantic_models():
        body = {"$schema": _SCHEMA}
        body.update(model.model_json_schema())
        relative = f"contracts/schema/mcp/{model.__name__}.schema.json"
        documents[relative] = json.dumps(body, indent=2, sort_keys=True) + "\n"
    documents["contracts/schema/ingest.openapi.yaml"] = json.dumps(_openapi(), indent=2, sort_keys=True) + "\n"
    return documents


def _ref(name: str) -> dict[str, object]:
    return {"$ref": f"#/components/schemas/{name}"}


def _json_response(description: str, schema: dict[str, object], *, media: str) -> dict[str, object]:
    return {"content": {media: {"schema": schema}}, "description": description}


def _problem(description: str) -> dict[str, object]:
    return _json_response(description, _ref("Problem"), media="application/problem+json")


def _error_responses() -> dict[str, object]:
    return {
        "401": _problem("unauthorized"),
        "403": _problem("forbidden"),
        "404": _problem("not found"),
        "413": _problem("payload too large"),
    }


def _tab_parameter() -> dict[str, object]:
    return {"in": "path", "name": "tab_id", "required": True, "schema": {"type": "string"}}


def _hook_responses() -> dict[str, object]:
    # additionalProperties is false so `{}` matches only this branch, not HookResponse.
    empty: dict[str, object] = {"additionalProperties": False, "properties": {}, "type": "object"}
    schema: dict[str, object] = {"oneOf": [empty, _ref("HookResponse")]}
    body: dict[str, object] = {"200": _json_response("hook decision or empty object", schema, media="application/json")}
    body.update(_error_responses())
    return body


def _accept_responses() -> dict[str, object]:
    body: dict[str, object] = {
        "200": _json_response("invalid payload", _ref("EmptyObject"), media="application/json"),
        "204": {"description": "accepted"},
    }
    body.update(_error_responses())
    return body


def _post(responses: dict[str, object]) -> dict[str, object]:
    return {"parameters": [_tab_parameter()], "responses": responses}


def _openapi() -> dict[str, object]:
    schemas: dict[str, object] = {}
    for struct in struct_types():
        if struct.__module__ != ingest.__name__:
            continue
        _refs, components = msgspec.json.schema_components((struct,))
        schemas.update(components)
    health: dict[str, object] = {
        "200": _json_response("ok", _ref("Health"), media="application/json"),
        "403": _problem("forbidden"),
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "AConsole ingest", "version": "0.2.0"},
        "paths": {
            "/v1/health": {"get": {"responses": health}},
            "/v1/tabs/{tab_id}/claude/hook": {"post": _post(_hook_responses())},
            "/v1/tabs/{tab_id}/claude/statusline": {"post": _post(_accept_responses())},
            "/v1/tabs/{tab_id}/codex/hook": {"post": _post(_hook_responses())},
            "/v1/tabs/{tab_id}/codex/notify": {"post": _post(_accept_responses())},
        },
        "components": {"schemas": schemas},
    }


def markdown_document() -> str:
    lines = ["# Generated contract types", "", "Regenerate with `aconsole.contracts.generate.write_artifacts`.", ""]
    for struct in struct_types():
        lines.append(f"## {struct.__name__}")
        lines.append("")
        lines.append(f"Module: `{struct.__module__}`")
        lines.append("")
        lines.extend(f"- `{field.encode_name}`" for field in msgspec.structs.fields(struct))
        lines.append("")
    lines.append("## METHODS")
    lines.append("")
    for method in methods.METHODS:
        flag = " long" if method.long else ""
        capability = f" capability={method.capability}" if method.capability else ""
        lines.append(f"- `{method.name}` {method.params} → {method.result}{flag}{capability}")
    lines.append("")
    return "\n".join(lines)


def documents() -> dict[str, str]:
    rendered = schema_documents()
    rendered["docs/contracts/generated/types.md"] = markdown_document()
    return rendered


def write_artifacts(repo: Path) -> tuple[str, ...]:
    written: list[str] = []
    for relative, text in documents().items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text, encoding="utf-8")
        written.append(relative)
    return tuple(written)
