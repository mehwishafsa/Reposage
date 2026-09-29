"""The plugin itself: manifest, license and skills are wired up correctly."""

import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Skills that must exist. Add new ones here as they are built.
EXPECTED_SKILLS = {"scan"}


def read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def frontmatter(text: str) -> dict[str, str]:
    """Tiny parser for the `key: value` block between the first two `---`."""
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, "missing frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


class PluginLayoutTest(unittest.TestCase):
    def test_manifest(self):
        manifest = json.loads(read(".claude-plugin", "plugin.json"))
        self.assertEqual(manifest["name"], "reposage")
        self.assertEqual(manifest["license"], "MIT")
        self.assertIn("Mehwish Afsa", read("LICENSE"))
        from reposage import __version__
        self.assertEqual(manifest["version"], __version__)

    def test_old_commands_folder_is_gone(self):
        # Skills replaced commands/; having both would confuse users.
        self.assertFalse(os.path.exists(os.path.join(ROOT, "commands")))

    def test_skills(self):
        skills_dir = os.path.join(ROOT, "skills")
        found = {d for d in os.listdir(skills_dir)
                 if os.path.isfile(os.path.join(skills_dir, d, "SKILL.md"))}
        self.assertTrue(EXPECTED_SKILLS <= found, EXPECTED_SKILLS - found)

        for name in sorted(found):
            with self.subTest(skill=name):
                text = read("skills", name, "SKILL.md")
                meta = frontmatter(text)
                self.assertEqual(meta.get("name"), name)
                self.assertGreater(len(meta.get("description", "")), 40)
                # Every script the skill runs must exist in the plugin.
                for rel in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)", text):
                    self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)
                # ...and every sub-command it calls must be a real CLI command.
                for sub in re.findall(r"bootstrap\.py\"? (\w+)", text):
                    self.assertIn(sub, cli_commands())


def cli_commands() -> set[str]:
    from reposage.__main__ import build_arg_parser
    parser = build_arg_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    return set(sub.choices)


if __name__ == "__main__":
    unittest.main()
