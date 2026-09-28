# hidrive-manager

A command line tool for **IONOS / STRATO HiDrive**. It talks WebDAV directly, so it works
without the official desktop client, without an API key and without waiting for an
app registration.

```
hidrive ls -l /users/home-1234
hidrive put ~/Music/Album "/Musik Backup"
hidrive sync ~/Documents /users/home-1234/Documents --delete
hidrive get "/Musik Backup/Album" -o ~/Downloads
```

---

## Features

- `ls`, `tree`, `stat`, `du`, `find` for browsing a 1 TB drive from the terminal
- `put` / `get` for files and whole directories, parallel transfers, progress bar
- `sync` in both directions with dry run, `--delete` and glob excludes
- Resumable downloads (`.part` files, HTTP Range)
- Skips files that are already up to date (size and modification time)
- `mkdir`, `rm`, `mv`, `cp` executed on the server, no round trip through your machine
- `cat` for piping a remote file into other tools
- `--json` output for `ls`, `stat`, `du`, `find`
- Password stored in the OS keychain (macOS Keychain, Windows Credential Manager, Secret Service on Linux), never in a file
- Environment variables for scripts and CI: `HIDRIVE_USERNAME`, `HIDRIVE_PASSWORD`, `HIDRIVE_URL`
- Two dependencies: `requests` and `keyring`

---

## Requirements

- Python 3.10 or later
- A HiDrive account with **WebDAV enabled**: in the HiDrive web UI go to
  *Settings → Access rights and protocols* and switch WebDAV on. WebDAV is included in
  every HiDrive plan.
- Works with classic HiDrive (IONOS and STRATO). HiDrive Next (the Nextcloud based
  product) also speaks WebDAV; set `--url` to its WebDAV endpoint. Not tested yet.

---

## Install

```bash
git clone https://github.com/nzrbits/hidrive-manager
cd hidrive-manager
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install .
```

or straight from GitHub with [pipx](https://pipx.pypa.io/) or [uv](https://docs.astral.sh/uv/):

```bash
pipx install git+https://github.com/nzrbits/hidrive-manager
uv tool install git+https://github.com/nzrbits/hidrive-manager
```

---

## First run

```bash
hidrive login
```

You are asked for your HiDrive username (for IONOS accounts something like `home-1234`)
and password. The tool verifies the credentials against the WebDAV endpoint, stores the
username in `~/.config/hidrive-manager/config.json` and the password in the system keychain.

```bash
hidrive status
```

prints the configuration and confirms the connection.

---

## Commands

| Command | What it does |
|---|---|
| `login` / `logout` | store or remove credentials |
| `status` | show config and test the connection |
| `ls [-l] [-R] [--json] PATH` | list a directory |
| `tree [-L N] PATH` | directory tree |
| `stat [--json] PATH` | metadata of one entry |
| `put [-j N] [-n] [--exclude GLOB] [--overwrite newer\|always\|never] LOCAL... REMOTE_DIR` | upload |
| `get [-j N] [-n] [-o DIR] [--overwrite ...] REMOTE...` | download |
| `sync [--pull] [--delete] [-y] [-n] [--exclude GLOB] LOCAL REMOTE` | mirror a directory |
| `cat PATH` | stream a file to stdout |
| `mkdir PATH...` | create directories including parents |
| `rm [-r] [-f] [-y] PATH...` | delete |
| `mv [-f] SRC DST` | move or rename on the server |
| `cp [-f] SRC DST` | copy on the server |
| `du [-s] [--json] PATH` | size per child, sorted |
| `find [--name GLOB] [--type f\|d] [--min-size 100M] [-l] [--json] PATH` | search |
| `config [--set KEY=VALUE]` | show or change `url`, `timeout`, `jobs`, `username` |

Global flags: `-q` (quiet, no progress bar), `-v` (print every action), `-u USER`, `--url URL`.

Remote paths are absolute paths on the drive. On IONOS accounts your personal folder is
`/users/<username>`; shared or top level folders sit next to it, for example `/Musik Backup`.

### Examples

```bash
# What is taking space?
hidrive du /users/home-1234

# Upload a folder with 8 parallel connections, skip macOS junk
hidrive put -j 8 --exclude .DS_Store ~/Pictures/2026 /users/home-1234/Pictures

# Look before you sync
hidrive sync -n --delete ~/Projects /users/home-1234/Projects

# Mirror the drive back to disk (pull)
hidrive sync --pull "/Musik Backup" ~/Music/Backup

# All FLAC files above 100 MB as JSON
hidrive find / --name '*.flac' --min-size 100M --json

# Pipe a remote file
hidrive cat /users/home-1234/notes/todo.md | less
```

### How "up to date" is decided

`put`, `get` and `sync` compare size and modification time. A file is transferred when the
size differs or when the source is newer than the destination (2 seconds tolerance).
HiDrive does not accept a client supplied modification time on upload, so after an upload
the remote timestamp is the upload time. That is fine for the next run: same size and the
local file is not newer, so it is skipped. On download the local file gets the remote
timestamp.

`--overwrite always` forces the transfer, `--overwrite never` only adds missing files.

### Scripting

```bash
export HIDRIVE_USERNAME=home-1234
export HIDRIVE_PASSWORD=...        # e.g. from a secret manager
hidrive -q sync --delete -y /srv/backup /users/home-1234/backup
echo $?   # 0 ok, 1 error, 2 not configured, 3 finished with failed transfers
```

`rm` and `sync --delete` refuse to delete without `-y` when stdin is not a terminal.

---

## Configuration

`~/.config/hidrive-manager/config.json` (or `$XDG_CONFIG_HOME/hidrive-manager/config.json`,
or the file named in `HIDRIVE_CONFIG`):

```json
{
  "username": "home-1234",
  "url": "https://webdav.hidrive.ionos.com",
  "timeout": 60,
  "jobs": 4
}
```

STRATO customers use `https://webdav.hidrive.strato.com`.

---

## Limits and known gaps

- WebDAV has no partial upload. An interrupted upload starts over (downloads resume).
- Share links, thumbnails and the trash are only available through the HiDrive REST API,
  which needs a registered OAuth client. Not implemented.
- Large files: uploads of several GB work over WebDAV, but HiDrive's per request limit is
  not documented. Please open an issue if a size fails reproducibly.
- No `mount`. If you need a drive letter, macOS Finder and Windows Explorer can mount
  the WebDAV URL directly, or use [rclone](https://rclone.org/hidrive/).

---

## Development

```bash
pip install -e '.[dev]'
pytest
```

The tests run against an in-memory WebDAV server (`tests/fakedav.py`) that mimics
HiDrive's Apache mod_dav responses, so no account is needed. `scripts/smoke.sh` runs every
command against a real account inside a throwaway folder and cleans up afterwards.

---

## License

MIT, see [LICENSE](LICENSE).
