#!/usr/bin/env python3
import argparse
import json
import posixpath
import re
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_PATH = Path("platform/docs/productization/capabilities.json")
SCHEMA_PATH = Path("platform/docs/productization/capabilities.schema.json")
OUTPUT_PATH = Path("platform/docs/productization/capability-map.md")
REQUIREMENT_INDEX_PATH = Path("platform/docs/traceability/requirement-index.md")

REQUIRED_CAPABILITY_FIELDS = {
    "id",
    "title",
    "requirementIds",
    "backend",
    "frontend",
    "flow",
    "production",
    "status",
    "evidence",
    "nextWave",
}
DELIVERY_STATES = {"not-started", "partial", "complete"}
CAPABILITY_STATES = {"planned", "in-progress", "engineering-complete", "accepted"}
EVIDENCE_LAYERS = {"requirement", "backend", "frontend", "flow", "production", "traceability"}
WAVES = {f"Wave {number}" for number in range(7)}
STATE_LABELS = {
    "not-started": "未开始",
    "partial": "部分完成",
    "complete": "完成",
    "planned": "已规划",
    "in-progress": "进行中",
    "engineering-complete": "工程切片完成",
    "accepted": "产品验收通过",
}
LAYER_LABELS = {
    "requirement": "需求",
    "backend": "后端",
    "frontend": "前端",
    "flow": "流程",
    "production": "生产",
    "traceability": "追溯",
}


class CapabilityValidationError(ValueError):
    pass


def load_document(repository_root=REPOSITORY_ROOT):
    path = Path(repository_root) / SOURCE_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def _requirement_ids(repository_root):
    path = Path(repository_root) / REQUIREMENT_INDEX_PATH
    contents = path.read_text(encoding="utf-8")
    return set(re.findall(r"^\|\s*(R\d{2}-\d{2})\s*\|", contents, flags=re.MULTILINE))


def _validate_schema_shape(document, repository_root):
    schema_path = Path(repository_root) / SCHEMA_PATH
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_required = set(schema["$defs"]["capability"]["required"])
    if schema_required != REQUIRED_CAPABILITY_FIELDS:
        raise CapabilityValidationError("schema required fields do not match the renderer contract")

    allowed_top_level = {"$schema", "schemaVersion", "capabilities"}
    unexpected = set(document) - allowed_top_level
    if unexpected:
        raise CapabilityValidationError(f"unexpected top-level field: {sorted(unexpected)[0]}")
    if document.get("schemaVersion") != 1:
        raise CapabilityValidationError("schemaVersion must be 1")
    if not isinstance(document.get("capabilities"), list) or not document["capabilities"]:
        raise CapabilityValidationError("capabilities must be a non-empty array")


def validate_document(document, repository_root=REPOSITORY_ROOT):
    repository_root = Path(repository_root).resolve()
    _validate_schema_shape(document, repository_root)
    known_requirements = _requirement_ids(repository_root)
    seen_ids = set()

    for capability in document["capabilities"]:
        capability_id = capability.get("id", "<unknown>") if isinstance(capability, dict) else "<unknown>"
        if not isinstance(capability, dict):
            raise CapabilityValidationError("capability must be an object")
        missing = REQUIRED_CAPABILITY_FIELDS - set(capability)
        if missing:
            raise CapabilityValidationError(
                f"capability {capability_id} missing required field: {sorted(missing)[0]}"
            )
        unexpected = set(capability) - REQUIRED_CAPABILITY_FIELDS
        if unexpected:
            raise CapabilityValidationError(
                f"capability {capability_id} has unexpected field: {sorted(unexpected)[0]}"
            )
        if not isinstance(capability_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", capability_id):
            raise CapabilityValidationError(f"invalid capability id: {capability_id}")
        if capability_id in seen_ids:
            raise CapabilityValidationError(f"duplicate capability id: {capability_id}")
        seen_ids.add(capability_id)

        if not isinstance(capability["title"], str) or not capability["title"].strip():
            raise CapabilityValidationError(f"capability {capability_id} has an empty title")
        requirements = capability["requirementIds"]
        if not isinstance(requirements, list) or not requirements:
            raise CapabilityValidationError(f"capability {capability_id} has no requirementIds")
        if len(requirements) != len(set(requirements)):
            raise CapabilityValidationError(f"capability {capability_id} has duplicate requirementIds")
        for requirement_id in requirements:
            if requirement_id not in known_requirements:
                raise CapabilityValidationError(f"unknown requirement id: {requirement_id}")

        for field in ("backend", "frontend", "flow", "production"):
            if capability[field] not in DELIVERY_STATES:
                raise CapabilityValidationError(
                    f"capability {capability_id} has invalid {field}: {capability[field]}"
                )
        if capability["status"] not in CAPABILITY_STATES:
            raise CapabilityValidationError(
                f"capability {capability_id} has invalid status: {capability['status']}"
            )
        if capability["nextWave"] not in WAVES:
            raise CapabilityValidationError(
                f"capability {capability_id} has invalid nextWave: {capability['nextWave']}"
            )

        evidence = capability["evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise CapabilityValidationError(f"capability {capability_id} has no evidence")
        evidence_layers = set()
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"layer", "path", "description"}:
                raise CapabilityValidationError(f"capability {capability_id} has malformed evidence")
            if item["layer"] not in EVIDENCE_LAYERS:
                raise CapabilityValidationError(
                    f"capability {capability_id} has unknown evidence layer: {item['layer']}"
                )
            evidence_layers.add(item["layer"])
            relative_path = Path(item["path"])
            evidence_path = (repository_root / relative_path).resolve()
            if relative_path.is_absolute() or repository_root not in evidence_path.parents:
                raise CapabilityValidationError(f"evidence path escapes repository: {item['path']}")
            if not evidence_path.is_file():
                raise CapabilityValidationError(f"evidence path does not exist: {item['path']}")
            if not isinstance(item["description"], str) or not item["description"].strip():
                raise CapabilityValidationError(f"capability {capability_id} has empty evidence description")

        if capability["status"] == "accepted":
            missing_layers = EVIDENCE_LAYERS - evidence_layers
            if missing_layers:
                raise CapabilityValidationError(
                    f"accepted capability {capability_id} is missing evidence layers: "
                    + ", ".join(sorted(missing_layers))
                )
            incomplete_layers = [
                field
                for field in ("backend", "frontend", "flow", "production")
                if capability[field] != "complete"
            ]
            if incomplete_layers:
                raise CapabilityValidationError(
                    f"accepted capability {capability_id} has incomplete layers: "
                    + ", ".join(incomplete_layers)
                )


def render_document(document):
    capabilities = sorted(document["capabilities"], key=lambda item: item["id"])
    status_counts = {
        status: sum(item["status"] == status for item in capabilities)
        for status in ("accepted", "engineering-complete", "in-progress", "planned")
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
        f"- 产品验收通过：{status_counts['accepted']}",
        f"- 工程切片完成：{status_counts['engineering-complete']}",
        f"- 进行中：{status_counts['in-progress']}",
        f"- 已规划：{status_counts['planned']}",
        "",
        "## 能力矩阵",
        "",
        "| 能力 | 需求编号 | 后端 | 前端 | 流程 | 生产 | 总体状态 | 下一波次 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in capabilities:
        requirement_ids = ", ".join(f"`{value}`" for value in sorted(item["requirementIds"]))
        lines.append(
            "| {title} | {requirements} | {backend} | {frontend} | {flow} | {production} | "
            "{status} | {wave} |".format(
                title=item["title"],
                requirements=requirement_ids,
                backend=STATE_LABELS[item["backend"]],
                frontend=STATE_LABELS[item["frontend"]],
                flow=STATE_LABELS[item["flow"]],
                production=STATE_LABELS[item["production"]],
                status=STATE_LABELS[item["status"]],
                wave=item["nextWave"],
            )
        )

    lines.extend(["", "## 证据", ""])
    for item in capabilities:
        lines.extend([f"### {item['title']} (`{item['id']}`)", ""])
        for evidence in sorted(
            item["evidence"], key=lambda value: (value["layer"], value["path"], value["description"])
        ):
            evidence_href = posixpath.relpath(evidence["path"], OUTPUT_PATH.parent.as_posix())
            lines.append(
                f"- **{LAYER_LABELS[evidence['layer']]}** "
                f"[`{evidence['path']}`]({evidence_href})："
                f"{evidence['description']}"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate and render the productization capability map.")
    parser.add_argument("--check", action="store_true", help="fail if the generated Markdown is stale")
    args = parser.parse_args(argv)
    try:
        document = load_document(REPOSITORY_ROOT)
        validate_document(document, REPOSITORY_ROOT)
        rendered = render_document(document)
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
    except (CapabilityValidationError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"capability validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
