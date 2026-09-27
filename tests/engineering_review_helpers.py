"""Synthetic review evidence for governance tests, never real model acceptance."""
from app.engineering.workspace import ALLOWED


REVIEW_KEYS = ('input_shape', 'material_rules', 'filenames', 'output_contract', 'side_effects', 'tests')


def synthetic_review_checks(messages):
    source = messages[2].content
    checks = {}
    for key in REVIEW_KEYS:
        path = ALLOWED[1] if key == 'tests' else ALLOWED[0]
        text = source.split('FILE ' + path + '\n', 1)[1].split('END FILE\n', 1)[0]
        first = text.splitlines()[0]
        line, evidence = first.split(' | ', 1)
        checks[key] = {'path': path, 'line': int(line), 'evidence': evidence.strip(), 'satisfied': True}
    return checks
