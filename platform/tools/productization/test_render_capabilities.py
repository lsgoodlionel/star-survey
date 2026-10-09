import copy
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RENDERER_PATH = Path(__file__).with_name("render_capabilities.py")


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
        (self.repository_root / "evidence.md").write_text("verified\n", encoding="utf-8")

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
                    "evidence": [
                        {
                            "layer": layer,
                            "path": "evidence.md",
                            "description": f"{layer} evidence",
                        }
                        for layer in (
                            "requirement",
                            "backend",
                            "frontend",
                            "flow",
                            "production",
                            "traceability",
                        )
                    ],
                    "nextWave": "Wave 0",
                }
            ],
        }

    def assert_invalid(self, document, message):
        with self.assertRaisesRegex(renderer.CapabilityValidationError, message):
            renderer.validate_document(document, self.repository_root)

    def test_rejects_duplicate_capability_ids(self):
        document = self.valid_document()
        document["capabilities"].append(copy.deepcopy(document["capabilities"][0]))

        self.assert_invalid(document, "duplicate capability id: workspace")

    def test_rejects_unknown_requirement_ids(self):
        document = self.valid_document()
        document["capabilities"][0]["requirementIds"] = ["R99-99"]

        self.assert_invalid(document, "unknown requirement id: R99-99")

    def test_rejects_missing_evidence_paths(self):
        document = self.valid_document()
        document["capabilities"][0]["evidence"][0]["path"] = "missing.md"

        self.assert_invalid(document, "evidence path does not exist: missing.md")

    def test_rejects_accepted_without_all_six_evidence_layers(self):
        document = self.valid_document()
        document["capabilities"][0]["evidence"] = document["capabilities"][0]["evidence"][:-1]

        self.assert_invalid(document, "accepted capability workspace is missing evidence layers: traceability")

    def test_rejects_accepted_with_an_incomplete_delivery_layer(self):
        document = self.valid_document()
        document["capabilities"][0]["production"] = "not-started"

        self.assert_invalid(document, "accepted capability workspace has incomplete layers: production")

    def test_rejects_items_missing_schema_required_fields(self):
        document = self.valid_document()
        del document["capabilities"][0]["nextWave"]

        self.assert_invalid(document, "capability workspace missing required field: nextWave")


class CapabilityRenderingTest(unittest.TestCase):
    def capability(self, capability_id, title):
        return {
            "id": capability_id,
            "title": title,
            "requirementIds": ["R01-02", "R01-01"],
            "backend": "complete",
            "frontend": "partial",
            "flow": "not-started",
            "production": "not-started",
            "status": "in-progress",
            "evidence": [
                {"layer": "frontend", "path": "z.md", "description": "前端证据"},
                {"layer": "backend", "path": "a.md", "description": "后端证据"},
            ],
            "nextWave": "Wave 1",
        }

    def test_rendering_is_deterministic_for_equivalent_input_order(self):
        first = {
            "schemaVersion": 1,
            "capabilities": [self.capability("z-last", "最后"), self.capability("a-first", "最先")],
        }
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
        self.assertTrue(
            {
                "工作台",
                "编辑",
                "导入",
                "预览",
                "审批与发布",
                "投放链接与二维码",
                "答卷与摘要",
                "导出",
                "模板与品牌",
                "通讯录与投放",
                "管理",
                "生产部署",
            }.issubset(titles)
        )
        self.assertNotIn("accepted", {item["status"] for item in document["capabilities"]})

    def test_schema_declares_every_required_capability_field(self):
        schema_path = REPOSITORY_ROOT / "platform/docs/productization/capabilities.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        required = set(schema["$defs"]["capability"]["required"])

        self.assertEqual(
            required,
            {
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
            },
        )

    def test_schema_allows_the_declared_schema_reference(self):
        schema_path = REPOSITORY_ROOT / "platform/docs/productization/capabilities.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertIn("$schema", schema["properties"])

    def test_repository_evidence_links_resolve_from_the_generated_document(self):
        document = renderer.load_document(REPOSITORY_ROOT)
        rendered = renderer.render_document(document)

        self.assertIn(
            "[`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`]"
            "(../../../docs/audits/2026-10-09-product-alignment/14-full-development-report.md)",
            rendered,
        )

    def test_checked_in_markdown_matches_the_renderer(self):
        document = renderer.load_document(REPOSITORY_ROOT)
        expected = renderer.render_document(document)
        output_path = REPOSITORY_ROOT / "platform/docs/productization/capability-map.md"

        self.assertEqual(expected, output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
