import copy
import importlib.util
import json
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


def load_renderer():
    spec = importlib.util.spec_from_file_location("render_capabilities", RENDERER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load renderer: {RENDERER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = load_renderer()


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
                for reference in self.AUTHORITY_REFERENCES:
                    self.assertIn(reference, contents)
                self.assertRegex(contents, self.AUDIT_BASELINE_PATTERN)
                self.assertIn("冻结范围", contents)
                self.assertIn("出版条件", contents)
                self.assertIn("责任边界", contents)

    def test_canonical_documents_cover_their_distinct_contracts(self):
        documents = self.read_canonical_documents()
        for marker in ("角色", "正常流", "失败流", "验收断言"):
            self.assertIn(marker, documents["requirements"])
        for marker in ("信息架构", "一级导航", "问卷内工作流", "引擎边界"):
            self.assertIn(marker, documents["blueprint"])
        for marker in ("依赖", "准入条件", "准出条件", "延后项"):
            self.assertIn(marker, documents["roadmap"])

    def test_release_roadmap_covers_every_wave_without_publishing_unaccepted_work(self):
        roadmap = self.read_canonical_documents()["roadmap"]
        for wave in range(7):
            self.assertIn(f"Wave {wave}", roadmap)
        self.assertIn("11 partial", roadmap)
        self.assertIn("1 not_started", roadmap)
        self.assertIn("0 accepted", roadmap)
        self.assertIn("六层证据", roadmap)

    def test_repository_status_documents_link_the_canonical_baseline(self):
        status_documents = (
            Path("platform/README.md"),
            Path("platform/docs/p2/progress.md"),
            Path("platform/docs/traceability/requirement-tests.md"),
        )
        canonical_paths = tuple(str(path) for path in self.CANONICAL_DOCUMENTS.values())
        for relative_path in status_documents:
            with self.subTest(document=str(relative_path)):
                contents = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
                for canonical_path in canonical_paths:
                    self.assertIn(canonical_path, contents)
                self.assertRegex(contents, r"13[^\n]*86[^\n]*191[^\n]*15")
                self.assertIn("11 partial", contents)
                self.assertIn("1 not_started", contents)
                self.assertIn("0 accepted", contents)


if __name__ == "__main__":
    unittest.main()
