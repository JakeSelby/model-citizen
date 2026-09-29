"""Regression coverage for the check that keeps product.json current with capability changes."""

import importlib.util
import io
import os
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'landing_copy', ROOT / '.github/scripts/check_landing_copy.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

FIXTURE_SPEC = importlib.util.spec_from_file_location(
    'evidence_bundle_fixture', ROOT / 'tests/test_evidence_bundle.py')
FIXTURE = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(FIXTURE)

REASON = 'Landing copy: internal refactor with no user-visible capability change.'


def runner(files, body):
    """Replay recorded gh output: the files listing first, then the pull request body."""
    replies = [mock.Mock(stdout=''.join(path + '\n' for path in files)), mock.Mock(stdout=body)]

    def run(argv, **kwargs):
        self_check = argv[0] == 'gh'
        if not self_check:
            raise AssertionError('the check may only call gh')
        return replies.pop(0)

    return run


class ValidateTests(unittest.TestCase):
    def test_a_capability_change_without_product_json_or_an_escape_fails(self):
        with self.assertRaises(ValueError) as caught:
            checker.validate(['bin/harness'], 'What and why\n\nCloses #1')
        message = str(caught.exception)
        self.assertIn('bin/harness', message)
        self.assertIn('product.json', message)
        self.assertIn('Landing copy:', message)

    def test_every_capability_root_fires(self):
        for path in ('bin/harness', 'lib/usage.sh', 'adapters/claude/bindings.json',
                     'primitives/rules/secrets.md', 'policy/lifecycle.json'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                checker.validate([path], '')

    def test_a_product_json_change_satisfies_the_rule(self):
        self.assertIn('product.json', checker.validate(['bin/harness', 'product.json'], ''))

    def test_a_product_only_change_still_runs_the_claim_gate(self):
        self.assertIn('0 measured claim', checker.validate(['product.json'], ''))

    def test_an_escape_line_with_a_real_reason_satisfies_the_rule(self):
        message = checker.validate(['bin/harness'], 'What and why\n\n' + REASON + '\n\nCloses #1')
        self.assertIn('internal refactor', message)

    def test_a_short_or_bare_escape_line_does_not_satisfy_the_rule(self):
        for body in ('Landing copy:', 'Landing copy: n/a', 'Landing copy:   ', 'landing copy: '
                     'this reads like the escape but the prefix is lowercase'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                checker.validate(['bin/harness'], body)

    def test_a_docs_tests_or_ci_only_change_never_fires(self):
        for files in (['docs/usage.md', 'README.md'], ['tests/test_harness.py'],
                      ['.github/workflows/ci.yml', '.github/scripts/check_landing_copy.py']):
            with self.subTest(files=files):
                self.assertIn('does not apply', checker.validate(files, ''))

    def test_a_path_that_merely_contains_a_root_name_does_not_fire(self):
        self.assertIn('does not apply', checker.validate(['docs/primitives-guide.md'], ''))

    def test_an_absent_body_is_treated_as_no_escape(self):
        self.assertIsNone(checker.reason(None))


class EvidenceClaimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'proof').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_product(self, line, cards=None):
        product = {'capabilities': [{'features': [{'line': line}]}],
                   'evidence_cards': cards or []}
        (self.root / 'product.json').write_text(json.dumps(product))

    @staticmethod
    def result(text, ok=True, verdict='supported', claim='cheaper', upper=0.8, difference=(0.05, 0.2)):
        return {'ok': ok, 'cards': [{'id': 'proof-card', 'claim': text,
                                     'verify_status': True}],
                'derived': {'sm2': {'verdict': verdict, 'claim': claim,
                                    'ratio_interval': [0.6, upper],
                                    'difference_interval': list(difference)}}}

    def check(self, text, **result):
        self.write_product(text, self.declaration(text))
        with mock.patch.object(checker, '_verify_bundle', return_value=self.result(text, **result)):
            return checker.validate_product_claims(self.root)

    def declaration(self, text):
        return [{'field': '/capabilities/0/features/0/line', 'text': text,
                 'bundle': 'proof', 'card': 'proof-card'}]

    def test_ungated_measured_claim_is_refused(self):
        text = 'The harness is cheaper.'
        self.write_product(text)
        with self.assertRaisesRegex(ValueError, 'needs an evidence card'):
            checker.validate_product_claims(self.root)

    def test_exact_card_and_fresh_bundle_verification_admit_a_supported_claim(self):
        text = 'The harness is at least 15% cheaper.'
        self.write_product(text, self.declaration(text))
        with mock.patch.object(checker, '_verify_bundle', return_value=self.result(text)) as verify:
            message = checker.validate_product_claims(self.root)
        self.assertIn('1 measured claim', message)
        verify.assert_called_once_with((self.root / 'proof').resolve())

    def test_refuted_or_tampered_evidence_is_refused(self):
        text = 'The harness is cheaper.'
        self.write_product(text, self.declaration(text))
        for result, phrase in ((self.result(text, verdict='not supported', claim=None), 'not supported'),
                               (self.result(text, ok=False), 'does not verify')):
            with self.subTest(phrase=phrase), mock.patch.object(
                    checker, '_verify_bundle', return_value=result), self.assertRaisesRegex(
                        ValueError, phrase):
                checker.validate_product_claims(self.root)

    def test_cheaper_magnitude_must_fit_the_interval(self):
        text = 'The harness is 20% cheaper.'
        self.write_product(text, self.declaration(text))
        with mock.patch.object(checker, '_verify_bundle', return_value=self.result(text, upper=0.85)), \
                self.assertRaisesRegex(ValueError, 'exceeds'):
            checker.validate_product_claims(self.root)

    def test_claims_are_reverified_when_product_json_is_unchanged(self):
        text = 'The harness is cheaper.'
        self.write_product(text, self.declaration(text))
        for files in (['docs/evidence-bundles.md'], ['bin/harness']):
            with self.subTest(files=files), mock.patch.object(
                    checker, '_verify_bundle', return_value=self.result(text, ok=False)), \
                    self.assertRaisesRegex(ValueError, 'does not verify'):
                checker.validate(files, REASON, root=self.root)

    def test_cached_status_and_changed_claim_text_are_not_trusted(self):
        text = 'The harness is cheaper.'
        declaration = self.declaration(text)
        declaration[0]['text'] = 'A different claim.'
        self.write_product(text, declaration)
        with mock.patch.object(checker, '_verify_bundle') as verify, \
                self.assertRaisesRegex(ValueError, 'exact text'):
            checker.validate_product_claims(self.root)
        verify.assert_not_called()


    def test_every_measured_form_is_recognized(self):
        for text in ('Costs 40% less.', 'A 40%.', 'Down 12.5 percent', 'Runs 2x quicker',
                     'Three × the throughput: 3×', 'Savings on every run', 'It saves money',
                     'It cuts cost', 'It halves the bill', 'Raises the pass rate',
                     'Speeds up review'):
            with self.subTest(text=text):
                self.write_product(text)
                with self.assertRaisesRegex(ValueError, 'needs an evidence card'):
                    checker.validate_product_claims(self.root)
        for text in ('Nineteen deterministic detectors', 'Exports over OTLP', 'x-ray of a 3 x 4 grid'):
            with self.subTest(text=text):
                self.write_product(text)
                self.assertIn('0 measured', checker.validate_product_claims(self.root))

    def test_every_cost_magnitude_must_fit_the_interval(self):
        for text in ('15% cheaper on average, up to 40% cheaper on long tasks.',
                     'Cheaper: 40 percent.', 'Cheaper, by 40%.'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'magnitude 40%'):
                self.check(text, upper=0.8)
        self.assertIn('1 measured', self.check('15% cheaper, 20 percent cheaper.', upper=0.8))

    def test_every_cost_phrase_needs_a_supported_sm2_result(self):
        for text in ('Savings on every run.', 'It lowers cost.', 'Less expensive.'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'not supported by SM-2'):
                self.check(text, verdict='not supported', claim=None)
        with self.assertRaisesRegex(ValueError, 'wholly below 1'):
            self.check('It lowers cost.', upper=1.02)

    def test_every_cost_reduction_wording_takes_the_sm2_path(self):
        forms = ('Costs 40% less.', 'It costs less.', '40% fewer tokens.', 'Uses fewer tokens.',
                 'Spend drops 40%.', 'The bill falls by 40 percent.', 'Token use down 40%.',
                 'A 40% reduction in spend.', 'Pays 40% lower prices.')
        for text in forms:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'not supported by SM-2'):
                self.check(text, verdict='not supported', claim=None)
        for text in ('Costs 40% less.', '40% fewer tokens.', 'Spend drops 40%.'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'magnitude 40%'):
                self.check(text, upper=0.8)
        self.assertIn('1 measured', self.check('Costs 20% less.', upper=0.8))

    def test_pass_rate_claims_need_a_positive_difference_interval(self):
        with self.assertRaisesRegex(ValueError, 'wholly above 0'):
            self.check('Improves the pass rate.', difference=(-0.1, 0.2))
        with self.assertRaisesRegex(ValueError, 'magnitude 10%'):
            self.check('Improves pass rate by 10%.', difference=(0.05, 0.2))
        self.assertIn('1 measured', self.check('Improves pass rate by 5%.', difference=(0.05, 0.2)))

    def test_speed_and_multiplier_claims_have_no_estimand_and_are_refused(self):
        for text, phrase in (('Runs faster.', 'speed claim'), ('A 2x win.', 'multiplier claim')):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, phrase):
                self.check(text)

    def test_a_descriptive_percentage_needs_only_its_verified_card(self):
        self.assertIn('1 measured', self.check('Fallback trials: 0% of attempts.'))


class EndToEndClaimTests(unittest.TestCase):
    """The claim gate against the real verifier and a real synthetic bundle, with nothing mocked."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = FIXTURE.EvidenceBundleTest('test_valid_bundle_rederives_figures_cards_and_descriptive_statistics')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def publish(self, text, change=None):
        self.fixture._set_card_claim(text)
        index = self.fixture._index()
        if change:
            change(index)
        self.fixture._save_index(index)
        shutil.copytree(str(self.fixture.root), str(self.root / 'proof'), symlinks=True)
        product = {'capabilities': [{'features': [{'line': text}]}],
                   'evidence_cards': [{'field': '/capabilities/0/features/0/line', 'text': text,
                                       'bundle': 'proof', 'card': 'ratio'}]}
        (self.root / 'product.json').write_text(json.dumps(product))

    def test_a_claim_bound_to_a_card_the_real_verifier_derives_is_admitted(self):
        self.publish('Fallback trials: 0% of attempts.')
        self.assertIn('1 measured claim', checker.validate(['product.json'], '', root=self.root))

    def test_a_stale_saved_verify_status_is_ignored(self):
        def tamper(index):
            index['evidence_cards'][0]['verify_status'] = True
            index['evidence_cards'][0]['figure']['value'] = 0.5
        self.publish('Fallback trials: 0% of attempts.', tamper)
        with self.assertRaisesRegex(ValueError, 'does not verify'):
            checker.validate_product_claims(self.root)

    def test_a_cost_claim_the_real_sm2_result_does_not_support_is_refused(self):
        self.publish('The harness is 10% cheaper.')
        with self.assertRaisesRegex(ValueError, 'not supported by SM-2'):
            checker.validate_product_claims(self.root)

class MainTests(unittest.TestCase):
    def run_main(self, files, body):
        with mock.patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo', 'PR_NUMBER': '7'}), \
                redirect_stdout(io.StringIO()) as out:
            checker.main(runner(files, body))
        return out.getvalue()

    def test_main_reads_the_changed_files_and_the_body_and_reports_the_verdict(self):
        self.assertIn('waived', self.run_main(['bin/harness'], REASON))
        self.assertIn('product.json', self.run_main(['bin/harness', 'product.json'], ''))

    def test_main_raises_when_neither_way_is_satisfied(self):
        with self.assertRaisesRegex(ValueError, 'Landing copy:'):
            self.run_main(['bin/harness'], 'Closes #1')


class WorkflowTests(unittest.TestCase):
    def test_the_workflow_runs_the_check_on_pull_requests_with_read_only_permissions(self):
        text = (ROOT / '.github/workflows/landing-copy.yml').read_text()
        self.assertIn('types: [opened, edited, reopened, synchronize, ready_for_review]', text)
        self.assertIn('\npermissions:\n  contents: read\n', text)
        self.assertNotIn('write', text)
        self.assertIn('run: python3 .github/scripts/check_landing_copy.py', text)

    def test_the_issue_ownership_check_name_is_untouched(self):
        text = (ROOT / '.github/workflows/issue-ownership.yml').read_text()
        self.assertIn('    name: issue-ownership\n', text)

    def test_the_pull_request_template_names_the_escape(self):
        self.assertIn('Landing copy:', (ROOT / '.github/PULL_REQUEST_TEMPLATE.md').read_text())

    def test_the_repository_rule_is_written_down_where_contributors_read_it(self):
        self.assertIn('landing-copy', (ROOT / 'AGENTS.md').read_text())
