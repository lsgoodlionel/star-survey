#!/usr/bin/env python3
import argparse
import json
import posixpath
import re
import shlex
import sys
from datetime import datetime
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_PATH = Path("platform/docs/productization/capabilities.json")
SCHEMA_PATH = Path("platform/docs/productization/capabilities.schema.json")
OUTPUT_PATH = Path("platform/docs/productization/capability-map.md")
REQUIREMENT_INDEX_PATH = Path("platform/docs/traceability/requirement-index.md")

STATE_LABELS = {
    "accepted": "产品验收通过",
    "partial": "部分完成",
    "not_started": "未开始",
    "external": "外部依赖",
    "complete": "完成",
}
LAYER_LABELS = {
    "requirement": "需求",
    "backend": "后端",
    "frontend": "前端",
    "flow": "流程",
    "production": "生产",
    "traceability": "追溯",
}
KIND_LABELS = {
    "requirement-spec": "需求规格",
    "implementation": "实现",
    "automated-test": "自动化测试",
    "audit": "审计",
    "browser-test": "浏览器测试",
    "deployment-baseline": "部署基线",
    "production-run": "生产运行",
    "traceability-record": "追溯记录",
    "traceability-check": "追溯检查",
    "release-attestation": "发布证明",
}


class CapabilityValidationError(ValueError):
    pass


def load_document(repository_root=REPOSITORY_ROOT):
    return json.loads((Path(repository_root) / SOURCE_PATH).read_text(encoding="utf-8"))


def load_schema(repository_root=REPOSITORY_ROOT):
    return json.loads((Path(repository_root) / SCHEMA_PATH).read_text(encoding="utf-8"))


def _type_matches(value, expected):
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
    }
    return expected in checks and checks[expected](value)


def _resolve_ref(root_schema, reference):
    if not reference.startswith("#/"):
        raise CapabilityValidationError(f"unsupported schema reference: {reference}")
    node = root_schema
    for part in reference[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _schema_accepts(value, schema, root_schema, path):
    try:
        _validate_schema(value, schema, root_schema, path)
        return True
    except CapabilityValidationError:
        return False


def _validate_schema(value, schema, root_schema, path="$"):
    if "$ref" in schema:
        _validate_schema(value, _resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return

    expected_type = schema.get("type")
    if expected_type and not _type_matches(value, expected_type):
        raise CapabilityValidationError(f"{path} must be {expected_type}")
    if "const" in schema and value != schema["const"]:
        raise CapabilityValidationError(f"{path} must equal {schema['const']}")
    if "enum" in schema and value not in schema["enum"]:
        raise CapabilityValidationError(
            f"{path} is not one of: {', '.join(str(item) for item in schema['enum'])}"
        )

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise CapabilityValidationError(f"{path} is shorter than minLength")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise CapabilityValidationError(f"{path} does not match {schema['pattern']}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise CapabilityValidationError(f"{path} has fewer than minItems")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                raise CapabilityValidationError(f"{path} contains duplicate items")
        if "items" in schema:
            for index, item in enumerate(value):
                _validate_schema(item, schema["items"], root_schema, f"{path}[{index}]")
        if "contains" in schema and not any(
            _schema_accepts(item, schema["contains"], root_schema, f"{path}[{index}]")
            for index, item in enumerate(value)
        ):
            raise CapabilityValidationError(f"{path} does not contain a required item")

    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                raise CapabilityValidationError(f"{path} missing required field: {required}")
        properties = schema.get("properties", {})
        for name, child_schema in properties.items():
            if name in value:
                _validate_schema(value[name], child_schema, root_schema, f"{path}.{name}")
        if schema.get("additionalProperties") is False:
            unexpected = sorted(set(value) - set(properties))
            if unexpected:
                raise CapabilityValidationError(f"{path} has additional property: {unexpected[0]}")

    for item in schema.get("allOf", []):
        _validate_schema(value, item, root_schema, path)
    if "if" in schema and _schema_accepts(value, schema["if"], root_schema, path):
        if "then" in schema:
            _validate_schema(value, schema["then"], root_schema, path)


def _requirement_ids(repository_root):
    contents = (Path(repository_root) / REQUIREMENT_INDEX_PATH).read_text(encoding="utf-8")
    return set(re.findall(r"^\|\s*(R\d{2}-\d{2})\s*\|", contents, flags=re.MULTILINE))


def _accepted_rules(schema):
    capability_schema = schema["$defs"]["capability"]
    for condition in capability_schema.get("allOf", []):
        expected_status = condition.get("if", {}).get("properties", {}).get("status", {}).get("const")
        if expected_status != "accepted":
            continue
        then = condition.get("then", {})
        evidence_rules = then.get("properties", {}).get("evidence", {}).get("allOf", [])
        rules = {}
        for rule in evidence_rules:
            contained = rule.get("contains", {})
            layer = contained.get("properties", {}).get("layer", {}).get("const")
            if layer:
                rules[layer] = contained
        return rules, bool(then.get("x-requireDistinctEvidenceIdentities"))
    return {}, False


def _validate_accepted_evidence(document, schema):
    rules, require_distinct = _accepted_rules(schema)
    required_layers = set(rules)
    selected_evidence_ids = set()
    for capability in document.get("capabilities", []):
        if not isinstance(capability, dict) or capability.get("status") != "accepted":
            continue
        evidence = capability.get("evidence", [])
        observed_layers = {
            item.get("layer") for item in evidence if isinstance(item, dict)
        }
        missing = sorted(required_layers - observed_layers)
        if missing:
            raise CapabilityValidationError(
                f"accepted capability {capability.get('id', '<unknown>')} "
                f"is missing evidence layers: {', '.join(missing)}"
            )
        selected = []
        for layer, rule in rules.items():
            candidates = [
                item for item in evidence
                if isinstance(item, dict) and item.get("layer") == layer
            ]
            for item in candidates:
                if "kind" not in item:
                    raise CapabilityValidationError(
                        f"accepted {layer} evidence missing required field: kind"
                    )
            kind_schema = rule.get("properties", {}).get("kind", {})
            matching = [
                item for item in candidates
                if _schema_accepts(item.get("kind"), kind_schema, schema, f"accepted {layer} kind")
            ]
            if not matching:
                expected = kind_schema.get("const") or "/".join(kind_schema.get("enum", []))
                raise CapabilityValidationError(
                    f"accepted {layer} evidence kind must equal {expected}"
                )
            verification_schema = rule.get("properties", {}).get("verification", {})
            verified = [
                item for item in matching
                if "verification" in item and _schema_accepts(
                    item["verification"],
                    verification_schema,
                    schema,
                    f"accepted {layer} verification",
                )
            ]
            item = verified[0] if verified else matching[0]
            if "verification" not in item:
                raise CapabilityValidationError(
                    f"accepted {layer} evidence missing required field: verification"
                )
            _validate_schema(
                item["verification"],
                verification_schema,
                schema,
                f"accepted {layer} verification",
            )
            selected.append(item)
            selected_evidence_ids.add(id(item))
        if require_distinct:
            identities = {
                (item.get("path"), item.get("locator"))
                for item in selected
            }
            if len(identities) < len(required_layers):
                raise CapabilityValidationError(
                    f"accepted capability {capability.get('id', '<unknown>')} "
                    "requires six distinct evidence identities"
                )
    return selected_evidence_ids


def _command_is_structurally_executable(command):
    if not isinstance(command, str) or "\n" in command or "\0" in command:
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    return bool(parts) and not parts[0].startswith("-")


def _path_matches_accepted_layer(layer, path):
    checks = {
        "requirement": lambda value: value.startswith("platform/docs/traceability/") and value.endswith(".md"),
        "backend": lambda value: value.startswith("platform/services/") and "/src/test/" in value,
        "frontend": lambda value: value.startswith("platform/apps/") and (".test." in value or ".spec." in value),
        "flow": lambda value: value.startswith("platform/apps/") and "/e2e/" in value and ".spec." in value,
        "production": lambda value: value.startswith("platform/deploy/production/"),
        "traceability": lambda value: value.startswith("platform/docs/traceability/"),
    }
    return layer in checks and checks[layer](path)


def _validate_accepted_semantics(capability, evidence, contents):
    layer = evidence["layer"]
    path = evidence["path"]
    verification = evidence["verification"]
    if not _path_matches_accepted_layer(layer, path):
        raise CapabilityValidationError(
            f"accepted {layer} evidence path does not match accepted conventions: {path}"
        )

    if layer == "requirement":
        if verification["requirementId"] not in capability["requirementIds"]:
            raise CapabilityValidationError(
                "requirement verification does not name a mapped requirement: "
                f"{verification['requirementId']}"
            )
        if verification["acceptanceLocator"] not in contents:
            raise CapabilityValidationError(
                f"acceptanceLocator not found in {path}: {verification['acceptanceLocator']}"
            )
        return

    if not _command_is_structurally_executable(verification["command"]):
        raise CapabilityValidationError(
            f"accepted {layer} verification command is not structurally executable"
        )
    try:
        observed_at = datetime.fromisoformat(verification["observedAt"].replace("Z", "+00:00"))
    except ValueError as error:
        raise CapabilityValidationError(
            f"accepted {layer} verification observedAt is not a valid ISO timestamp"
        ) from error
    if observed_at.tzinfo is None:
        raise CapabilityValidationError(
            f"accepted {layer} verification observedAt must include a timezone"
        )

    locator_field = {
        "flow": "browserArtifactLocator",
        "traceability": "artifactLocator",
    }.get(layer)
    if locator_field and verification[locator_field] not in contents:
        raise CapabilityValidationError(
            f"{locator_field} not found in {path}: {verification[locator_field]}"
        )


def validate_document(document, repository_root=REPOSITORY_ROOT):
    repository_root = Path(repository_root).resolve()
    schema = load_schema(repository_root)
    accepted_evidence_ids = _validate_accepted_evidence(document, schema)
    _validate_schema(document, schema, schema)

    known_requirements = _requirement_ids(repository_root)
    seen_ids = set()
    for capability in document["capabilities"]:
        capability_id = capability["id"]
        if capability_id in seen_ids:
            raise CapabilityValidationError(f"duplicate capability id: {capability_id}")
        seen_ids.add(capability_id)
        for requirement_id in capability["requirementIds"]:
            if requirement_id not in known_requirements:
                raise CapabilityValidationError(f"unknown requirement id: {requirement_id}")

        for evidence in capability["evidence"]:
            relative_path = Path(evidence["path"])
            evidence_path = (repository_root / relative_path).resolve()
            if relative_path.is_absolute() or repository_root not in evidence_path.parents:
                raise CapabilityValidationError(f"evidence path escapes repository: {evidence['path']}")
            if not evidence_path.is_file():
                raise CapabilityValidationError(f"evidence path does not exist: {evidence['path']}")
            try:
                contents = evidence_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise CapabilityValidationError(
                    f"evidence path is not UTF-8 text: {evidence['path']}"
                ) from error
            if evidence["locator"] not in contents:
                raise CapabilityValidationError(
                    f"evidence locator not found in {evidence['path']}: {evidence['locator']}"
                )
            if evidence["layer"] == "requirement" and not any(
                requirement_id in evidence["locator"]
                for requirement_id in capability["requirementIds"]
            ):
                raise CapabilityValidationError(
                    f"requirement evidence locator does not name a mapped requirement: "
                    f"{evidence['locator']}"
                )
            if id(evidence) in accepted_evidence_ids:
                _validate_accepted_semantics(capability, evidence, contents)


def _state_label(value):
    return STATE_LABELS.get(value, value)


def render_document(document, schema=None):
    schema = schema or load_schema(REPOSITORY_ROOT)
    statuses = schema["$defs"]["capability"]["properties"]["status"]["enum"]
    capabilities = sorted(document["capabilities"], key=lambda item: item["id"])
    status_counts = {
        status: sum(item["status"] == status for item in capabilities)
        for status in statuses
    }
    lines = [
        "# 产品化能力基线",
        "",
        "> 本文件由 `platform/tools/productization/render_capabilities.py` 根据 "
        "`capabilities.json` 生成，请勿手工编辑。",
        "",
        "产品完成必须具备需求、后端、前端、流程、生产和追溯六层证据。"
        "后端完成不等于产品验收通过。",
        "",
        "## 汇总",
        "",
        f"- 能力项：{len(capabilities)}",
    ]
    lines.extend(f"- {_state_label(status)}：{status_counts[status]}" for status in statuses)
    lines.extend([
        "",
        "## 能力矩阵",
        "",
        "| 能力 | 需求编号 | 后端 | 前端 | 流程 | 生产 | 总体状态 | 下一波次 |",
        "|---|---|---|---|---|---|---|---|",
    ])
    for item in capabilities:
        requirement_ids = ", ".join(f"`{value}`" for value in sorted(item["requirementIds"]))
        lines.append(
            "| {title} | {requirements} | {backend} | {frontend} | {flow} | {production} | "
            "{status} | {wave} |".format(
                title=item["title"],
                requirements=requirement_ids,
                backend=_state_label(item["backend"]),
                frontend=_state_label(item["frontend"]),
                flow=_state_label(item["flow"]),
                production=_state_label(item["production"]),
                status=_state_label(item["status"]),
                wave=item["nextWave"],
            )
        )

    lines.extend(["", "## 证据", ""])
    for item in capabilities:
        lines.extend([f"### {item['title']} (`{item['id']}`)", ""])
        for evidence in sorted(
            item["evidence"],
            key=lambda value: (
                value["layer"],
                value["path"],
                value["locator"],
                value["description"],
            ),
        ):
            evidence_href = posixpath.relpath(evidence["path"], OUTPUT_PATH.parent.as_posix())
            lines.append(
                f"- **{LAYER_LABELS[evidence['layer']]}** "
                f"（{KIND_LABELS[evidence['kind']]}）"
                f"[`{evidence['path']}`]({evidence_href}) "
                f"定位 `{evidence['locator']}`：{evidence['description']}"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate and render the productization capability map.")
    parser.add_argument("--check", action="store_true", help="fail if the generated Markdown is stale")
    args = parser.parse_args(argv)
    try:
        document = load_document(REPOSITORY_ROOT)
        schema = load_schema(REPOSITORY_ROOT)
        validate_document(document, REPOSITORY_ROOT)
        rendered = render_document(document, schema)
        output_path = REPOSITORY_ROOT / OUTPUT_PATH
        if args.check:
            if not output_path.is_file() or output_path.read_text(encoding="utf-8") != rendered:
                print(f"stale generated file: {OUTPUT_PATH}", file=sys.stderr)
                return 1
            print(f"capability map is current: {OUTPUT_PATH}")
            return 0
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(f"rendered {OUTPUT_PATH}")
        return 0
    except (CapabilityValidationError, KeyError, TypeError, json.JSONDecodeError, OSError) as error:
        print(f"capability validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
