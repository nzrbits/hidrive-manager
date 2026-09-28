#!/usr/bin/env bash
# Live smoke test: runs every command against a real HiDrive account inside a throwaway
# folder and removes it afterwards. Needs `hidrive login` done (or HIDRIVE_USERNAME/PASSWORD).
#
#   scripts/smoke.sh [remote-base-dir]     default: /users/<username>
set -euo pipefail

H="${HIDRIVE_BIN:-hidrive}"
USER_NAME="${HIDRIVE_USERNAME:-$($H config | awk -F': ' '/^username/ {print $2}')}"
BASE="${1:-/users/$USER_NAME}"
T="$BASE/_hidrive-manager-smoke-$$"
S="$(mktemp -d)"
trap 'echo "## cleanup"; $H rm -r -y -f "$T" || true; rm -rf "$S"' EXIT

step() { printf '\n## %s\n' "$*"; }
same() { [ "$(shasum -a 256 < "$1")" = "$(shasum -a 256 < "$2")" ] || { echo "MISMATCH $1 $2"; exit 1; }; }

mkdir -p "$S/src/album/disc1" "$S/dl"
head -c 5242880 /dev/urandom > "$S/src/album/big.bin"
echo hello > "$S/src/album/disc1/track ä 01.txt"
echo cover > "$S/src/album/cover.jpg"

step status;            $H status
step "ls -l /";         $H ls -l /
step "put dir";         $H -q put "$S/src/album" "$T"
step tree;              $H tree "$T"
step "put again";       $H put "$S/src/album" "$T" | grep -q "nothing to do"
step get;               $H -q get "$T/album" -o "$S/dl"
same "$S/src/album/big.bin" "$S/dl/album/big.bin"
same "$S/src/album/disc1/track ä 01.txt" "$S/dl/album/disc1/track ä 01.txt"
step "resume";          rm "$S/dl/album/big.bin"; head -c 1000000 "$S/src/album/big.bin" > "$S/dl/album/big.bin.part"
                        $H -v get "$T/album/big.bin" -o "$S/dl/album" --overwrite always
same "$S/src/album/big.bin" "$S/dl/album/big.bin"
step du;                $H du "$T"
step find;              $H find "$T" --name '*.txt' -l
step "mv / cp";         $H mv "$T/album/cover.jpg" "$T/album/disc1"; $H cp "$T/album/disc1/cover.jpg" "$T/album/cover2.jpg"; $H ls -l "$T/album"
step "sync dry run";    rm "$S/src/album/cover.jpg"; $H sync -n --delete "$S/src/album" "$T/album"
step "sync --delete";   $H -q sync --delete -y "$S/src/album" "$T/album"; $H ls -R "$T"
step "sync --pull";     $H -q sync --pull --delete -y "$S/dl/album" "$T/album"; same "$S/src/album/big.bin" "$S/dl/album/big.bin"
step cat;               [ "$($H cat "$T/album/disc1/track ä 01.txt")" = "hello" ]
step "rm -r";           $H rm -r -y "$T"; ! $H stat "$T" 2>/dev/null

echo
echo "SMOKE OK"
