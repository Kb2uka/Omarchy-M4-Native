#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 studioxradio aka kb2uka
# Download and reconstruct files only. Never launch an installer or use sudo.
set -euo pipefail
export LC_ALL=C
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
unset TAR_OPTIONS TAPE
umask 077
fail() { printf '%s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'HELP'
Download an exact J713 installer kit without launching it.
The generated release copy needs no arguments and writes to
  $HOME/Downloads/Omarchy-M4-RELEASE_TAG
Explicit options use only the supplied values:
  bash download-j713.command --base-url HTTPS_RELEASE_DIRECTORY \
    --manifest-sha256 PIN --output NEW_DIRECTORY
Offline/private-auth readback (assets acquired separately):
  bash download-j713.command --asset-directory DIRECTORY \
    --manifest-sha256 PIN --output NEW_DIRECTORY
The manifest pin must come from the trusted release announcement. Read the
reconstructed installer/START-HERE.md before any installation action.
HELP
}
# Set by the transport packager only after the release manifest is frozen.
release_base=''
release_pin=''
release_tag=''
base=''; assets=''; pin=''; output=''
if (( $# == 0 )) && [[ -n "$release_tag" ]]; then
  base="$release_base"
  pin="$release_pin"
  output="${HOME:?HOME is required}/Downloads/Omarchy-M4-$release_tag"
fi
while (( $# )); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --base-url|--asset-directory|--manifest-sha256|--output)
      (( $# >= 2 )) || fail "Missing value for $1"
      case "$1" in
        --base-url) [[ -z "$base" ]] || fail 'Duplicate option'; base="$2" ;;
        --asset-directory) [[ -z "$assets" ]] || fail 'Duplicate option'; assets="$2" ;;
        --manifest-sha256) [[ -z "$pin" ]] || fail 'Duplicate option'; pin="$2" ;;
        --output) [[ -z "$output" ]] || fail 'Duplicate option'; output="$2" ;;
      esac
      shift 2 ;;
    *) fail "Unknown option: $1" ;;
  esac
done
[[ -n "$output" && "$pin" =~ ^[0-9a-f]{64}$ ]] || { usage >&2; exit 2; }
[[ -n "$base" && -z "$assets" || -z "$base" && -n "$assets" ]] || fail 'Select exactly one asset source'
if [[ -n "$base" ]]; then
  [[ "$base" =~ ^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~-]+)+$ ]] || fail 'Use an HTTPS release directory without credentials, query or fragment'
fi
no_links() {
  local p="$1"
  [[ "$p" == /* && "$p" != *$'\n'* && "$p" != *$'\t'* && "$p" != *$'\r'* ]] || fail 'Absolute ordinary paths required'
  [[ "$p" != */../* && "$p" != */./* && "$p" != */.. && "$p" != */. && "$p" != *//* ]] || fail 'Noncanonical path refused'
  while [[ "$p" != / ]]; do
    [[ ! -L "$p" ]] || fail 'Symlink path refused'
    p=${p%/*}; [[ -n "$p" ]] || p=/
  done
}
no_links "$output"
[[ ! -e "$output" && "$output" != */ ]] || fail 'Output must be a new directory'
parent=${output%/*}; [[ -n "$parent" ]] || parent=/
[[ -d "$parent" ]] || fail 'Output parent does not exist'
if [[ -n "$assets" ]]; then no_links "$assets"; [[ -d "$assets" ]] || fail 'Missing asset directory'; fi
if command -v shasum >/dev/null 2>&1; then
  sha() { shasum -a 256 "$1" | awk '{print $1}'; }
else
  command -v sha256sum >/dev/null 2>&1 || fail 'SHA-256 utility is missing'
  sha() { sha256sum "$1" | awk '{print $1}'; }
fi
bytes() { wc -c < "$1" | tr -d ' '; }
# mkdir reserves this destination atomically. All work stays in this private root;
# no pre-existing download or installer directory is adopted or overwritten.
mkdir -m 700 "$output" || fail 'Cannot reserve output directory'
work="$output/.partial"
complete=0
cleanup() {
  status=$?
  trap - EXIT
  if [[ -d "$work" && ! -L "$work" ]]; then rm -rf -- "$work"; fi
  if [[ "$complete" != 1 ]]; then rmdir "$output" 2>/dev/null || :; fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -m 700 "$work" "$work/kit"
fetch() {
  local name="$1" limit="$2" dest="$3" expected="${4:-}"
  [[ "$name" =~ ^[A-Za-z0-9_-][A-Za-z0-9._-]*$ ]] || fail 'Unsafe asset name'
  if [[ -n "$assets" ]]; then
    [[ -f "$assets/$name" && ! -L "$assets/$name" ]] || fail "Missing regular asset: $name"
    [[ $(bytes "$assets/$name") -le "$limit" ]] || fail "Oversized asset: $name"
    head -c "$((limit + 1))" "$assets/$name" > "$dest"
  else
    # head bounds even chunked responses. A larger response fails the byte check
    # or the pipeline; curl never follows an HTTP redirect or ignores HTTP errors.
    curl -q --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
      --connect-timeout 20 --max-time 7200 --url "$base/$name" |
      head -c "$((limit + 1))" > "$dest"
  fi
  [[ -f "$dest" && ! -L "$dest" && $(bytes "$dest") -le "$limit" ]] || fail "Asset size limit: $name"
  if [[ -n "$expected" ]]; then
    [[ $(bytes "$dest") -eq "$limit" && $(sha "$dest") == "$expected" ]] || fail "Asset hash/size mismatch: $name"
  fi
}
manifest="$work/download-manifest.tsv"
fetch download-manifest.tsv 131072 "$manifest"
[[ $(sha "$manifest") == "$pin" ]] || fail 'Transport manifest SHA-256 mismatch'
[[ $(tr -d '\11\12\40-\176' < "$manifest" | wc -c) -eq 0 ]] || fail 'Non-ASCII manifest'
[[ $(tail -c 1 "$manifest" | od -An -tu1 | tr -d ' \n') == 10 ]] || fail 'Manifest must end with LF'
# Parse only this closed TSV grammar. No manifest field becomes shell code.
awk -F '\t' '
function bad() { failed=1; exit 1 }
function hash(s) { return length(s)==64 && s !~ /[^0-9a-f]/ }
function num(s) { return s ~ /^(0|[1-9][0-9]*)$/ && length(s)<=12 }
function path(s) { return length(s)<=240 && s ~ /^[A-Za-z0-9_-][A-Za-z0-9._-]*(\/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$/ }
function asset(s) { return path(s) && s !~ /\// }
NR==1 { if(NF!=2 || $1!="format" || $2!="j713-download-v1") bad(); next }
NR==2 { if(NF!=2 || $1!="release" || $2!~/^v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.-]+)?$/) bad(); tag=$2; next }
NR==3 { if(NF!=4 || $1!="archive" || !path($2) || $2!~/^os\/[^\/]+\.zip$/ || !num($3) || $3<1 || !hash($4)) bad(); archive=$2; size=$3; pin=$4; next }
NR==4 { if(NF!=4 || $1!="companion" || $2!="omarchy-j713-" tag "-companion.tar" || !num($3) || $3<1 || $3>2147483647 || !hash($4)) bad(); companion=$2; next }
$1=="part" {
  if(phase || NF!=5 || !num($2) || $2!=++parts || parts>99 || !asset($3) ||
     $3!="omarchy-j713-" tag "-os.part" sprintf("%03d",parts) ||
     !num($4) || $4<1 || $4>2147483647 || !hash($5)) bad();
  if(parts==1) partsize=$4;
  if(parts>1 && previous!=partsize) bad();
  if($4>partsize) bad(); previous=$4; total+=$4; next
}
$1=="file" {
  phase=1;
  if(!parts || NF!=5 || !path($2) || seen[$2]++ || ("x" $2)<=("x" last) ||
     (last!="" && index($2,last "/")==1) ||
     $3!~/^(600|644|700|755)$/ || !num($4) || !hash($5) || ++files>512) bad();
  last=$2;
  if($2==archive) { if($4!=size || $5!=pin) bad(); found=1 }
  else if($4>2147483647) bad();
  if($2=="KIT.json" || $2=="SHA256SUMS" || $2=="Install.command" || $2=="IMAGE.json" || $2=="installer_data.json") required++;
  allbytes+=$4; next
}
{ bad() }
END { if(failed || NR<10 || !found || required!=5 || !parts || total!=size || allbytes>200000000000) exit 1 }
' "$manifest" || fail 'Malformed transport manifest'
release=$(awk -F '\t' '$1=="release"{print $2}' "$manifest")
[[ -z "$base" || "$base" == */"$release" ]] || fail 'Release URL/tag mismatch'
archive=$(awk -F '\t' '$1=="archive"{print $2}' "$manifest")
archive_size=$(awk -F '\t' '$1=="archive"{print $3}' "$manifest")
archive_pin=$(awk -F '\t' '$1=="archive"{print $4}' "$manifest")
companion=$(awk -F '\t' '$1=="companion"{print $2}' "$manifest")
companion_size=$(awk -F '\t' '$1=="companion"{print $3}' "$manifest")
companion_pin=$(awk -F '\t' '$1=="companion"{print $4}' "$manifest")
needed=$(awk -F '\t' '$1=="file"{n+=$4} END{printf "%.0f", n*2+1073741824}' "$manifest")
available=$(df -Pk "$output" | awk 'END{printf "%.0f", $4*1024}')
[[ "$available" =~ ^[0-9]+$ && "$available" -ge "$needed" ]] || fail 'Insufficient space for download and reconstruction'
mkdir -m 700 "$work/kit/os"
: > "$work/kit/$archive"
while IFS=$'\t' read -r kind index name size expected; do
  [[ "$kind" == part ]] || continue
  fetch "$name" "$size" "$work/part" "$expected"
  cat "$work/part" >> "$work/kit/$archive"
  rm "$work/part"
done < "$manifest"
[[ $(bytes "$work/kit/$archive") -eq "$archive_size" && $(sha "$work/kit/$archive") == "$archive_pin" ]] || fail 'Reassembled OS ZIP mismatch'
fetch "$companion" "$companion_size" "$work/companion.tar" "$companion_pin"
awk -F '\t' -v archive="$archive" '$1=="file" && $2!=archive{print $2}' "$manifest" > "$work/expected-members"
tar -tf "$work/companion.tar" > "$work/members"
cmp -s "$work/expected-members" "$work/members" || fail 'Companion member set/order mismatch'
# The packager writes regular USTAR members only. Refuse links, devices,
# directories and any other type before extraction into the empty private tree.
tar -tvf "$work/companion.tar" > "$work/member-types"
awk 'substr($0,1,1)!="-"{exit 1}' "$work/member-types" || fail 'Nonregular companion member'
[[ $(wc -l < "$work/member-types") -eq $(wc -l < "$work/expected-members") ]] || fail 'Ambiguous companion member types'
tar -xf "$work/companion.tar" -C "$work/kit" --no-same-owner --no-same-permissions
while IFS=$'\t' read -r kind name mode size expected; do
  [[ "$kind" == file ]] || continue
  no_links "$work/kit/$name"
  [[ -f "$work/kit/$name" && $(bytes "$work/kit/$name") -eq "$size" && $(sha "$work/kit/$name") == "$expected" ]] || fail "Reconstructed file mismatch: $name"
  chmod "$mode" "$work/kit/$name"
done < "$manifest"
awk -F '\t' '$1=="file" && $2!="SHA256SUMS"{print $5 "  " $2}' "$manifest" > "$work/expected-sums"
cmp -s "$work/expected-sums" "$work/kit/SHA256SUMS" || fail 'Original checksum inventory differs'
[[ ! -e "$output/installer" && ! -L "$output/installer" ]] || fail 'Installer destination appeared during download'
mv "$work/kit" "$output/installer"
complete=1
printf 'Verified installer files: %s/installer\n' "$output"
printf 'Release: %s; manifest SHA-256: %s\n' "$release" "$pin"
if [[ -n "$assets" ]]; then
  printf '%s\n' 'Source: local assets. Public HTTPS availability was not checked.'
else
  printf '%s\n' 'Source: unauthenticated HTTPS release assets.'
fi
printf '%s\n' 'Nothing was installed. Read installer/START-HERE.md before proceeding.'
