"""Every committed pre-registration passes the runner's own plan check, so a plan the runner
would refuse cannot merge."""
import importlib.util
import unittest

from test_harness import REPO

spec = importlib.util.spec_from_file_location("experiment_protocol", REPO / "scripts" / "experiment_protocol.py")
PROTOCOL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PROTOCOL)


def findings(directory):
    """One line per problem the runner would refuse, for every `*.md` plan under `directory`,
    nested ones included, since the runner accepts a plan anywhere below it."""
    found = []
    for plan in sorted(directory.rglob("*.md")):
        name = plan.relative_to(directory).as_posix()
        if not PROTOCOL.DATED_NAME.match(plan.name):
            found.append("%s: the file name does not start with its date" % name)
        for field in PROTOCOL.missing_fields(plan.read_text(encoding="utf-8")):
            found.append("%s: %s is unfilled or holds a <...> placeholder" % (name, field))
    return found


class CommittedPreRegistrationTests(unittest.TestCase):
    def test_every_pre_registration_passes_the_runner_check(self):
        directory = REPO / PROTOCOL.DIRECTORY
        self.assertTrue(list(directory.rglob("*.md")), "no pre-registrations found in %s" % directory)
        problems = findings(directory)
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
