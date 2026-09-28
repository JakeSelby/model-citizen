# SPDX-License-Identifier: MIT
"""Keep skill descriptions compatible with standard YAML registry readers."""
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "primitives" / "skills"


def description_is_yaml_safe(value):
    """Validate the scalar subset used by skill descriptions without a YAML dependency."""
    if value.startswith('"'):
        try:
            return isinstance(json.loads(value), str)
        except json.JSONDecodeError:
            return False
    if value.startswith("'"):
        return len(value) >= 2 and value.endswith("'") and "'" not in value[1:-1].replace("''", "")
    return re.search(r":\s", value) is None


class SkillFrontmatterYamlTests(unittest.TestCase):
    def test_descriptions_are_valid_yaml_scalars(self):
        for path in sorted(SKILLS.glob("*/SKILL.md")):
            lines = path.read_text(encoding="utf-8").splitlines()
            header = lines[1:lines.index("---", 1)]
            description = next(line.partition(":")[2].strip() for line in header
                               if line.startswith("description:"))
            with self.subTest(skill=path.parent.name):
                self.assertTrue(description_is_yaml_safe(description),
                                "quote the description or replace the invalid YAML scalar")

    def test_unclosed_quotes_and_mapping_separators_are_rejected(self):
        for description in ('"unclosed', "'unclosed", "unsafe: mapping"):
            with self.subTest(description=description):
                self.assertFalse(description_is_yaml_safe(description))


if __name__ == "__main__":
    unittest.main()
