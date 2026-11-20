"""Write JSON Schema, OpenAPI, and the Markdown type index from the structs."""

import importlib
import json
import pkgutil
from pathlib import Path
from types import ModuleType

import msgspec
from pydantic import BaseModel

from aconsole.contracts import ingest, methods
from aconsole.contracts.catalog import STRUCTS
from aconsole.contracts.errors import ErrorObject
from aconsole.contracts.mcp_models import (
    GateRunIn,
    McpErrorBody,
    NodeAttachIn,
    NodeClaimIn,
    NodeEscalateIn,
    NodeSpec,
    NodeUpdateIn,
    PlanAddNodesIn,
    PlanCreateIn,
    StepBeginIn,
    StepResetIn,
    StepSubmitIn,
)

_SCHEMA = "https://json-schema.org/draft/2020-12/schema"


def struct_types() -> tuple[type[msgspec.Struct], ...]:
    return STRUCTS


def pydantic_models() -> tuple[type[BaseModel], ...]:
    return (
        McpErrorBody,
        NodeAttachIn,
        NodeClaimIn,
        NodeEscalateIn,
        NodeSpec,
        NodeUpdateIn,
        PlanAddNodesIn,
        PlanCreateIn,
        StepBeginIn,
        StepSubmitIn,
        StepResetIn,
        GateRunIn,
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
    documents["contracts/schema/api.openapi.yaml"] = json.dumps(_api_openapi(), indent=2, sort_keys=True) + "\n"
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


def _contract_structs() -> dict[str, type[msgspec.Struct]]:
    """Every struct defined under ``aconsole.contracts``, including ones absent from ``STRUCTS``."""
    import aconsole.contracts as package  # noqa: PLC0415 -- pkgutil needs the package path

    found: dict[str, type[msgspec.Struct]] = {}
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name == "generate":
            continue
        module = importlib.import_module(f"aconsole.contracts.{module_info.name}")
        found.update(_structs_defined_in(module))
    return found


def _structs_defined_in(module: ModuleType) -> dict[str, type[msgspec.Struct]]:
    found: dict[str, type[msgspec.Struct]] = {}
    for name in dir(module):
        value: object = getattr(module, name)  # pyright: ignore[reportAny] -- module attributes are untyped
        if isinstance(value, type) and issubclass(value, msgspec.Struct) and value.__module__ == module.__name__:
            found[name] = value
    return found


def _api_openapi() -> dict[str, object]:
    """One POST path per published method. The socket and the sidecar stay out of this document."""
    by_name = _contract_structs()
    selected = methods.rest_methods()
    ordered: list[type[msgspec.Struct]] = [ErrorObject]
    seen = {"ErrorObject"}
    for method in selected:
        for name in (method.params, method.result):
            if name in seen:
                continue
            struct = by_name.get(name)
            if struct is None:
                message = f"{method.name} names unknown struct {name}"
                raise KeyError(message)
            seen.add(name)
            ordered.append(struct)
    _refs, components = msgspec.json.schema_components(tuple(ordered))
    schemas = _openapi_schemas(components)
    paths: dict[str, object] = {}
    for method in selected:
        paths[f"/api/v1/{method.name}"] = {"post": _api_operation(method)}
    return {
        "openapi": "3.1.0",
        "info": {"title": "AConsole methods", "version": "1.0.0"},
        "paths": paths,
        "components": {
            "schemas": schemas,
            "securitySchemes": {"apiToken": {"type": "http", "scheme": "bearer"}},
        },
        "security": [{"apiToken": []}],
    }


def _openapi_schemas(components: dict[str, object]) -> dict[str, object]:
    return {key: _rewrite_refs(value) for key, value in components.items()}


def _rewrite_refs(value: object) -> object:
    if isinstance(value, str):
        return value.replace("#/$defs/", "#/components/schemas/")
    if isinstance(value, list):
        return [_rewrite_refs(item) for item in value]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, dict):
        rewritten: dict[str, object] = {}
        for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
            rewritten[str(key)] = _rewrite_refs(item)  # pyright: ignore[reportUnknownArgumentType]
        return rewritten
    return value


def _with_timeout(body: dict[str, object], header: dict[str, object]) -> dict[str, object]:
    timed = dict(body)
    timed["headers"] = {"X-Aconsole-Timeout-Ms": header}
    return timed


def _api_operation(method: methods.Method) -> dict[str, object]:
    tag = method.capability if method.capability is not None else "core"
    header: dict[str, object] = {
        "description": "Milliseconds the caller should wait for this method.",
        "schema": {"const": methods.rest_timeout_ms(method), "type": "integer"},
    }
    described: dict[str, dict[str, object]] = {
        "200": _json_response("method result", _ref(method.result), media="application/json"),
        "400": _json_response("invalid params", _ref("ErrorObject"), media="application/json"),
        "401": _problem("unauthorized"),
        "403": _json_response("forbidden", _ref("ErrorObject"), media="application/json"),
        "404": _json_response("method not published", _ref("ErrorObject"), media="application/json"),
        "409": _json_response("domain error", _ref("ErrorObject"), media="application/json"),
        "413": _problem("payload too large"),
        "500": _json_response("internal error", _ref("ErrorObject"), media="application/json"),
        "503": _json_response("busy", _ref("ErrorObject"), media="application/json"),
    }
    responses: dict[str, object] = {code: _with_timeout(body, header) for code, body in described.items()}
    return {
        "tags": [tag],
        "operationId": method.name.replace("/", "_"),
        "requestBody": {"required": True, "content": {"application/json": {"schema": _ref(method.params)}}},
        "responses": responses,
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
