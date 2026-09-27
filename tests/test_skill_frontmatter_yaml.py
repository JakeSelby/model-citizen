# SPDX-License-Identifier: MIT
"""Keep skill frontmatter compatible with standard YAML registry readers."""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "primitives" / "skills"


class SkillFrontmatterYamlTests(unittest.TestCase):
    def test_plain_descriptions_do_not_contain_a_mapping_separator(self):
        for path in sorted(SKILLS.glob("*/SKILL.md")):
            lines = path.read_text(encoding="utf-8").splitlines()
            header = lines[1:lines.index("---", 1)]
            description = next(line.partition(":")[2].strip() for line in header
                               if line.startswith("description:"))
            if description[:1] in ('"', "'"):
                continue
            with self.subTest(skill=path.parent.name):
                self.assertIsNone(re.search(r":\s", description),
                                  "quote the description or replace the colon-space sequence")


if __name__ == "__main__":
    unittest.main()
