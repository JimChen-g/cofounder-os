"""Patch-bound source references; selecting a line never proves its semantics."""
from __future__ import annotations

import hashlib
from typing import Any

from .workspace import ALLOWED


def reference_table(files: dict[str, str], patch_sha: str) -> dict[str, Any]:
    refs = {}
    for prefix, path in zip(('I', 'T'), ALLOWED):
        for line, source in enumerate(files[path].splitlines(), 1):
            evidence = source.strip()
            if evidence and len(evidence) <= 180 and not evidence.startswith(('#', 'import ', 'from ')):
                refs[f'{prefix}{line}'] = {'path': path, 'line': line, 'evidence': evidence}
    return {'patch_sha': patch_sha, 'source_hashes': {
        path: hashlib.sha256(source.encode()).hexdigest() for path, source in files.items()}, 'references': refs}


def resolve_review(raw: dict[str, Any], table: dict[str, Any], files: dict[str, str]) -> dict[str, Any]:
    """Resolve only exact current references. Leave model judgments unchanged."""
    if raw.get('patch_sha') != table['patch_sha']:
        raise RuntimeError('review_patch_sha_mismatch')
    if table != reference_table(files, table['patch_sha']):
        raise RuntimeError('review_reference_table_mismatch')
    def resolve(item: dict[str, Any]) -> dict[str, Any]:
        ref = item['ref']
        if ref not in table['references']:
            raise RuntimeError('review_reference_invalid')
        return {**{k: v for k, v in item.items() if k != 'ref'}, **table['references'][ref]}
    return {**raw, 'checks': {k: resolve(v) for k, v in raw['checks'].items()},
            'findings': [resolve(v) for v in raw['findings']]}
