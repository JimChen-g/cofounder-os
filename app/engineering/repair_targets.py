"""Narrow runtime repair targets; candidate text is parsed, never executed."""
from __future__ import annotations

import ast
import hashlib
import io
import tokenize
import re
import textwrap
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .workspace import ALLOWED


class StatementEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_id: str = Field(pattern=r'^target-[123]$')
    new: str = Field(min_length=1, max_length=4000)


class StatementRepair(BaseModel):
    model_config = ConfigDict(extra='forbid')
    edits: list[StatementEdit] = Field(min_length=1, max_length=3)


@dataclass(frozen=True)
class StatementTarget:
    target_id: str
    path: str
    start: int
    end: int
    line: int
    source_sha256: str
    old: str
    exception: str

    def context(self) -> dict[str, Any]:
        return asdict(self)


def digest(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def statement_targets(files: dict[str, str], failures: list[dict[str, str]]) -> list[StatementTarget]:
    """Use only traceback positions in top-level test setup assignments before raises."""
    if not failures or len(failures) > 3:
        return []
    path = ALLOWED[1]
    source = files[path]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines(keepends=True)
    targets: list[StatementTarget] = []
    seen = set()
    for failure in failures:
        name = failure['test'].split('[', 1)[0]
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
        if len(functions) != 1:
            return []
        positions = [int(n) for n in re.findall(re.escape(path) + r':(\d+):', failure['traceback'])]
        candidates = []
        for node in functions[0].body:
            if isinstance(node, (ast.With, ast.AsyncWith)):
                break
            if isinstance(node, ast.Assign) and any(node.lineno <= n <= (node.end_lineno or node.lineno) for n in positions):
                candidates.append(node)
        if len(candidates) != 1:
            return []
        node = candidates[0]
        if node.lineno in seen:
            return []
        seen.add(node.lineno)
        start = sum(map(len, lines[:node.lineno - 1]))
        end = sum(map(len, lines[:node.end_lineno]))
        while end > start and source[end - 1] in '\r\n':
            end -= 1
        old = source[start:end]
        if len(ast.parse(textwrap.dedent(old)).body) != 1:
            return []
        targets.append(StatementTarget('target-' + str(len(targets) + 1), path, start, end,
                                       node.lineno, digest(source), old, failure['exceptions']))
    return targets


def independent_edits(files: dict[str, str], edits: list[dict[str, str]]) -> dict[str, str]:
    """Resolve every anchor against immutable input, reject chains and overlaps."""
    positions: dict[str, list[tuple[int, int, str]]] = {}
    for edit in edits:
        path, old = edit['path'], edit['old']
        source = files[path]
        if not old or source.count(old) != 1:
            raise ValueError('repair_anchor_not_unique')
        start = source.index(old)
        end = start + len(old)
        ranges = positions.setdefault(path, [])
        if any(start < stop and end > begin for begin, stop, _ in ranges):
            raise ValueError('repair_anchors_overlap')
        ranges.append((start, end, edit['new']))
    output = dict(files)
    for path, ranges in positions.items():
        for start, end, new in sorted(ranges, reverse=True):
            output[path] = output[path][:start] + new + output[path][end:]
    return output


def apply_statements(files: dict[str, str], targets: list[StatementTarget], repair: StatementRepair) -> dict[str, str]:
    by_id = {target.target_id: target for target in targets}
    ids = [edit.target_id for edit in repair.edits]
    if len(set(ids)) != len(ids) or set(ids) != set(by_id):
        raise ValueError('repair_target_set_mismatch')
    edits = []
    for edit in repair.edits:
        target = by_id[edit.target_id]
        if digest(files[target.path]) != target.source_sha256:
            raise ValueError('repair_target_source_changed')
        if files[target.path][target.start:target.end] != target.old:
            raise ValueError('repair_target_location_changed')
        indent = target.old[:len(target.old) - len(target.old.lstrip())]
        if not edit.new.startswith(indent) or edit.new.endswith(('\r', '\n')):
            raise ValueError('repair_statement_indentation')
        try:
            before = ast.parse(textwrap.dedent(target.old)).body
            after = ast.parse(textwrap.dedent(edit.new)).body
        except SyntaxError as exc:
            raise ValueError('repair_statement_invalid') from exc
        if any(token.type == tokenize.COMMENT for token in tokenize.generate_tokens(io.StringIO(edit.new).readline)):
            raise ValueError('repair_statement_comment')
        if len(after) != 1 or not isinstance(after[0], ast.Assign):
            raise ValueError('repair_statement_shape')
        assert len(before) == 1 and isinstance(before[0], ast.Assign)
        if ast.dump(ast.Tuple(elts=before[0].targets, ctx=ast.Load())) != ast.dump(ast.Tuple(elts=after[0].targets, ctx=ast.Load())):
            raise ValueError('repair_statement_assignment_changed')
        if ast.dump(before[0], include_attributes=False) == ast.dump(after[0], include_attributes=False):
            raise ValueError('runtime_repair_has_no_semantic_change')
        if any(isinstance(n, (ast.Lambda, ast.NamedExpr, ast.Yield, ast.YieldFrom, ast.Await)) or (isinstance(n, ast.Call)
               and ((isinstance(n.func, ast.Attribute) and n.func.attr in {'skip', 'xfail', 'exit'})
                    or (isinstance(n.func, ast.Name) and n.func.id in {'skip', 'xfail', 'exit', 'exec', 'eval', '__import__'})))
               for n in ast.walk(after[0])):
            raise ValueError('repair_statement_control_flow')
        edits.append({'path': target.path, 'old': target.old, 'new': edit.new})
    output = independent_edits(files, edits)
    ast.parse(output[ALLOWED[1]])
    return output
