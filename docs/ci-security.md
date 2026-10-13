# Public contribution checks

Contributor PRs target `develop`. Only KB2UKA promotes `develop` to `main` for
releases. Neither a PR merge nor CI automatically publishes an installer.

## Where checks run and cost

All workflows use **standard GitHub-hosted runners** (`ubuntu-24.04` and
`macos-14`). No self-hosted runner, hardware access, personal login, repository
secret or paid scanning service is required. Standard hosted runners are free
for public repositories. While private, ordinary Actions use the account's
private-repository allowance; CodeQL and dependency review explicitly wait for
public visibility. Skipped private-only checks are not security qualification.

See [GitHub runner pricing](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job).

## Checks

| Check | What it covers |
| --- | --- |
| PR policy | Contributor target is `develop`; maintainer release PRs are `develop` to `main` in this repository. |
| Tests (Linux) | Offline Python regressions and the Lua policy fixture. No image installation, speaker pulse or hardware reboot. |
| Speaker daemon (Rust) | Verifies every hash in `audio/j713/imported-source.sha256`, then builds and unit-tests `speakersafetyd` with its locked dependencies. No audio device or speaker is touched. |
| Installer transport (macOS) | Downloader and packaging regressions on macOS, including its shell and archive tools. |
| Package channel | Builds the M4 pacman packages with makepkg, checks every file against the channel allowlist and protected paths, and refuses changed contents under an already-published file name. It then installs them with real pacman into a sandboxed alpha.1-style root, signed by a throwaway key, and runs the setup script twice. Protected stand-ins and `boot.bin` must not change, and pacman must refuse a database signed by an untrusted key. See [package-channel.md](package-channel.md). |
| Secrets | Gitleaks default rules across fetched history and current files; supported encoded/archive content is also scanned. Findings are redacted. |
| Malware | Updated ClamAV definitions; every tracked regular file, including archives. Detections, scanner errors, encrypted archives and scan-limit exhaustion fail. Git export exclusions cannot hide files. Symlinks, submodules, oversized files and unresolved LFS pointers require review. |
| Workflow lint | Actionlint plus security-policy assertions and negative tests for unsafe workflow changes. |
| Dependency review | On public PRs, block newly introduced moderate-or-higher advisories in supported dependency manifests, including development and unknown scopes. |
| CodeQL | Python, C/C++, Rust and Actions source with extended security queries; public PRs, branch pushes and weekly scans. No repository build scripts are executed in the scanner job. |
| Dependabot | Weekly GitHub Actions update PRs into `develop`; updates are reviewed, never automatically merged. The speaker daemon's `Cargo.lock` and the owner-filter numpy wheel are hash-pinned parts of the qualified audio payload, so they get no automatic version-update PRs: changing them means re-pinning and requalifying the audio build. |

CodeQL does not parse kernel patches as complete kernel translation units or
qualify hardware. Its build-free C/C++ analysis can miss context supplied by
external kernel headers. Dependency review covers supported manifests, not
arbitrary URLs or vendored source. ClamAV detects known signatures and heuristics;
it cannot prove source code contains no malicious logic. Gitleaks cannot recognize
every credential format. Downloaded release binaries and installer images need
separate artifact checks and physical acceptance; source CI does not scan those
external release assets.

## Trust boundaries

Fork PRs use `pull_request`, never privileged `pull_request_target`. The token is
read-only, checkout credentials are not persisted, actions are pinned by full
commit SHA, and scanner binaries have fixed SHA-256 checksums. Only the CodeQL
job requests the permission needed to upload security results.

The single exception is the `publish` job in `packages.yml`. It runs only for
pushes and manual runs on `main`, after the package checks pass, inside the
`package-publish` environment (deployments restricted to `main`). It alone
receives write access and the package signing key (`PACKAGE_SIGNING_KEY`). It
signs packages and the repository database and publishes them to the `packages`
release. The workflow policy and its negative tests reject a secret, write
access or that environment anywhere else. No workflow publishes an installer.

External contributors require maintainer approval to run workflows. Review the
workflow, build scripts and dependencies **before** approving a run. CODEOWNERS
assigns every contribution to KB2UKA. Protection must require that review and
dismiss it after changes; a contributor can alter workflow source and tests,
so their own green checks alone are never approval. Review new executables,
network destinations, obfuscation and changes to safety protections manually.

## Activation before opening contributions

The repo remains private until the maintainer explicitly authorizes publication.
GitHub Free does not enforce branch rules on this private repo. The setup helper
prints the exact configuration without changing anything by default:

```sh
python3 tools/ci/configure-public-security.py --gh /absolute/path/to/gh
```

`--apply-settings` sets read-only workflow tokens, SHA pinning, an official-action
allowlist and dependency alerts. It
never changes visibility. Once the owner makes the repo public, run
`--activate-public` to require the CI checks, CODEOWNER review and CodeQL results,
require approval for all external contributors, restrict `develop` and `main`
updates to KB2UKA,
enable push protection/private vulnerability
reports, and verify the settings by reading them back. No other settings or
rulesets are replaced. This helper must run from a reviewed checkout with the
owner's authenticated CLI, never inside an untrusted PR workflow.

The public activation refuses to run until these workflow files and CODEOWNERS
are present on both protected branches. First run the workflows on each branch
and inspect the Security tab; resolve findings before release. A successful
scan is not a substitute for successful enforcement. Confirm a fork PR runs only
after approval and cannot merge with a failed required check or missing review.

The required status names are recorded in the setup helper. CodeQL merge rules
block security alerts at medium or higher, and non-security errors. KB2UKA can
bypass review rules **through a PR** for maintainer-authored changes; using that
exception requires reviewing the actual results. Ordinary contributors cannot
bypass. The separate "Maintainer merges only" update rule reserves updates to
`develop` and `main`, including release promotion, to KB2UKA.
