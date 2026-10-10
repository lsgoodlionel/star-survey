import copy
import importlib.util
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RENDERER_PATH = Path(__file__).with_name("render_capabilities.py")
STATUS_VALUES = {"accepted", "partial", "not_started", "external"}
EVIDENCE_LAYERS = (
    "requirement",
    "backend",
    "frontend",
    "flow",
    "production",
    "traceability",
)
OBSERVED_AT = "2026-10-09T12:00:00Z"
COMMIT_SHA = "844d1516"
CAPABILITY_STATUS_ORDER = ("accepted", "partial", "not_started", "external")
CAPABILITY_SUMMARY_PATTERN = re.compile(
    r"^能力摘要（机器事实）：accepted=(\d+)，partial=(\d+)，"
    r"not_started=(\d+)，external=(\d+)。$",
    flags=re.MULTILINE,
)
COUNTED_STATUS_PATTERN = re.compile(
    r"(?<![A-Za-z_])\d+\s+(?:accepted|partial|not_started|external)(?![A-Za-z_])"
)
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")
MARKDOWN_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$", flags=re.MULTILINE)
MANUAL_CAPABILITY_TABLE_PATTERN = re.compile(
    r"^\|\s*能力\s*\|[^\n]*(?:总体状态|产品验收|accepted|partial|not_started|external)[^\n]*\|$",
    flags=re.IGNORECASE | re.MULTILINE,
)
CONTRADICTORY_COMPLETION_PATTERNS = (
    re.compile(r"(?:全部|所有)(?:\s*\d+\s*项)?能力.{0,12}(?:已|均).{0,4}(?:验收通过|完成|accepted)", re.IGNORECASE),
    re.compile(r"Wave\s*0\s*[-–—至]\s*6.{0,12}(?:全部|均|已).{0,4}(?:完成|交付)", re.IGNORECASE),
    re.compile(r"生产(?:部署|环境|验收|版本).{0,8}(?:已|均).{0,4}(?:完成|通过|就绪|production-ready)", re.IGNORECASE),
    re.compile(r"(?:GitHub\s*)?Release.{0,8}(?:已|均).{0,4}(?:完成|发布|通过)", re.IGNORECASE),
)


def load_renderer():
    spec = importlib.util.spec_from_file_location("render_capabilities", RENDERER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load renderer: {RENDERER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = load_renderer()


def require_current_capability_summary(contents, repository_root=REPOSITORY_ROOT):
    matches = CAPABILITY_SUMMARY_PATTERN.findall(contents)
    if len(matches) != 1:
        raise AssertionError("capability summary must appear exactly one time")
    document = renderer.load_document(repository_root)
    expected = {
        status: sum(item["status"] == status for item in document["capabilities"])
        for status in CAPABILITY_STATUS_ORDER
    }
    observed = dict(zip(CAPABILITY_STATUS_ORDER, (int(value) for value in matches[0])))
    if observed != expected:
        raise AssertionError(
            f"capability summary does not match machine source: expected {expected}, got {observed}"
        )
    legacy_summaries = COUNTED_STATUS_PATTERN.findall(contents)
    if legacy_summaries:
        raise AssertionError("capability summary must use the single machine-fact format")


def require_no_capability_summary(contents):
    if CAPABILITY_SUMMARY_PATTERN.search(contents) or COUNTED_STATUS_PATTERN.search(contents):
        raise AssertionError("capability summary is only allowed in roadmap and status documents")


def require_repository_links(document_path, contents, references, repository_root=REPOSITORY_ROOT):
    links = {}
    for label, target in MARKDOWN_LINK_PATTERN.findall(contents):
        normalized_label = label.strip()
        if normalized_label.startswith("`") and normalized_label.endswith("`"):
            normalized_label = normalized_label[1:-1]
        links.setdefault(normalized_label, []).append(target.strip().strip("<>"))

    repository_root = Path(repository_root).resolve()
    document_directory = (repository_root / document_path).parent
    for reference in references:
        targets = links.get(reference, [])
        if len(targets) != 1:
            raise AssertionError(f"repository link must appear exactly once: {reference}")
        resolved = (document_directory / targets[0]).resolve()
        expected = (repository_root / reference).resolve()
        if resolved != expected:
            raise AssertionError(
                f"link text/path disagree for {reference}: target resolves to {resolved}"
            )
        if not resolved.is_file():
            raise AssertionError(f"link target does not exist for {reference}: {resolved}")


def require_document_structure(document_name, contents):
    required_headings = {
        "requirements": (
            (2, "1. 权威来源与责任边界"),
            (2, "2. 冻结范围与出版条件"),
            (2, "3. 角色"),
            (2, "4. 通用交互与错误契约"),
            (2, "5. 用户旅程需求"),
            (2, "6. 状态维护规则"),
        ),
        "blueprint": (
            (2, "1. 权威来源与责任边界"),
            (2, "2. 冻结范围与出版条件"),
            (2, "3. 信息架构"),
            (3, "3.1 一级工作区"),
            (3, "3.2 一级导航规则"),
            (2, "4. 问卷内工作流"),
            (2, "6. 引擎边界"),
            (2, "8. Wave 0-6 在蓝图中的落点"),
        ),
        "roadmap": (
            (2, "1. 权威来源与责任边界"),
            (2, "2. 冻结范围与出版条件"),
            (2, "3. 全局门禁"),
            (3, "3.1 全局准入条件"),
            (3, "3.2 全局准出条件"),
            (2, "4. Wave 0-6"),
            (2, "5. 依赖与发布顺序"),
            (2, "6. 延后项"),
        ),
    }
    headings = {
        (len(prefix), title)
        for prefix, title in MARKDOWN_HEADING_PATTERN.findall(contents)
    }
    for heading in required_headings[document_name]:
        if heading not in headings:
            raise AssertionError(f"missing required heading: {heading[1]}")

    if document_name == "requirements":
        journey_matches = list(re.finditer(r"^###\s+5\.\d+\s+.+$", contents, flags=re.MULTILINE))
        if not journey_matches:
            raise AssertionError("requirements document has no journey headings")
        for index, match in enumerate(journey_matches):
            end = journey_matches[index + 1].start() if index + 1 < len(journey_matches) else contents.find("\n## ", match.end())
            body = contents[match.end():end if end != -1 else len(contents)]
            for marker in ("正常流：", "失败流：", "验收断言："):
                if marker not in body:
                    raise AssertionError(f"journey heading lacks {marker}: {match.group(0)}")

    if document_name == "roadmap":
        for wave in range(7):
            if not any(level == 3 and title.startswith(f"Wave {wave}：") for level, title in headings):
                raise AssertionError(f"missing required heading: Wave {wave}")


def require_no_manual_capability_table(contents):
    if MANUAL_CAPABILITY_TABLE_PATTERN.search(contents):
        raise AssertionError("manual capability table is not allowed outside capability-map.md")


def require_no_contradictory_completion_claims(contents):
    for pattern in CONTRADICTORY_COMPLETION_PATTERNS:
        if pattern.search(contents):
            raise AssertionError(f"contradictory completion declaration: {pattern.pattern}")


class CapabilityValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository_root = Path(self.temp_dir.name)
        (self.repository_root / "platform/docs/traceability").mkdir(parents=True)
        (self.repository_root / "platform/docs/productization").mkdir(parents=True)
        shutil.copyfile(
            REPOSITORY_ROOT / "platform/docs/productization/capabilities.schema.json",
            self.repository_root / "platform/docs/productization/capabilities.schema.json",
        )
        (self.repository_root / "platform/docs/traceability/requirement-index.md").write_text(
            "| R01-01 | WP-01 | 空白与应用类型创建 |\n",
            encoding="utf-8",
        )
        evidence_contents = {
            "platform/docs/traceability/requirements.md": "R01-01\nacceptance: creates survey\n",
            "platform/services/business/src/test/WorkspaceTest.java": "testCreatesWorkspace\n",
            "platform/apps/admin-web/src/Workspace.test.tsx": "creates workspace\n",
            "platform/apps/admin-web/e2e/workspace.spec.ts": "browser journey\nartifact: workspace.png\n",
            "platform/deploy/production/evidence/workspace-run.txt": "production probe passed\n",
            "platform/docs/traceability/workspace-check.txt": "traceability check passed\nartifact: release.json\n",
        }
        for relative_path, contents in evidence_contents.items():
            path = self.repository_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")

    def accepted_evidence(self):
        return [
            {
                "layer": "requirement",
                "kind": "requirement-spec",
                "path": "platform/docs/traceability/requirements.md",
                "locator": "R01-01",
                "description": "requirement evidence",
                "verification": {
                    "requirementId": "R01-01",
                    "acceptanceLocator": "acceptance: creates survey",
                },
            },
            {
                "layer": "backend",
                "kind": "automated-test",
                "path": "platform/services/business/src/test/WorkspaceTest.java",
                "locator": "testCreatesWorkspace",
                "description": "backend test evidence",
                "verification": {
                    "command": "./gradlew test --tests WorkspaceTest.testCreatesWorkspace",
                    "result": "passed",
                    "observedAt": OBSERVED_AT,
                    "commit": COMMIT_SHA,
                },
            },
            {
                "layer": "frontend",
                "kind": "automated-test",
                "path": "platform/apps/admin-web/src/Workspace.test.tsx",
                "locator": "creates workspace",
                "description": "frontend test evidence",
                "verification": {
                    "command": "npm test -- Workspace.test.tsx",
                    "result": "passed",
                    "observedAt": OBSERVED_AT,
                    "commit": COMMIT_SHA,
                },
            },
            {
                "layer": "flow",
                "kind": "browser-test",
                "path": "platform/apps/admin-web/e2e/workspace.spec.ts",
                "locator": "browser journey",
                "description": "browser flow evidence",
                "verification": {
                    "command": "npx playwright test workspace.spec.ts",
                    "result": "passed",
                    "observedAt": OBSERVED_AT,
                    "commit": COMMIT_SHA,
                    "browserArtifactLocator": "artifact: workspace.png",
                },
            },
            {
                "layer": "production",
                "kind": "production-run",
                "path": "platform/deploy/production/evidence/workspace-run.txt",
                "locator": "production probe passed",
                "description": "production run evidence",
                "verification": {
                    "environment": "production",
                    "os": "linux",
                    "arch": "amd64",
                    "command": "docker compose exec api ./healthcheck",
                    "result": "passed",
                    "observedAt": OBSERVED_AT,
                    "commit": COMMIT_SHA,
                },
            },
            {
                "layer": "traceability",
                "kind": "traceability-check",
                "path": "platform/docs/traceability/workspace-check.txt",
                "locator": "traceability check passed",
                "description": "traceability check evidence",
                "verification": {
                    "command": "python3 -m reqtrace.cli check",
                    "result": "passed",
                    "observedAt": OBSERVED_AT,
                    "commit": COMMIT_SHA,
                    "artifactLocator": "artifact: release.json",
                },
            },
        ]

    def valid_document(self):
        return {
            "schemaVersion": 1,
            "capabilities": [
                {
                    "id": "workspace",
                    "title": "工作台",
                    "requirementIds": ["R01-01"],
                    "backend": "complete",
                    "frontend": "complete",
                    "flow": "complete",
                    "production": "complete",
                    "status": "accepted",
                    "evidence": self.accepted_evidence(),
                    "nextWave": "Wave 0",
                }
            ],
        }

    def weak_proof_document(self):
        document = self.valid_document()
        document["capabilities"][0]["evidence"] = []
        for layer in EVIDENCE_LAYERS:
            path = self.repository_root / f"{layer}.txt"
            path.write_text(f"proof:{layer}:R01-01\n", encoding="utf-8")
            document["capabilities"][0]["evidence"].append(
                {
                    "layer": layer,
                    "path": f"{layer}.txt",
                    "locator": f"proof:{layer}:R01-01",
                    "description": f"{layer} evidence",
                }
            )
        return document

    def schema(self):
        path = self.repository_root / "platform/docs/productization/capabilities.schema.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def write_schema(self, schema):
        path = self.repository_root / "platform/docs/productization/capabilities.schema.json"
        path.write_text(json.dumps(schema), encoding="utf-8")

    def assert_invalid(self, document, message):
        with self.assertRaisesRegex(renderer.CapabilityValidationError, message):
            renderer.validate_document(document, self.repository_root)

    def test_rejects_duplicate_capability_ids(self):
        document = self.valid_document()
        document["capabilities"].append(copy.deepcopy(document["capabilities"][0]))
        self.assert_invalid(document, "duplicate capability id: workspace")

    def test_rejects_unknown_requirement_ids(self):
        document = self.valid_document()
        unknown_requirement = "R" + "99-99"
        document["capabilities"][0]["requirementIds"] = [unknown_requirement]
        self.assert_invalid(document, f"unknown requirement id: {unknown_requirement}")

    def test_rejects_missing_evidence_paths(self):
        document = self.valid_document()
        document["capabilities"][0]["evidence"][0]["path"] = "missing.md"
        self.assert_invalid(document, "evidence path does not exist: missing.md")

    def test_rejects_an_evidence_locator_absent_from_the_referenced_text(self):
        document = self.valid_document()
        document["capabilities"][0]["evidence"][0]["locator"] = "not in the file"
        self.assert_invalid(document, "evidence locator not found")

    def test_rejects_accepted_without_all_six_evidence_layers(self):
        document = self.valid_document()
        document["capabilities"][0]["evidence"] = document["capabilities"][0]["evidence"][:-1]
        self.assert_invalid(document, "accepted capability workspace is missing evidence layers: traceability")

    def test_rejects_old_temporary_proof_fixture(self):
        self.assert_invalid(self.weak_proof_document(), "missing required field: kind")

    def test_accepts_layer_typed_evidence_with_structured_verification(self):
        renderer.validate_document(self.valid_document(), self.repository_root)

    def test_accepted_capability_may_keep_non_qualifying_supporting_evidence(self):
        path = self.repository_root / "platform/services/business/src/main/Workspace.java"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("class Workspace\n", encoding="utf-8")
        document = self.valid_document()
        document["capabilities"][0]["evidence"].append(
            {
                "layer": "backend",
                "kind": "implementation",
                "path": "platform/services/business/src/main/Workspace.java",
                "locator": "class Workspace",
                "description": "supporting implementation evidence",
            }
        )
        renderer.validate_document(document, self.repository_root)

    def test_rejects_kind_incompatible_with_layer(self):
        document = self.valid_document()
        document["capabilities"][0]["evidence"][1]["kind"] = "browser-test"
        self.assert_invalid(document, "kind.*must equal automated-test")

    def test_rejects_accepted_test_evidence_without_command_result_or_commit(self):
        for missing_field in ("command", "result", "commit"):
            with self.subTest(missing_field=missing_field):
                document = self.valid_document()
                del document["capabilities"][0]["evidence"][1]["verification"][missing_field]
                self.assert_invalid(document, f"missing required field: {missing_field}")

    def test_rejects_production_run_without_os_or_arch(self):
        for missing_field in ("os", "arch"):
            with self.subTest(missing_field=missing_field):
                document = self.valid_document()
                del document["capabilities"][0]["evidence"][4]["verification"][missing_field]
                self.assert_invalid(document, f"missing required field: {missing_field}")

    def test_rejects_browser_test_without_artifact_locator(self):
        document = self.valid_document()
        del document["capabilities"][0]["evidence"][3]["verification"]["browserArtifactLocator"]
        self.assert_invalid(document, "missing required field: browserArtifactLocator")

    def test_rejects_traceability_check_without_artifact_locator(self):
        document = self.valid_document()
        del document["capabilities"][0]["evidence"][5]["verification"]["artifactLocator"]
        self.assert_invalid(document, "missing required field: artifactLocator")

    def test_rejects_invalid_commit_timestamp_or_non_passing_result(self):
        invalid_values = {
            "commit": "not-a-sha",
            "observedAt": "October 9",
            "result": "failed",
        }
        for field, value in invalid_values.items():
            with self.subTest(field=field):
                document = self.valid_document()
                document["capabilities"][0]["evidence"][1]["verification"][field] = value
                self.assert_invalid(document, f"{field}.*(does not match|must equal passed)")

    def test_rejects_missing_structured_locator_from_referenced_text(self):
        document = self.valid_document()
        document["capabilities"][0]["evidence"][3]["verification"]["browserArtifactLocator"] = "artifact: missing.png"
        self.assert_invalid(document, "browserArtifactLocator not found")

    def test_rejects_accepted_evidence_outside_layer_path_conventions(self):
        path = self.repository_root / "arbitrary.txt"
        path.write_text("testCreatesWorkspace\n", encoding="utf-8")
        document = self.valid_document()
        document["capabilities"][0]["evidence"][1]["path"] = "arbitrary.txt"
        self.assert_invalid(document, "backend evidence path does not match accepted conventions")

    def test_rejects_accepted_when_one_generic_identity_is_repeated_for_six_layers(self):
        generic_path = self.repository_root / "generic.txt"
        generic_path.write_text("verified\n", encoding="utf-8")
        document = self.valid_document()
        document["capabilities"][0]["evidence"] = [
            {
                "layer": layer,
                "kind": self.accepted_evidence()[index]["kind"],
                "path": "generic.txt",
                "locator": "verified",
                "description": f"{layer} evidence",
                "verification": self.accepted_evidence()[index]["verification"],
            }
            for index, layer in enumerate(EVIDENCE_LAYERS)
        ]
        self.assert_invalid(document, "accepted capability workspace requires six distinct evidence identities")

    def test_status_enum_is_driven_by_the_schema(self):
        document = self.valid_document()
        document["capabilities"][0]["status"] = "partial"
        renderer.validate_document(document, self.repository_root)
        schema = self.schema()
        schema["$defs"]["capability"]["properties"]["status"]["enum"].remove("partial")
        self.write_schema(schema)
        self.assert_invalid(document, "status.*not one of")

    def test_object_closure_is_driven_by_the_schema(self):
        document = self.valid_document()
        document["capabilities"][0]["reviewNote"] = "schema decides whether this is allowed"
        self.assert_invalid(document, "additional property: reviewNote")
        schema = self.schema()
        schema["$defs"]["capability"]["additionalProperties"] = True
        self.write_schema(schema)
        renderer.validate_document(document, self.repository_root)

    def test_accepted_delivery_conditions_are_driven_by_the_schema(self):
        document = self.valid_document()
        document["capabilities"][0]["production"] = "partial"
        self.assert_invalid(document, "production.*must equal complete")
        schema = self.schema()
        accepted_then = schema["$defs"]["capability"]["allOf"][0]["then"]
        accepted_then["properties"]["production"]["const"] = "partial"
        self.write_schema(schema)
        renderer.validate_document(document, self.repository_root)

    def test_accepted_evidence_kind_is_driven_by_the_schema(self):
        document = self.valid_document()
        schema = self.schema()
        accepted_then = schema["$defs"]["capability"]["allOf"][0]["then"]
        evidence_rules = accepted_then["properties"]["evidence"]["allOf"]
        backend_rule = next(
            rule["contains"]
            for rule in evidence_rules
            if rule["contains"]["properties"]["layer"].get("const") == "backend"
        )
        backend_rule["properties"]["kind"]["const"] = "implementation"
        self.write_schema(schema)
        self.assert_invalid(document, "backend evidence kind must equal implementation")


class CapabilityRenderingTest(unittest.TestCase):
    def capability(self, capability_id, title):
        return {
            "id": capability_id,
            "title": title,
            "requirementIds": ["R01-02", "R01-01"],
            "backend": "complete",
            "frontend": "partial",
            "flow": "not_started",
            "production": "not_started",
            "status": "partial",
            "evidence": [
                {"layer": "frontend", "kind": "implementation", "path": "z.md", "locator": "frontend locator", "description": "前端证据"},
                {"layer": "backend", "kind": "implementation", "path": "a.md", "locator": "backend locator", "description": "后端证据"},
            ],
            "nextWave": "Wave 1",
        }

    def test_rendering_is_deterministic_for_equivalent_input_order(self):
        first = {"schemaVersion": 1, "capabilities": [self.capability("z-last", "最后"), self.capability("a-first", "最先")]}
        second = copy.deepcopy(first)
        second["capabilities"].reverse()
        for capability in second["capabilities"]:
            capability["requirementIds"].reverse()
            capability["evidence"].reverse()
        self.assertEqual(renderer.render_document(first), renderer.render_document(second))

    def test_repository_source_validates_and_covers_the_required_initial_scope(self):
        document = renderer.load_document(REPOSITORY_ROOT)
        renderer.validate_document(document, REPOSITORY_ROOT)
        titles = {capability["title"] for capability in document["capabilities"]}
        self.assertTrue({"工作台", "编辑", "导入", "预览", "审批与发布", "投放链接与二维码", "答卷与摘要", "导出", "模板与品牌", "通讯录与投放", "管理", "生产部署"}.issubset(titles))
        self.assertEqual({item["status"] for item in document["capabilities"]} - STATUS_VALUES, set())
        self.assertNotIn("accepted", {item["status"] for item in document["capabilities"]})

    def test_schema_declares_the_only_allowed_capability_statuses(self):
        schema_path = REPOSITORY_ROOT / "platform/docs/productization/capabilities.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["$defs"]["capability"]["properties"]["status"]["enum"]), STATUS_VALUES)

    def test_schema_requires_structured_evidence_locators(self):
        schema_path = REPOSITORY_ROOT / "platform/docs/productization/capabilities.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["$defs"]["evidence"]["required"]), {"layer", "kind", "path", "locator", "description"})

    def test_repository_evidence_links_resolve_from_the_generated_document(self):
        document = renderer.load_document(REPOSITORY_ROOT)
        rendered = renderer.render_document(document)
        self.assertIn("[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`](../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md)", rendered)

    def test_checked_in_markdown_matches_the_renderer(self):
        document = renderer.load_document(REPOSITORY_ROOT)
        expected = renderer.render_document(document)
        output_path = REPOSITORY_ROOT / "platform/docs/productization/capability-map.md"
        self.assertEqual(expected, output_path.read_text(encoding="utf-8"))


class ProductizationDocumentationTest(unittest.TestCase):
    CANONICAL_DOCUMENTS = {
        "requirements": Path("platform/docs/productization/frontend-backend-requirements.md"),
        "blueprint": Path("platform/docs/productization/product-blueprint.md"),
        "roadmap": Path("platform/docs/productization/release-roadmap.md"),
    }
    AUTHORITY_REFERENCES = (
        "docs/superpowers/specs/2026-10-09-platform-productization-production-upgrade-design.md",
        "docs/superpowers/plans/2026-10-09-platform-productization-production-upgrade.md",
        "platform/docs/productization/capability-map.md",
        "docs/audits/2026-10-09-product-alignment/14-full-development-report.md",
    )
    AUDIT_BASELINE_PATTERN = r"13[^\n]*86[^\n]*191[^\n]*15"

    def read_canonical_documents(self):
        contents = {}
        for name, relative_path in self.CANONICAL_DOCUMENTS.items():
            path = REPOSITORY_ROOT / relative_path
            self.assertTrue(path.is_file(), f"missing canonical document: {relative_path}")
            contents[name] = path.read_text(encoding="utf-8")
        return contents

    def test_canonical_documents_share_the_authority_chain_and_audit_baseline(self):
        for name, contents in self.read_canonical_documents().items():
            with self.subTest(document=name):
                require_repository_links(
                    self.CANONICAL_DOCUMENTS[name],
                    contents,
                    self.AUTHORITY_REFERENCES,
                )
                self.assertRegex(contents, self.AUDIT_BASELINE_PATTERN)
                require_no_manual_capability_table(contents)
                require_no_contradictory_completion_claims(contents)
                if name == "roadmap":
                    require_current_capability_summary(contents)
                else:
                    require_no_capability_summary(contents)

    def test_canonical_documents_cover_their_distinct_contracts(self):
        documents = self.read_canonical_documents()
        for name, contents in documents.items():
            with self.subTest(document=name):
                require_document_structure(name, contents)

    def test_release_roadmap_covers_every_wave_without_publishing_unaccepted_work(self):
        roadmap = self.read_canonical_documents()["roadmap"]
        for wave in range(7):
            self.assertIn(f"Wave {wave}", roadmap)
        require_current_capability_summary(roadmap)
        self.assertIn("六层证据", roadmap)

    def test_repository_status_documents_link_the_canonical_baseline(self):
        status_documents = (
            Path("platform/README.md"),
            Path("platform/docs/p2/progress.md"),
            Path("platform/docs/traceability/requirement-tests.md"),
        )
        canonical_paths = tuple(str(path) for path in self.CANONICAL_DOCUMENTS.values()) + (
            "platform/docs/productization/capability-map.md",
        )
        for relative_path in status_documents:
            with self.subTest(document=str(relative_path)):
                contents = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
                require_repository_links(relative_path, contents, canonical_paths)
                self.assertRegex(contents, r"13[^\n]*86[^\n]*191[^\n]*15")
                require_current_capability_summary(contents)
                require_no_manual_capability_table(contents)
                require_no_contradictory_completion_claims(contents)

    def test_stale_capability_summary_is_rejected_when_the_machine_source_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository_root = Path(temp_dir)
            source_path = repository_root / "platform/docs/productization/capabilities.json"
            source_path.parent.mkdir(parents=True)
            document = renderer.load_document(REPOSITORY_ROOT)
            changed = next(item for item in document["capabilities"] if item["status"] == "partial")
            changed["status"] = "external"
            source_path.write_text(json.dumps(document), encoding="utf-8")
            roadmap = self.read_canonical_documents()["roadmap"]

            with self.assertRaisesRegex(AssertionError, "capability summary"):
                require_current_capability_summary(roadmap, repository_root)

    def test_capability_summary_must_appear_exactly_once(self):
        roadmap = self.read_canonical_documents()["roadmap"]
        summary = next(
            line for line in roadmap.splitlines()
            if line.startswith("能力摘要（机器事实）：")
        )
        duplicated = roadmap + f"\n{summary}\n"

        with self.assertRaisesRegex(AssertionError, "exactly one"):
            require_current_capability_summary(duplicated)

    def test_repository_links_reject_missing_or_disagreeing_targets(self):
        reference = "docs/superpowers/specs/design.md"
        document_path = Path("platform/docs/productization/example.md")
        with tempfile.TemporaryDirectory() as temp_dir:
            repository_root = Path(temp_dir)
            (repository_root / reference).parent.mkdir(parents=True)
            missing_source = f"[`{reference}`](../../../docs/superpowers/specs/design.md)"
            with self.assertRaisesRegex(AssertionError, "does not exist"):
                require_repository_links(
                    document_path,
                    missing_source,
                    (reference,),
                    repository_root,
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            repository_root = Path(temp_dir)
            expected = repository_root / reference
            expected.parent.mkdir(parents=True)
            expected.write_text("design", encoding="utf-8")
            other = repository_root / "docs/superpowers/specs/other.md"
            other.write_text("other", encoding="utf-8")
            disagreeing_source = f"[`{reference}`](../../../docs/superpowers/specs/other.md)"
            with self.assertRaisesRegex(AssertionError, "link text/path disagree"):
                require_repository_links(
                    document_path,
                    disagreeing_source,
                    (reference,),
                    repository_root,
                )

    def test_structural_contract_rejects_a_required_heading_changed_to_plain_text(self):
        blueprint = self.read_canonical_documents()["blueprint"]
        mutated = blueprint.replace("## 3. 信息架构", "**信息架构**", 1)

        with self.assertRaisesRegex(AssertionError, "heading"):
            require_document_structure("blueprint", mutated)

    def test_manual_capability_state_table_is_rejected(self):
        source = self.read_canonical_documents()["requirements"] + """

| 能力 | 总体状态 |
|---|---|
| 工作台 | accepted |
"""
        with self.assertRaisesRegex(AssertionError, "manual capability table"):
            require_no_manual_capability_table(source)

    def test_contradictory_completion_declarations_are_rejected(self):
        declarations = (
            "全部能力已验收通过。",
            "Wave 0-6 全部完成。",
            "生产验收已通过。",
            "Release 已发布。",
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                with self.assertRaisesRegex(AssertionError, "completion declaration"):
                    require_no_contradictory_completion_claims(declaration)


if __name__ == "__main__":
    unittest.main()
