"""Literal diagnostics must explain mistakes without making review claims valid."""
import pytest

from app.engineering.service import ALLOWED, Review, review_citation_diagnostics, validate_review_evidence


def test_quote_style_and_line_errors_are_reported_without_rewriting():
    files = {ALLOWED[0]: 'def check():\n    if "/" in fname:\n        raise ValueError()\n', ALLOWED[1]: 'def test_check():\n    assert True\n'}
    checks = {name: {'path': ALLOWED[0], 'line': 2, 'evidence': 'if "/" in fname:', 'satisfied': True}
              for name in ['input_shape', 'material_rules', 'filenames', 'output_contract', 'side_effects']}
    checks['filenames']['evidence'] = "if '/' in fname:"
    checks['side_effects']['line'] = 3
    checks['tests'] = {'path': ALLOWED[1], 'line': 2, 'evidence': 'assert True', 'satisfied': True}
    review = Review.model_validate({'checks': checks, 'patch_sha': 'immutable', 'findings': [], 'conclusion': 'passed'})
    before = review.model_dump()
    diagnostics = review_citation_diagnostics(review, files)
    assert diagnostics == [
        {'check': 'filenames', 'path': ALLOWED[0], 'claimed_line': 2, 'quoted': "if '/' in fname:", 'source_at_claimed_line': 'if "/" in fname:', 'source_line_truncated': False, 'exact_quote_lines': []},
        {'check': 'side_effects', 'path': ALLOWED[0], 'claimed_line': 3, 'quoted': 'if "/" in fname:', 'source_at_claimed_line': 'raise ValueError()', 'source_line_truncated': False, 'exact_quote_lines': [2]},
    ]
    assert review.model_dump() == before
    with pytest.raises(RuntimeError, match='review_evidence_not_in_patch'):
        validate_review_evidence(review, files)


def test_out_of_bounds_and_long_source_diagnostics_are_bounded():
    files = {ALLOWED[0]: 'x = "' + 'a' * 400 + '"\n', ALLOWED[1]: 'import pytest\n'}
    check = {'path': ALLOWED[0], 'line': 1, 'evidence': 'missing', 'satisfied': True}
    checks = {name: dict(check) for name in ['input_shape', 'material_rules', 'filenames', 'output_contract', 'side_effects', 'tests']}
    checks['tests'] = {'path': ALLOWED[1], 'line': 999, 'evidence': 'import pytest', 'satisfied': True}
    review = Review.model_validate({'checks': checks, 'patch_sha': 'immutable', 'findings': [], 'conclusion': 'passed'})
    diagnostics = review_citation_diagnostics(review, files)
    assert len(diagnostics) == 6
    assert len(diagnostics[0]['source_at_claimed_line']) == 180
    assert diagnostics[0]['source_line_truncated']
    assert diagnostics[-1]['source_at_claimed_line'] is None
    assert diagnostics[-1]['exact_quote_lines'] == [1]
