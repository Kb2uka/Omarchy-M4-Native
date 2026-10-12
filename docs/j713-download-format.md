# J713 installer download transport

This transport reconstructs a frozen installer directory. It does not modify
an OS image, grant redistribution rights, install an OS, or run `Install.command`.
The only supported installation target remains the exact model described by
the enclosed installer. Downloading the files can happen on another machine.

The maintainer creates transport assets only after the installer and source
notices have their own distribution review. `KIT.json` and `IMAGE.json` must
explicitly have `private_only: false`; the packager refuses the old private kit.
That metadata check is a prerequisite, not evidence of a license grant or
hardware qualification. Transport never changes it or any other installer byte.

## Assets and pins

GitHub release assets must each be smaller than 2 GiB. The packager splits only
the large OS ZIP, defaulting to 1,900,000,000 bytes per part, and stores the other
files in one uncompressed regular-file USTAR companion. Already-compressed
source archives and the macOS framework bundle retain their bytes. A ZIP of
4,437,092,587 bytes would yield three parts of 1,900,000,000, 1,900,000,000 and
637,092,587 bytes. This is a sizing example, not approval of that private image.

For release tag `v0.1.0-alpha.1`, upload:

- `download-manifest.tsv`, the generated `download-j713.command`, and
  `DOWNLOAD-SHA256SUMS`;
- `omarchy-j713-v0.1.0-alpha.1-os.part001`, `.part002`, and so on;
- `omarchy-j713-v0.1.0-alpha.1-companion.tar`.

Publish the manifest SHA-256 and downloader SHA-256 in the trusted release
announcement. A manifest downloaded without an independently trusted pin does
not authenticate itself. The release downloader embeds the manifest pin and
release URL after the manifest is frozen. Running it without arguments selects
those exact files and the default Downloads directory; explicit options require
their own pin. It checks ordered part hashes and sizes, the concatenated ZIP hash, every
reconstructed file, and the original `SHA256SUMS` inventory before completion.
`DOWNLOAD-SHA256SUMS` covers all transport assets except itself. The returned
packaging receipt records every asset's size, mode and checksum, including that
checksum file. These generated files stay outside the kit, avoiding circular
checksums and preserving every installer byte.

The maintainer command, on a build host with room for another copy of the kit:

```sh
python3 tools/package-j713-download.py \
  --delivery /absolute/cleared-delivery \
  --kit-sha256 EXACT_KIT_SHA256 --checksums-sha256 EXACT_SUMS_SHA256 \
  --release-tag v0.1.0-alpha.1 --output /absolute/new-transport-directory
```

The output must be new. The delivery must contain only regular, single-link
files and ordinary directories, with a closed `KIT.json`/`SHA256SUMS` inventory.
There is no upload step. The release tag is an explicit argument and must agree
with the frozen metadata's version. The original OS ZIP name and all directory
paths are preserved, even if historical names contain `private`.

## Downloading

Use stock Bash and the system curl, tar, awk and SHA-256 utilities. No Python,
package installation or administrator access is required by the downloader.
The script uses Bash 3.2-compatible constructs; native macOS execution of this
new transport has not been performed. This is separate from the previous
macOS verification of the earlier private installer entrypoint.

The generated release asset supports the documented default:

```sh
bash "$HOME/Downloads/download-j713.command"
```

The repository template has no embedded release defaults. Its explicit form,
also accepted by the generated release copy, is:

```sh
bash download-j713.command \
  --base-url https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/v0.1.0-alpha.1 \
  --manifest-sha256 MANIFEST_SHA256_FROM_RELEASE \
  --output "$HOME/Downloads/Omarchy-M4-v0.1.0-alpha.1"
```

The HTTPS mode ignores `.curlrc`, is unauthenticated and follows HTTPS redirects only. A private
repository's inaccessible URL fails; that failure must not be described as a
public download pass. It never reads GitHub tokens or adds credentials to a URL.
For private authenticated staging, acquire the assets separately using the
maintainer's normal authenticated client, then perform local readback:

```sh
bash download-j713.command --asset-directory /absolute/downloaded-assets \
  --manifest-sha256 TRUSTED_MANIFEST_SHA256 --output /absolute/new-readback
```

Local mode explicitly reports that public HTTPS availability was not checked.
Neither mode starts the installer. On completion the original kit is in
`OUTPUT/installer/`; read `OUTPUT/installer/START-HERE.md`. Its `os/` path and
adjacent `installer_data.json`, framework archive, source and notices remain in
the same relative locations expected by `Install.command`.

The destination is reserved with an exclusive `mkdir`, mode 0700. Work happens
inside its private `.partial` directory; the verified kit is renamed to
`installer` only at completion. Existing output paths and symlink ancestors are
refused. Failed downloads remove their owned partial directory; a foreign file
placed in the output prevents removing that output directory. There is no resume
or adoption of an existing directory. Do not modify the destination concurrently.
Free space is checked conservatively for twice the original kit plus 1 GiB.

## Manifest grammar

`download-manifest.tsv` is ASCII, at most 128 KiB, LF-terminated, tab-separated.
Rows have this exact order; no comments, blank rows, duplicate paths or extra
columns are allowed:

```text
format<TAB>j713-download-v1
release<TAB>v0.1.0-alpha.1
archive<TAB>os/ORIGINAL_NAME.zip<TAB>BYTES<TAB>SHA256
companion<TAB>omarchy-j713-v0.1.0-alpha.1-companion.tar<TAB>BYTES<TAB>SHA256
part<TAB>1<TAB>omarchy-j713-v0.1.0-alpha.1-os.part001<TAB>BYTES<TAB>SHA256
file<TAB>IMAGE.json<TAB>644<TAB>BYTES<TAB>SHA256
```

There are 1–99 consecutive part rows, then lexically sorted file rows for every
original kit file, including the OS ZIP, `KIT.json` and `SHA256SUMS`. Part sizes
are equal except for a potentially shorter last part; their sum equals the ZIP
size. An asset is at most 2,147,483,647 bytes. Hashes are lowercase 64-digit
SHA-256. Modes are 600, 644, 700 or 755. Portable paths consist of nonempty
`[A-Za-z0-9_-][A-Za-z0-9._-]*` segments and are at most 240 characters. Thus there
are no absolute paths, dot segments, shell substitutions, whitespace or links.

Companion members must exactly equal the ordered non-ZIP file rows and all be
regular files. The downloader inspects the member list and types before
extracting into the private empty tree, then checks each resulting path, size
and digest and restores its listed mode. The packager writes normalized USTAR
headers with UID/GID/mtime zero; it does not reproduce another user's filesystem
ownership or include an outer symlink.

## Validation record

`tests/test_j713_download.py` contains synthetic offline packager and actual
shell-entrypoint cases for reconstruction, no installer launch, private-input
refusal, pins, missing/corrupt/oversized/out-of-order parts, final ZIP hash,
unsafe paths, links and conflicting output. It opens no network sockets.
These new tests have not been run: the maintainer explicitly stopped new test
runs during release preparation. New transport native macOS execution and
public unauthenticated download are also **NOT PERFORMED**. Earlier installer
validation is not evidence that this new transport has executed successfully.
