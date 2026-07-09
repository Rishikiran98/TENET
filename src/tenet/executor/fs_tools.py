"""Sandboxed file tools — the only side effects v1 can perform (§8).

Two independent checks run on every call, both at the executor:

1. **The path jail.** Every logical path is resolved against the sandbox root
   and the result must stay inside it. ``..`` traversal, absolute-path tricks,
   and symlink escapes all fail here — checked on the *resolved* real path, so
   a symlink pointing out of the sandbox is caught even though its own path
   looks innocent.
2. **Constraint re-check.** The ToolGrant's ``path_prefix`` is enforced again,
   component-wise, even though the gate already vetted the proposal. Defense in
   depth: the executor does not trust that the gate was the only path to it.

Paths are *logical*: POSIX-style, interpreted relative to the sandbox root, and
a leading ``/`` means the sandbox root itself — ``/notes/a.txt`` and
``notes/a.txt`` are the same file. Nothing here can name a real absolute path.

A violation raises ``SandboxViolation`` (a ``ToolExecutionError``), which the
loop records as ``action.failed`` — an execution-time refusal, distinct from a
policy ``action.blocked``.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from ..agent.loop import ToolExecutionError


class SandboxViolation(ToolExecutionError):
    """A path tried to leave the sandbox or its granted prefix."""


def _logical_parts(path_str: str) -> tuple[str, ...]:
    """Normalize a logical path to its components. Rejects ``..`` outright —
    inside the sandbox there is nothing above the root to legitimately name."""
    parts = [p for p in PurePosixPath(path_str).parts if p not in ("/", ".")]
    if ".." in parts:
        raise SandboxViolation(f"path {path_str!r} contains '..' (jail)")
    if not parts:
        raise SandboxViolation(f"path {path_str!r} names no file")
    return tuple(parts)


class SandboxedFs:
    """The jail. All three fs tools resolve their paths through here."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, path_str: str, constraints: dict) -> Path:
        parts = _logical_parts(path_str)

        # constraint re-check (defense in depth): component-wise prefix match,
        # so a prefix of /notes does not authorize /notes2.
        prefix = constraints.get("path_prefix")
        if prefix is not None:
            prefix_parts = tuple(
                p for p in PurePosixPath(prefix).parts if p not in ("/", ".")
            )
            if parts[: len(prefix_parts)] != prefix_parts:
                raise SandboxViolation(
                    f"path {path_str!r} outside granted prefix {prefix!r}"
                )

        # the jail: resolve (follows symlinks) and require the result inside root
        candidate = self._root.joinpath(*parts).resolve()
        if not candidate.is_relative_to(self._root):
            raise SandboxViolation(f"path {path_str!r} escapes the sandbox (jail)")
        return candidate


def _require_path(args: dict) -> str:
    path = args.get("path")
    if not isinstance(path, str) or not path:
        raise ToolExecutionError("args must include a non-empty 'path'")
    return path


class FsRead:
    name = "fs.read"

    def __init__(self, fs: SandboxedFs) -> None:
        self._fs = fs

    def execute(self, args: dict, constraints: dict) -> dict:
        target = self._fs.resolve(_require_path(args), constraints)
        if not target.is_file():
            raise ToolExecutionError(f"no such file: {args['path']!r}")
        return {"path": args["path"], "content": target.read_text("utf-8")}


class FsWrite:
    name = "fs.write"

    def __init__(self, fs: SandboxedFs) -> None:
        self._fs = fs

    def execute(self, args: dict, constraints: dict) -> dict:
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolExecutionError("fs.write args must include string 'content'")
        target = self._fs.resolve(_require_path(args), constraints)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, "utf-8")
        return {"path": args["path"], "bytes_written": len(content.encode("utf-8"))}


class FsDelete:
    name = "fs.delete"

    def __init__(self, fs: SandboxedFs) -> None:
        self._fs = fs

    def execute(self, args: dict, constraints: dict) -> dict:
        target = self._fs.resolve(_require_path(args), constraints)
        if not target.is_file():
            raise ToolExecutionError(f"no such file: {args['path']!r}")
        target.unlink()
        return {"path": args["path"], "deleted": True}
