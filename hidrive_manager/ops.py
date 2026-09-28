"""Higher-level operations: recursive upload/download, sync planning, du, find."""
from __future__ import annotations

import fnmatch
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .davxml import Entry
from .progress import Progress
from .webdav import WebDavClient

MTIME_TOLERANCE = 2.0  # seconds; WebDAV timestamps have 1s resolution


@dataclass
class Action:
    kind: str  # "upload" | "download" | "mkdir" | "delete_remote" | "delete_local" | "skip"
    local: Path | None = None
    remote: str | None = None
    size: int = 0
    reason: str = ""
    mtime: float | None = None  # remote mtime, applied to the local file after download


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)

    def add(self, a: Action) -> None:
        self.actions.append(a)

    def of(self, kind: str) -> list[Action]:
        return [a for a in self.actions if a.kind == kind]

    @property
    def transfer_bytes(self) -> int:
        return sum(a.size for a in self.actions if a.kind in ("upload", "download"))

    @property
    def is_noop(self) -> bool:
        return all(a.kind == "skip" for a in self.actions)


@dataclass
class Result:
    ok: int = 0
    skipped: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def _excluded(rel: str, excludes: list[str]) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat) for pat in excludes)


# --- local/remote tree snapshots ---------------------------------------------------

def local_tree(root: Path, excludes: list[str] | None = None) -> tuple[dict[str, Path], set[str]]:
    """Return ({relpath: Path} for files, {relpath} for dirs) below root, '/'-separated."""
    excludes = excludes or []
    files: dict[str, Path] = {}
    dirs: set[str] = set()
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in sorted(dirnames):
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if _excluded(rel, excludes):
                continue
            dirs.add(rel)
            keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            rel = f"{rel_dir}/{f}" if rel_dir else f
            if _excluded(rel, excludes):
                continue
            p = Path(dirpath) / f
            if p.is_symlink() and not p.exists():
                continue
            files[rel] = p
    return files, dirs


def remote_tree(client: WebDavClient, root: str, excludes: list[str] | None = None) -> tuple[dict[str, Entry], set[str]]:
    excludes = excludes or []
    files: dict[str, Entry] = {}
    dirs: set[str] = set()
    root = paths.normalize(root)
    for dirpath, subdirs, fentries in client.walk(root):
        for d in list(subdirs):
            rel = paths.relative(d.path, root)
            if _excluded(rel, excludes):
                subdirs.remove(d)
                continue
            dirs.add(rel)
        for f in fentries:
            rel = paths.relative(f.path, root)
            if _excluded(rel, excludes):
                continue
            files[rel] = f
    return files, dirs


# --- upload ------------------------------------------------------------------------

def plan_upload(client: WebDavClient, sources: list[Path], remote_dir: str, overwrite: str = "newer", excludes: list[str] | None = None) -> Plan:
    """overwrite: 'always' | 'never' | 'newer' (size differs or local is newer)."""
    plan = Plan()
    remote_dir = paths.normalize(remote_dir)
    remote_files: dict[str, Entry] = {}
    remote_dirs: set[str] = set()
    if client.exists(remote_dir):
        remote_files, remote_dirs = remote_tree(client, remote_dir)
    else:
        plan.add(Action("mkdir", remote=remote_dir))

    def consider(local: Path, rel: str) -> None:
        size = local.stat().st_size
        remote_path = paths.join(remote_dir, rel)
        existing = remote_files.get(rel)
        if existing is not None and not _needs_upload(local, existing, overwrite):
            plan.add(Action("skip", local=local, remote=remote_path, size=size, reason="up to date"))
            return
        plan.add(Action("upload", local=local, remote=remote_path, size=size))

    for src in sources:
        if src.is_dir():
            files, dirs = local_tree(src, excludes)
            for rel in [src.name] + [f"{src.name}/{d}" for d in sorted(dirs)]:
                if rel not in remote_dirs:
                    plan.add(Action("mkdir", remote=paths.join(remote_dir, rel)))
                    remote_dirs.add(rel)
            for rel, p in files.items():
                consider(p, f"{src.name}/{rel}")
        elif src.is_file():
            consider(src, src.name)
        else:
            raise FileNotFoundError(src)
    # mkdir actions must run parents first
    mk = sorted(plan.of("mkdir"), key=lambda a: a.remote.count("/"))
    plan.actions = mk + [a for a in plan.actions if a.kind != "mkdir"]
    return plan


def _needs_upload(local: Path, remote: Entry, overwrite: str) -> bool:
    if overwrite == "always":
        return True
    if overwrite == "never":
        return False
    st = local.stat()
    if st.st_size != remote.size:
        return True
    rt = remote.mtime_ts
    return rt is None or st.st_mtime > rt + MTIME_TOLERANCE


def _needs_download(remote: Entry, local: Path, overwrite: str) -> bool:
    if overwrite == "always":
        return True
    if overwrite == "never":
        return False
    if not local.exists():
        return True
    st = local.stat()
    if st.st_size != remote.size:
        return True
    rt = remote.mtime_ts
    return rt is not None and rt > st.st_mtime + MTIME_TOLERANCE


# --- download ----------------------------------------------------------------------

def plan_download(client: WebDavClient, remotes: list[str], local_dir: Path, overwrite: str = "newer", excludes: list[str] | None = None) -> Plan:
    plan = Plan()
    for r in remotes:
        r = paths.normalize(r)
        entry = client.stat(r)
        if entry is None:
            raise FileNotFoundError(r)
        if entry.is_dir:
            files, dirs = remote_tree(client, r, excludes)
            base = local_dir / entry.name
            for d in [base] + [base / d for d in sorted(dirs)]:
                if not d.is_dir():
                    plan.add(Action("mkdir_local", local=d))
            for rel, e in files.items():
                target = base / rel
                if _needs_download(e, target, overwrite):
                    plan.add(Action("download", local=target, remote=e.path, size=e.size, mtime=e.mtime_ts))
                else:
                    plan.add(Action("skip", local=target, remote=e.path, size=e.size, reason="up to date"))
        else:
            target = local_dir / entry.name
            if _needs_download(entry, target, overwrite):
                plan.add(Action("download", local=target, remote=entry.path, size=entry.size, mtime=entry.mtime_ts))
            else:
                plan.add(Action("skip", local=target, remote=entry.path, size=entry.size, reason="up to date"))
    return plan


# --- sync --------------------------------------------------------------------------

def plan_sync(client: WebDavClient, local_root: Path, remote_root: str, pull: bool = False, delete: bool = False, excludes: list[str] | None = None) -> Plan:
    """Mirror local_root -> remote_root (push) or remote_root -> local_root (pull)."""
    plan = Plan()
    remote_root = paths.normalize(remote_root)
    lfiles, ldirs = local_tree(local_root, excludes) if local_root.exists() else ({}, set())
    if client.exists(remote_root):
        rfiles, rdirs = remote_tree(client, remote_root, excludes)
    else:
        rfiles, rdirs = {}, set()
        if not pull:
            plan.add(Action("mkdir", remote=remote_root))

    if not pull:
        for d in sorted(ldirs - rdirs, key=lambda s: s.count("/")):
            plan.add(Action("mkdir", remote=paths.join(remote_root, d)))
        for rel, lp in lfiles.items():
            rp = paths.join(remote_root, rel)
            re_ = rfiles.get(rel)
            if re_ is None or _needs_upload(lp, re_, "newer"):
                plan.add(Action("upload", local=lp, remote=rp, size=lp.stat().st_size, reason="missing" if re_ is None else "changed"))
            else:
                plan.add(Action("skip", local=lp, remote=rp, size=lp.stat().st_size, reason="up to date"))
        if delete:
            for rel in sorted(set(rfiles) - set(lfiles)):
                plan.add(Action("delete_remote", remote=rfiles[rel].path, reason="not in source"))
            for rel in sorted(rdirs - ldirs, key=lambda s: -s.count("/")):
                if not any(r.startswith(rel + "/") for r in lfiles):
                    plan.add(Action("delete_remote", remote=paths.join(remote_root, rel), reason="not in source"))
    else:
        if not local_root.exists():
            plan.add(Action("mkdir_local", local=local_root))
        for d in sorted(rdirs - ldirs, key=lambda s: s.count("/")):
            plan.add(Action("mkdir_local", local=local_root / d))
        for rel, re_ in rfiles.items():
            lp = local_root / rel
            if _needs_download(re_, lp, "newer"):
                plan.add(Action("download", local=lp, remote=re_.path, size=re_.size, mtime=re_.mtime_ts, reason="missing" if not lp.exists() else "changed"))
            else:
                plan.add(Action("skip", local=lp, remote=re_.path, size=re_.size, reason="up to date"))
        if delete:
            for rel in sorted(set(lfiles) - set(rfiles)):
                plan.add(Action("delete_local", local=lfiles[rel], reason="not in source"))
            for rel in sorted(ldirs - rdirs, key=lambda s: -s.count("/")):
                plan.add(Action("delete_local", local=local_root / rel, reason="not in source"))
    return plan


# --- execution ---------------------------------------------------------------------

def execute(client: WebDavClient, plan: Plan, jobs: int = 4, progress: Progress | None = None, verbose: bool = False) -> Result:
    res = Result()
    log = progress.write if progress else (lambda m: print(m))

    # 1. directories (sequential, parents first)
    for a in plan.of("mkdir"):
        try:
            client.makedirs(a.remote)
            res.ok += 1
            if verbose:
                log(f"mkdir  {a.remote}")
        except Exception as exc:
            res.failed.append((a.remote or "", str(exc)))
    for a in plan.of("mkdir_local"):
        a.local.mkdir(parents=True, exist_ok=True)

    # 2. transfers (parallel)
    transfers = plan.of("upload") + plan.of("download")
    cb = progress.advance if progress else None

    def run(a: Action) -> None:
        if a.kind == "upload":
            client.upload(a.local, a.remote, cb)
        else:
            client.download(a.remote, a.local, cb, expected_size=a.size)
            if a.mtime is not None:
                os.utime(a.local, (a.mtime, a.mtime))

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futs = {pool.submit(run, a): a for a in transfers}
        for fut in as_completed(futs):
            a = futs[fut]
            label = a.remote if a.kind == "upload" else str(a.local)
            try:
                fut.result()
                res.ok += 1
                if verbose:
                    log(f"{a.kind:<8} {label}")
            except Exception as exc:
                res.failed.append((label, str(exc)))
                log(f"FAILED {a.kind} {label}: {exc}")
            finally:
                if progress:
                    progress.file_done()

    # 3. deletions (after transfers; deepest paths first)
    for a in sorted(plan.of("delete_remote"), key=lambda x: -(x.remote or "").count("/")):
        try:
            client.delete(a.remote)
            res.ok += 1
            if verbose:
                log(f"delete {a.remote}")
        except Exception as exc:
            res.failed.append((a.remote or "", str(exc)))
    for a in sorted(plan.of("delete_local"), key=lambda x: -len(str(x.local))):
        try:
            if a.local.is_dir():
                a.local.rmdir()
            else:
                a.local.unlink()
            res.ok += 1
            if verbose:
                log(f"delete {a.local}")
        except Exception as exc:
            res.failed.append((str(a.local), str(exc)))

    res.skipped = len(plan.of("skip"))
    return res


# --- helpers for du / find ---------------------------------------------------------

def du(client: WebDavClient, path: str) -> tuple[int, int, int]:
    """Return (bytes, files, dirs) below path (path itself excluded from dir count)."""
    total = files = dirs = 0
    for _, subdirs, fentries in client.walk(path):
        dirs += len(subdirs)
        files += len(fentries)
        total += sum(f.size for f in fentries)
    return total, files, dirs


def find(client: WebDavClient, path: str, pattern: str | None = None, kind: str | None = None, min_size: int | None = None):
    for dirpath, subdirs, fentries in client.walk(path):
        for e in subdirs + fentries:
            if kind == "f" and e.is_dir:
                continue
            if kind == "d" and not e.is_dir:
                continue
            if pattern and not fnmatch.fnmatch(e.name, pattern):
                continue
            if min_size is not None and e.size < min_size:
                continue
            yield e
