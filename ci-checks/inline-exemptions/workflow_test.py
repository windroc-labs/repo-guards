import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/inline-exemptions.yml").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "ci-checks/ci.yml").read_text(encoding="utf-8")


def block(text: str, key: str) -> str:
    """The lines of a top-level YAML key's block, without the key line."""
    match = re.search(rf"^{key}:\n((?:[ \t]+.*\n|\n)*)", text, re.M)
    assert match, key
    return match.group(1)


class WorkflowTest(unittest.TestCase):
    def test_s35_only_contents_read(self) -> None:
        self.assertEqual(block(WORKFLOW, "permissions").strip(), "contents: read")
        self.assertEqual(WORKFLOW.count("permissions:"), 1)

    def test_fr1_only_workflow_call_with_runs_on(self) -> None:
        on = block(WORKFLOW, "on")
        self.assertEqual(re.findall(r"^  (\S+):", on, re.M), ["workflow_call"])
        self.assertEqual(re.findall(r"^      (\S+):", on, re.M), ["runs-on"])

    def test_s1_job_name_and_template(self) -> None:
        self.assertIn("    name: Inline exemptions\n", WORKFLOW)
        self.assertIn(
            "  inline-exemptions:\n    uses: windroc-labs/repo-guards/.github/workflows/inline-exemptions.yml@main\n",
            TEMPLATE,
        )

    def test_s4_runs_on_every_event(self) -> None:
        self.assertNotIn("if:", WORKFLOW)
        self.assertNotIn("github.event", WORKFLOW)

    def test_fr5_overrides_before_the_check(self) -> None:
        steps = re.findall(r"- name: (.+)", WORKFLOW)
        self.assertLess(steps.index("Apply caller overrides"), steps.index("Inline exemptions"))
        self.assertIn("--require-hashes", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
