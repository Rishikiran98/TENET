"""Pure grant-constraint checks shared by enforcement and trace inspection.

No filesystem calls occur here. Logical paths are POSIX paths relative to the
sandbox; a leading slash names that sandbox, not the host filesystem. Real-path
resolution and symlink checks remain the executor's responsibility.
"""
from pathlib import PurePosixPath

FILESYSTEM_TOOLS = frozenset({'fs.read', 'fs.write', 'fs.delete'})


class ConstraintViolation(ValueError):
    """Malformed, unsupported, or unsatisfied capability constraints."""


def logical_parts(path, *, prefix=False):
    if not isinstance(path, str) or not path or '\x00' in path or path.startswith('//'):
        raise ConstraintViolation('invalid logical path')
    parts = tuple(p for p in PurePosixPath(path).parts if p not in ('/', '.'))
    if '..' in parts:
        raise ConstraintViolation("logical path contains '..' (jail)")
    if not parts and not (prefix and path == '/'):
        raise ConstraintViolation('logical path names no file or directory')
    return parts


def check_constraints(tool, args, constraints):
    """Raise on any constraint this mechanism cannot fully enforce logically.

    With no constraints, argument validity and the root jail remain executor
    checks. A path_prefix requires a valid path before policy can be consulted.
    New constrained tool families need an explicit validator here.
    """
    if not constraints:
        return
    if tool not in FILESYSTEM_TOOLS or set(constraints) - {'path_prefix'}:
        raise ConstraintViolation('unsupported tool constraint')
    if 'path_prefix' in constraints:
        allowed = logical_parts(constraints['path_prefix'], prefix=True)
        path = logical_parts(args.get('path'))
        if path[:len(allowed)] != allowed:
            raise ConstraintViolation('path outside granted path_prefix')
