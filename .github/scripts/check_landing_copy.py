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
# A percentage is a number followed by `%` or `percent`; `\b` cannot follow `%`, so the end is
# a lookahead instead. A multiplier such as `2x` or `3×` is a measured magnitude too.
PERCENT = re.compile(r'(\d+(?:\.\d+)?)\s*(?:%|percent\b)', re.IGNORECASE)
MULTIPLIER = r'\b\d+(?:\.\d+)?(?:x|\s*×)(?![\w])'
COST = re.compile(
    r'\b(?:cheaper|less expensive|savings?|saved|saves|(?:reduces?|lowers?|cuts?|halves)'
    r' (?:the )?(?:cost|spend|bill)s?|costs? less|fewer tokens'
    r'|(?:cost|spend|bill)s? (?:drops?|falls?))\b', re.IGNORECASE)
# A percentage beside a cost noun or a reduction word is a cost claim whatever its phrasing,
# so "spend drops 40%" and "40% fewer tokens" take the SM-2 path rather than the descriptive one.
COST_CONTEXT = re.compile(
    r'\b(?:costs?|costing|spend(?:s|ing)?|spent|bills?|billing|prices?|pricing|tokens?|dollars?'
    r'|money|budgets?|expens\w*|less|fewer|lower\w*|drops?|dropped|falls?|fell|down'
    r'|reduc\w*|cuts?|halve[sd]?)\b', re.IGNORECASE)
PASS_RATE = re.compile(
    r'\b(?:improves?|raises?|increases?|boosts?|higher) (?:the )?(?:pass|success) rates?\b',
    re.IGNORECASE)
SPEED = re.compile(r'\b(?:faster|quicker|speeds? up|less time)\b', re.IGNORECASE)
MEASURED = re.compile('|'.join((COST.pattern, PASS_RATE.pattern, SPEED.pattern,
                                PERCENT.pattern, MULTIPLIER)), re.IGNORECASE)

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


def _is_cost_claim(text):
    return bool(COST.search(text) or (PERCENT.search(text) and COST_CONTEXT.search(text)))


def _check_direction(field, text, derived):
    """Refuse a directional claim the paired SM-2 result does not support, at every magnitude."""
    sm2 = derived.get('sm2') if isinstance(derived, dict) else None
    sm2 = sm2 if isinstance(sm2, dict) else {}
    amounts = [float(match.group(1)) for match in PERCENT.finditer(text)]
    if SPEED.search(text):
        raise ValueError('speed claim at {} has no re-derived time estimand to support it'.format(field))
    if re.search(MULTIPLIER, text, re.IGNORECASE):
        raise ValueError('multiplier claim at {} has no re-derived estimand to support it'.format(field))
    if _is_cost_claim(text):
        if sm2.get('verdict') != 'supported' or not sm2.get('claim'):
            raise ValueError('cost claim at {} is not supported by SM-2'.format(field))
        interval = sm2.get('ratio_interval')
        upper = interval[1] if isinstance(interval, list) and len(interval) == 2 else None
        if not isinstance(upper, (int, float)) or upper >= 1:
            raise ValueError('cost claim at {} has no ratio interval wholly below 1'.format(field))
        for amount in amounts:
            if upper > 1 - amount / 100:
                raise ValueError('cost magnitude {}% at {} exceeds the SM-2 interval'.format(
                    _format(amount), field))
    if PASS_RATE.search(text):
        interval = sm2.get('difference_interval')
        lower = interval[0] if isinstance(interval, list) and len(interval) == 2 else None
        if not isinstance(lower, (int, float)) or lower <= 0:
            raise ValueError('pass-rate claim at {} has no difference interval wholly above 0'.format(field))
        for amount in amounts:
            if lower < amount / 100:
                raise ValueError('pass-rate magnitude {}% at {} exceeds the SM-2 interval'.format(
                    _format(amount), field))


def _format(amount):
    return ('%f' % amount).rstrip('0').rstrip('.')


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
        _check_direction(field, text, result.get('derived', {}))
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
