"""Command line interface: `hidrive <command> ...`."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

from . import __version__, config, ops, paths
from .davxml import Entry
from .progress import Progress, human_size
from .webdav import AuthError, NotFound, WebDavClient, WebDavError

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_PARTIAL = 0, 1, 2, 3


class CliError(Exception):
    pass


# --- helpers -----------------------------------------------------------------------

def _err(msg: str) -> None:
    print(f"hidrive: {msg}", file=sys.stderr)


def _client(args) -> WebDavClient:
    s = config.load()
    username = args.username or s.username
    url = args.url or s.url
    if not username:
        raise CliError("no username configured. Run `hidrive login` or set HIDRIVE_USERNAME.")
    password = config.get_password(username)
    if password is None:
        raise CliError(f"no password stored for {username!r}. Run `hidrive login` or set HIDRIVE_PASSWORD.")
    return WebDavClient(url, username, password, timeout=s.timeout)


def _fmt_time(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else "                "


def _entry_dict(e: Entry) -> dict:
    return {
        "path": e.path,
        "name": e.name,
        "type": "dir" if e.is_dir else "file",
        "size": e.size,
        "mtime": e.mtime.isoformat() if e.mtime else None,
        "etag": e.etag,
        "content_type": e.content_type,
    }


def _print_entries(entries: list[Entry], long: bool, as_json: bool, show_path: bool = False) -> None:
    if as_json:
        print(json.dumps([_entry_dict(e) for e in entries], indent=2))
        return
    for e in entries:
        label = e.path if show_path else e.name
        if e.is_dir and not show_path:
            label += "/"
        if long:
            kind = "d" if e.is_dir else "-"
            size = "" if e.is_dir else human_size(e.size)
            print(f"{kind} {size:>10}  {_fmt_time(e.mtime)}  {label}")
        else:
            print(label)


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise CliError("refusing to delete without --yes when stdin is not a terminal")
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def _print_plan(plan: ops.Plan, verbose: bool) -> None:
    counts = {}
    for a in plan.actions:
        counts[a.kind] = counts.get(a.kind, 0) + 1
    if verbose:
        for a in plan.actions:
            if a.kind == "skip":
                continue
            target = a.remote if a.kind in ("upload", "mkdir", "delete_remote") else str(a.local)
            extra = f"  ({human_size(a.size)})" if a.kind in ("upload", "download") else ""
            print(f"{a.kind:<13} {target}{extra}")
    summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
    print(f"plan: {summary}; {human_size(plan.transfer_bytes)} to transfer")


def _run_plan(client: WebDavClient, plan: ops.Plan, args, label: str) -> int:
    if plan.is_noop:
        print("nothing to do, everything up to date")
        return EXIT_OK
    if args.dry_run:
        _print_plan(plan, verbose=True)
        return EXIT_OK
    if args.verbose:
        _print_plan(plan, verbose=True)
    transfers = plan.of("upload") + plan.of("download")
    progress = Progress(plan.transfer_bytes, len(transfers), enabled=None if not args.quiet else False, label=label)
    res = ops.execute(client, plan, jobs=args.jobs, progress=progress, verbose=args.verbose)
    progress.finish()
    msg = f"done: {res.ok} ok, {res.skipped} skipped, {len(res.failed)} failed"
    if not args.quiet or res.failed:
        print(msg, file=sys.stderr if res.failed else sys.stdout)
    for target, reason in res.failed:
        _err(f"  {target}: {reason}")
    return EXIT_PARTIAL if res.failed else EXIT_OK


# --- commands ----------------------------------------------------------------------

def cmd_login(args) -> int:
    s = config.load()
    username = args.login_username or args.username or s.username or input("HiDrive username: ").strip()
    url = args.url or s.url
    password = config.get_password(username) if args.keep_password else None
    if password is None and args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    if password is None:
        if not sys.stdin.isatty():
            raise CliError("no terminal for the password prompt. Run this in a real terminal, or pipe the password with --password-stdin.")
        try:
            password = getpass.getpass(f"Password for {username}: ")
        except EOFError:
            raise CliError("password prompt aborted")
    if not username or not password:
        raise CliError("username and password are required")
    client = WebDavClient(url, username, password, timeout=s.timeout)
    try:
        client.check_login()
    except AuthError:
        raise CliError("login failed: HiDrive rejected the credentials. Check that WebDAV is enabled under Settings > Access rights and protocols.")
    s.username, s.url = username, url
    p = config.save(s)
    config.set_password(username, password)
    print(f"login ok. username and URL saved to {p}, password stored in the system keychain.")
    return EXIT_OK


def cmd_logout(args) -> int:
    s = config.load()
    if s.username:
        config.delete_password(s.username)
    config.delete()
    print("credentials removed.")
    return EXIT_OK


def cmd_status(args) -> int:
    s = config.load()
    print(f"config file : {config.config_path()}")
    print(f"username    : {s.username or '(not set)'}")
    print(f"url         : {args.url or s.url}")
    print(f"password    : {'stored' if config.get_password(args.username or s.username) else 'missing'}")
    client = _client(args)
    root = client.listdir("/")
    print(f"connection  : ok ({len(root)} entries in /)")
    return EXIT_OK


def cmd_ls(args) -> int:
    client = _client(args)
    entries: list[Entry] = []
    if args.recursive:
        for _, dirs, files in client.walk(args.path):
            entries.extend(dirs + files)
        _print_entries(entries, args.long, args.json, show_path=True)
        return EXIT_OK
    entry = client.stat(args.path)
    if entry is None:
        raise NotFound(404, "not found", args.path)
    if entry.is_dir:
        entries = client.listdir(args.path)
    else:
        entries = [entry]
    _print_entries(entries, args.long, args.json)
    return EXIT_OK


def cmd_tree(args) -> int:
    client = _client(args)
    root = paths.normalize(args.path)
    print(root)
    counts = {"dirs": 0, "files": 0}

    def rec(path: str, prefix: str, depth: int) -> None:
        if args.depth is not None and depth > args.depth:
            return
        children = client.listdir(path)
        for i, c in enumerate(children):
            last = i == len(children) - 1
            print(f"{prefix}{'└── ' if last else '├── '}{c.name}{'/' if c.is_dir else ''}")
            if c.is_dir:
                counts["dirs"] += 1
                rec(c.path, prefix + ("    " if last else "│   "), depth + 1)
            else:
                counts["files"] += 1

    rec(root, "", 1)
    print(f"\n{counts['dirs']} directories, {counts['files']} files")
    return EXIT_OK


def cmd_stat(args) -> int:
    client = _client(args)
    e = client.stat(args.path)
    if e is None:
        raise NotFound(404, "not found", args.path)
    if args.json:
        print(json.dumps(_entry_dict(e), indent=2))
        return EXIT_OK
    for k, v in _entry_dict(e).items():
        print(f"{k:<13}: {v if v is not None else ''}")
    return EXIT_OK


def cmd_put(args) -> int:
    client = _client(args)
    sources = [Path(s).expanduser() for s in args.local]
    for s in sources:
        if not s.exists():
            raise CliError(f"local path not found: {s}")
    plan = ops.plan_upload(client, sources, args.remote, overwrite=args.overwrite, excludes=args.exclude)
    return _run_plan(client, plan, args, label="upload ")


def cmd_get(args) -> int:
    client = _client(args)
    local_dir = Path(args.local_dir).expanduser()
    try:
        plan = ops.plan_download(client, args.remote, local_dir, overwrite=args.overwrite, excludes=args.exclude)
    except FileNotFoundError as exc:
        raise NotFound(404, "not found", str(exc))
    return _run_plan(client, plan, args, label="download ")


def cmd_sync(args) -> int:
    client = _client(args)
    local = Path(args.local).expanduser()
    if not args.pull and not local.is_dir():
        raise CliError(f"local directory not found: {local}")
    plan = ops.plan_sync(client, local, args.remote, pull=args.pull, delete=args.delete, excludes=args.exclude)
    deletions = plan.of("delete_remote") + plan.of("delete_local")
    if deletions and not args.dry_run and not _confirm(f"sync will delete {len(deletions)} item(s) on the destination. Continue?", args.yes):
        print("aborted.")
        return EXIT_ERROR
    return _run_plan(client, plan, args, label="sync ")


def cmd_cat(args) -> int:
    client = _client(args)
    resp = client.open_stream(args.path)
    out = sys.stdout.buffer
    try:
        for chunk in resp.iter_content(1024 * 256):
            out.write(chunk)
    finally:
        resp.close()
    out.flush()
    return EXIT_OK


def cmd_mkdir(args) -> int:
    client = _client(args)
    for p in args.path:
        client.makedirs(p)
        if not args.quiet:
            print(paths.normalize(p))
    return EXIT_OK


def cmd_rm(args) -> int:
    client = _client(args)
    targets: list[Entry] = []
    for p in args.path:
        e = client.stat(p)
        if e is None:
            if args.force:
                continue
            raise NotFound(404, "not found", p)
        if e.is_dir and not args.recursive:
            raise CliError(f"{e.path} is a directory (use -r)")
        targets.append(e)
    if not targets:
        return EXIT_OK
    if not args.yes:
        for e in targets:
            print(f"{'dir ' if e.is_dir else 'file'} {e.path}")
    if not _confirm(f"delete {len(targets)} item(s) permanently?", args.yes):
        print("aborted.")
        return EXIT_ERROR
    for e in targets:
        client.delete(e.path)
        if not args.quiet:
            print(f"deleted {e.path}")
    return EXIT_OK


def cmd_mv(args) -> int:
    client = _client(args)
    dst = _resolve_destination(client, args.src, args.dst)
    client.move(args.src, dst, overwrite=args.force)
    if not args.quiet:
        print(f"{paths.normalize(args.src)} -> {dst}")
    return EXIT_OK


def cmd_cp(args) -> int:
    client = _client(args)
    dst = _resolve_destination(client, args.src, args.dst)
    client.copy(args.src, dst, overwrite=args.force)
    if not args.quiet:
        print(f"{paths.normalize(args.src)} -> {dst}")
    return EXIT_OK


def _resolve_destination(client: WebDavClient, src: str, dst: str) -> str:
    """If dst is an existing directory, place src inside it (like `mv file dir/`)."""
    e = client.stat(dst)
    if e is not None and e.is_dir:
        return paths.join(dst, paths.name(src))
    return paths.normalize(dst)


def cmd_du(args) -> int:
    client = _client(args)
    root = paths.normalize(args.path)
    rows: list[tuple[int, str]] = []
    if args.summarize:
        total, files, dirs = ops.du(client, root)
        rows.append((total, root))
    else:
        total = 0
        files = dirs = 0
        for c in client.listdir(root):
            if c.is_dir:
                b, f, d = ops.du(client, c.path)
                dirs += 1 + d
                files += f
            else:
                b, f = c.size, 1
                files += 1
            total += b
            rows.append((b, c.path))
        rows.sort(reverse=True)
        rows.append((total, root + "  (total)"))
    if args.json:
        print(json.dumps({"path": root, "bytes": total, "files": files, "dirs": dirs, "children": [{"path": p, "bytes": b} for b, p in rows]}, indent=2))
        return EXIT_OK
    for b, p in rows:
        print(f"{human_size(b):>10}  {p}")
    print(f"{files} files, {dirs} directories")
    return EXIT_OK


def cmd_find(args) -> int:
    client = _client(args)
    min_size = _parse_size(args.min_size) if args.min_size else None
    found = list(ops.find(client, args.path, pattern=args.name, kind=args.type, min_size=min_size))
    _print_entries(found, args.long, args.json, show_path=True)
    return EXIT_OK


def _parse_size(text: str) -> int:
    units = {"k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
    t = text.strip().lower().rstrip("ib")
    if t and t[-1] in units:
        return int(float(t[:-1]) * units[t[-1]])
    return int(t)


def cmd_config(args) -> int:
    s = config.load()
    if args.set:
        key, _, value = args.set.partition("=")
        if key not in ("url", "timeout", "jobs", "username"):
            raise CliError(f"unknown setting {key!r} (url, timeout, jobs, username)")
        if key in ("timeout", "jobs"):
            setattr(s, key, int(value))
        else:
            setattr(s, key, value)
        config.save(s)
    print(f"config file : {config.config_path()}")
    for k, v in s.to_dict().items():
        print(f"{k:<12}: {v}")
    return EXIT_OK


# --- parser ------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hidrive",
        description="Manage an IONOS/STRATO HiDrive over WebDAV.",
        epilog="Remote paths are absolute on the drive, e.g. /users/<name>/Photos. Run `hidrive login` once.",
    )
    p.add_argument("--version", action="version", version=f"hidrive-manager {__version__}")
    p.add_argument("-u", "--username", help="override the configured username")
    p.add_argument("--url", help="override the WebDAV base URL")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress bar, fewer messages")
    p.add_argument("-v", "--verbose", action="store_true", help="print every action")
    sub = p.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    def add(name: str, func, help: str, **kw) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help, description=help, **kw)
        sp.set_defaults(func=func)
        return sp

    def transfer_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("-j", "--jobs", type=int, default=config.load().jobs, help="parallel transfers (default from config, 4)")
        sp.add_argument("-n", "--dry-run", action="store_true", help="show the plan, transfer nothing")
        sp.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip names/paths matching GLOB (repeatable)")

    sp = add("login", cmd_login, "store username and password (keychain) and verify the connection")
    sp.add_argument("login_username", nargs="?", metavar="USERNAME", help="HiDrive username, e.g. home-1234 (asked for if omitted)")
    sp.add_argument("--keep-password", action="store_true", help="reuse the stored password, only re-verify")
    sp.add_argument("--password-stdin", action="store_true", help="read the password from the first line of stdin")
    add("logout", cmd_logout, "remove stored credentials and config")
    add("status", cmd_status, "show configuration and test the connection")

    sp = add("ls", cmd_ls, "list a directory")
    sp.add_argument("path", nargs="?", default="/")
    sp.add_argument("-l", "--long", action="store_true", help="size and modification time")
    sp.add_argument("-R", "--recursive", action="store_true")
    sp.add_argument("--json", action="store_true")

    sp = add("tree", cmd_tree, "show a directory tree")
    sp.add_argument("path", nargs="?", default="/")
    sp.add_argument("-L", "--depth", type=int, help="maximum depth")

    sp = add("stat", cmd_stat, "show metadata of one path")
    sp.add_argument("path")
    sp.add_argument("--json", action="store_true")

    sp = add("put", cmd_put, "upload files or directories into a remote directory")
    sp.add_argument("local", nargs="+", help="local files or directories")
    sp.add_argument("remote", help="remote target directory (created if missing)")
    sp.add_argument("--overwrite", choices=["newer", "always", "never"], default="newer", help="when the remote file exists (default: newer = size differs or local is newer)")
    transfer_opts(sp)

    sp = add("get", cmd_get, "download files or directories")
    sp.add_argument("remote", nargs="+", help="remote files or directories")
    sp.add_argument("-o", "--local-dir", default=".", help="local target directory (default: current)")
    sp.add_argument("--overwrite", choices=["newer", "always", "never"], default="newer")
    transfer_opts(sp)

    sp = add("sync", cmd_sync, "mirror a local directory to a remote directory (or back with --pull)")
    sp.add_argument("local", help="local directory")
    sp.add_argument("remote", help="remote directory")
    sp.add_argument("--pull", action="store_true", help="mirror remote -> local instead of local -> remote")
    sp.add_argument("--delete", action="store_true", help="delete files on the destination that are missing in the source")
    sp.add_argument("-y", "--yes", action="store_true", help="do not ask before deleting")
    transfer_opts(sp)

    sp = add("cat", cmd_cat, "print a remote file to stdout")
    sp.add_argument("path")

    sp = add("mkdir", cmd_mkdir, "create directories (parents included)")
    sp.add_argument("path", nargs="+")

    sp = add("rm", cmd_rm, "delete files or directories")
    sp.add_argument("path", nargs="+")
    sp.add_argument("-r", "--recursive", action="store_true", help="allow deleting directories")
    sp.add_argument("-f", "--force", action="store_true", help="ignore missing paths")
    sp.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")

    for name, func, help_ in (("mv", cmd_mv, "move or rename"), ("cp", cmd_cp, "copy on the server")):
        sp = add(name, func, help_)
        sp.add_argument("src")
        sp.add_argument("dst", help="target path, or an existing directory")
        sp.add_argument("-f", "--force", action="store_true", help="overwrite an existing destination")

    sp = add("du", cmd_du, "disk usage per child of a directory")
    sp.add_argument("path", nargs="?", default="/")
    sp.add_argument("-s", "--summarize", action="store_true", help="only the total")
    sp.add_argument("--json", action="store_true")

    sp = add("find", cmd_find, "search files and directories")
    sp.add_argument("path", nargs="?", default="/")
    sp.add_argument("--name", metavar="GLOB", help="match the entry name, e.g. '*.jpg'")
    sp.add_argument("--type", choices=["f", "d"], help="files only (f) or directories only (d)")
    sp.add_argument("--min-size", metavar="SIZE", help="e.g. 100M, 2G")
    sp.add_argument("-l", "--long", action="store_true")
    sp.add_argument("--json", action="store_true")

    sp = add("config", cmd_config, "show or change settings")
    sp.add_argument("--set", metavar="KEY=VALUE", help="url, timeout, jobs or username")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:  # keep stdout and stderr interleaved correctly when logging to a file
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    try:
        return args.func(args)
    except CliError as exc:
        _err(str(exc))
        return EXIT_USAGE if "Run `hidrive login`" in str(exc) else EXIT_ERROR
    except AuthError as exc:
        _err(f"{exc}. Run `hidrive login` to update the stored password.")
        return EXIT_ERROR
    except (NotFound, WebDavError) as exc:
        _err(str(exc))
        return EXIT_ERROR
    except requests.ConnectionError as exc:
        _err(f"connection failed: {exc}")
        return EXIT_ERROR
    except requests.Timeout:
        _err("request timed out (raise `hidrive config --set timeout=120`)")
        return EXIT_ERROR
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
