# Changelog

All notable changes to this project will be documented here.

---

## [0.1.0] - 2026-09-28

### Added
- `hidrive` CLI over WebDAV: `login`, `logout`, `status`, `ls`, `tree`, `stat`, `put`, `get`, `sync`, `cat`, `mkdir`, `rm`, `mv`, `cp`, `du`, `find`, `config`
- Parallel uploads and downloads with a shared progress bar
- Resumable downloads via `.part` files and HTTP Range
- Up-to-date detection by size and modification time; `--overwrite newer|always|never`
- `sync` in both directions with `--delete`, `--dry-run` and `--exclude`
- Password in the OS keychain via `keyring`; env overrides for scripts
- `--json` output for `ls`, `stat`, `du`, `find`
- Test suite against an in-memory WebDAV server mimicking HiDrive
- CI on macOS, Linux and Windows
