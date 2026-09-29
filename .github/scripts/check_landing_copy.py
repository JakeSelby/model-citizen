"""Keep the landing copy current with the capabilities a pull request changes."""

import json
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pull_number import pull_number  # noqa: E402

CAPABILITY_ROOTS = ('bin/', 'lib/', 'adapters/', 'primitives/', 'policy/')
PRODUCT = 'product.json'
ESCAPE = re.compile(r'^\s*Landing copy:\s*(.+?)\s*$', re.MULTILINE)
MINIMUM_REASON = 20
MEASURED = re.compile(
    r'\b(?:cheaper|savings?|saved|faster|reduces? cost|lowers? cost|improves? (?:pass|success) rate)\b'
    r'|\b\d+(?:\.\d+)?%\b', re.IGNORECASE)
MAGNITUDE = re.compile(
    r'\b(?:at least )?(\d+(?:\.\d+)?)% cheaper\b'
    r'|\bcheaper by (?:at least )?(\d+(?:\.\d+)?)%', re.IGNORECASE)

FAILURE = (
    'A pull request that adds or changes a user-visible capability updates {product} in the same '
    'pull request. Touched: {touched}. Satisfy this either by updating {product}, or by adding a '
    'line to the pull request body that begins "Landing copy:" and says in at least {minimum} '
    'characters why the landing copy needs no change.')


def touched_capability_paths(files):
    return sorted(path for path in files if path.startswith(CAPABILITY_ROOTS))


def reason(body):
    match = ESCAPE.search(body or '')
    return match.group(1) if match else None


def _pointer_part(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def _strings(value, pointer=''):
    if isinstance(value, dict):
        for key, child in value.items():
            if key != 'evidence_cards':
                yield from _strings(child, pointer + '/' + _pointer_part(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _strings(child, pointer + '/' + str(index))
    elif isinstance(value, str):
        yield pointer, value


def _verify_bundle(path):
    module_path = Path(__file__).resolve().parents[2] / 'scripts/evidence_bundle.py'
    spec = importlib.util.spec_from_file_location('landing_evidence_bundle', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify(path)


def validate_product_claims(root=Path(__file__).resolve().parents[2]):
    """Require each mechanically recognized measured claim to bind to a freshly verified card."""
    root = Path(root).resolve()
    product = json.loads((root / PRODUCT).read_text(encoding='utf-8'))
    declarations = product.get('evidence_cards', [])
    if not isinstance(declarations, list):
        raise ValueError('product.json evidence_cards must be a list')
    by_field = {}
    for declaration in declarations:
        required = {'field', 'text', 'bundle', 'card'}
        if not isinstance(declaration, dict) or set(declaration) != required:
            raise ValueError('each product evidence card needs exactly field, text, bundle and card')
        if any(not isinstance(declaration[key], str) or not declaration[key]
               for key in required) or not declaration['field'].startswith('/'):
            raise ValueError('product evidence card fields must be nonempty strings and field a pointer')
        if declaration['field'] in by_field:
            raise ValueError('product evidence card field {} is duplicated'.format(declaration['field']))
        by_field[declaration['field']] = declaration
    measured = [(field, text) for field, text in _strings(product) if MEASURED.search(text)]
    unused = sorted(set(by_field) - {field for field, _ in measured})
    if unused:
        raise ValueError('product evidence cards name fields with no recognized measured claim: {}'.format(
            ', '.join(unused)))
    verified = {}
    for field, text in measured:
        declaration = by_field.get(field)
        if not declaration or declaration.get('text') != text:
            raise ValueError('measured claim at {} needs an evidence card bound to its exact text'.format(field))
        relative = declaration.get('bundle')
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() \
                or any(part in ('', '.', '..') for part in Path(relative).parts):
            raise ValueError('evidence bundle for {} is not a safe relative path'.format(field))
        bundle = (root / relative).resolve()
        try:
            bundle.relative_to(root)
        except ValueError as exc:
            raise ValueError('evidence bundle for {} escapes the repository'.format(field)) from exc
        if str(bundle) not in verified:
            verified[str(bundle)] = _verify_bundle(bundle)
        result = verified[str(bundle)]
        if not result.get('ok'):
            raise ValueError('evidence bundle for {} does not verify'.format(field))
        matches = [card for card in result.get('cards', [])
                   if card.get('id') == declaration.get('card')]
        if len(matches) != 1 or matches[0].get('verify_status') is not True \
                or matches[0].get('claim') != text:
            raise ValueError('measured claim at {} has no exact verified evidence card'.format(field))
        if re.search(r'\bcheaper\b', text, re.IGNORECASE):
            sm2 = result.get('derived', {}).get('sm2', {})
            if sm2.get('verdict') != 'supported' or not sm2.get('claim'):
                raise ValueError('cheaper claim at {} is not supported by SM-2'.format(field))
            magnitude = MAGNITUDE.search(text)
            interval = sm2.get('ratio_interval')
            amount = next((value for value in magnitude.groups() if value is not None), None) \
                if magnitude else None
            if magnitude and (not isinstance(interval, list) or len(interval) != 2
                              or interval[1] is None
                              or interval[1] > 1 - float(amount) / 100):
                raise ValueError('cheaper magnitude at {} exceeds the SM-2 interval'.format(field))
    return 'Landing copy claims verified: {} measured claim(s).'.format(len(measured))


def validate(files, body, root=Path(__file__).resolve().parents[2]):
    """Return the sentence to print, or raise ValueError naming both ways to satisfy the rule."""
    touched = touched_capability_paths(files)
    # Every run re-verifies the bound bundles: a change to a bundle or to the verifier can
    # invalidate a claim without touching product.json.
    claims = validate_product_claims(root)
    if not touched:
        if PRODUCT in files:
            return claims
        return 'Landing copy check does not apply: no capability-bearing path changed. ' + claims
    if PRODUCT in files:
        return 'Landing copy verified: {} changed alongside {}. {}'.format(PRODUCT, touched[0], claims)
    explained = reason(body)
    if explained is not None and len(explained) >= MINIMUM_REASON:
        return 'Landing copy waived by the pull request body: {} {}'.format(explained, claims)
    raise ValueError(FAILURE.format(product=PRODUCT, touched=', '.join(touched),
                                    minimum=MINIMUM_REASON))


def pull_request(runner=None):
    runner = runner or subprocess.run
    repository, number = os.environ['GITHUB_REPOSITORY'], pull_number()
    files = runner([
        'gh', 'api', '--paginate', '-H', 'Accept: application/vnd.github+json',
        'repos/{}/pulls/{}/files'.format(repository, number), '--jq', '.[].filename'],
        check=True, capture_output=True, text=True)
    body = runner([
        'gh', 'api', '-H', 'Accept: application/vnd.github+json',
        'repos/{}/pulls/{}'.format(repository, number), '--jq', '.body'],
        check=True, capture_output=True, text=True)
    return [line for line in files.stdout.splitlines() if line.strip()], body.stdout


def main(runner=None):
    files, body = pull_request(runner)
    print(validate(files, body))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print('Landing copy check failed: {}'.format(error), file=sys.stderr)
        sys.exit(1)
