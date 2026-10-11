"""Pure grant-constraint checks shared by enforcement and trace inspection.

No filesystem calls occur here. Logical paths are POSIX paths relative to the
sandbox; a leading slash names that sandbox, not the host filesystem. Real-path
resolution and symlink checks remain the executor's responsibility.
"""
from pathlib import PurePosixPath
import re

FILESYSTEM_TOOLS = frozenset({'fs.read', 'fs.write', 'fs.delete'})


class ConstraintViolation(ValueError):
    """Malformed, unsupported, or unsatisfied capability constraints."""


def logical_parts(path, *, prefix=False):
    if not isinstance(path, str) or not path or '\x00' in path or path.startswith('//') or '\\' in path or ':' in path:
        raise ConstraintViolation('invalid logical path')
    parts = tuple(p for p in PurePosixPath(path).parts if p not in ('/', '.'))
    if '..' in parts:
        raise ConstraintViolation("logical path contains '..' (jail)")
    if any(p.endswith((' ', '.')) for p in parts):
        raise ConstraintViolation('ambiguous trailing dot or space in path component')
    if not parts and not (prefix and path == '/'):
        raise ConstraintViolation('logical path names no file or directory')
    return parts


def check_constraints(tool, args, constraints):
    """Validate arguments for built-in tools, then all declared constraints.

    Unknown fields are rejected. New tools with more path operands must define
    their own complete validator instead of inheriting a single-path schema.
    Backslashes and colons are forbidden to preserve POSIX semantics on Windows.
    """
    if tool == 'journal.append':
        if set(args) != {'journal', 'content'} or set(constraints) != {'journal'}:
            raise ConstraintViolation('journal append requires exact arguments and journal constraint')
        name = args['journal']
        if not isinstance(name, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) is None:
            raise ConstraintViolation('invalid journal name')
        if name != constraints['journal']:
            raise ConstraintViolation('journal outside grant')
        try:
            if not isinstance(args['content'], str) or len(args['content'].encode('utf-8')) > 4096:
                raise ConstraintViolation('journal content must be UTF-8 text of at most 4096 bytes')
        except UnicodeError as exc:
            raise ConstraintViolation('invalid journal UTF-8') from exc
        return
    if tool not in FILESYSTEM_TOOLS:
        if tool.startswith('fs.'):
            raise ConstraintViolation('unregistered filesystem tool schema')
        if constraints:
            raise ConstraintViolation('unsupported tool constraint')
        return
    required = {'path', 'content'} if tool == 'fs.write' else {'path'}
    if set(args) != required:
        raise ConstraintViolation('missing or unsupported tool argument')
    path = logical_parts(args['path'])
    if tool == 'fs.write':
        if not isinstance(args['content'], str):
            raise ConstraintViolation('content must be a string')
        try:
            args['content'].encode('utf-8')
        except UnicodeError as exc:
            raise ConstraintViolation('content must be valid UTF-8 text') from exc
    if set(constraints) - {'path_prefix'}:
        raise ConstraintViolation('unsupported tool constraint')
    if 'path_prefix' in constraints:
        allowed = logical_parts(constraints['path_prefix'], prefix=True)
        if path[:len(allowed)] != allowed:
            raise ConstraintViolation('path outside granted path_prefix')
