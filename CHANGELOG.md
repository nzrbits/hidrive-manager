# Changelog

All notable changes to this project will be documented here.

---

## [Unreleased]

### Fixed
- `.gz` / `.tgz` files: HiDrive's Apache tags them with `Content-Encoding: gzip`, which made `put` report a failure after a complete upload and would have gunzipped archives on `get` and `cat`. Responses of PUT/DELETE/MKCOL/MOVE/COPY are no longer decoded, downloads read raw bytes.
- stdout is line buffered so `-v` logs redirected to a file stay in order
- `put`/`sync` create each missing directory with one MKCOL instead of an existence check per parent

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
