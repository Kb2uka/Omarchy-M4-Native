# Security

Report suspected vulnerabilities privately through
[Report a vulnerability](https://github.com/Kb2uka/Omarchy-M4-Native/security/advisories/new).
If that form is unavailable, contact the maintainer through the contact links on
[the maintainer profile](https://github.com/Kb2uka). Do not post passwords,
private keys, personal firmware, browser profiles or exploit payloads in issues.

This experimental project changes boot policy and installs privileged software.
The maintained development branch is `develop`; release artifacts are separately
qualified by the maintainer. A passing source scan does not establish hardware
safety, validate a downloadable installer, or guarantee the absence of malware.

Contributions require maintainer review and the checks described in
[CI and security](docs/ci-security.md). In particular, reviewers must inspect
new downloads, executable payloads, obfuscated code, install hooks, workflow
changes and changes to speaker protection. Unknown or deliberately concealed
malicious behavior may evade automated scanners.

Do not approve a fork workflow before reviewing its workflow and dependency
changes. Do not run unknown contributor code on a personal machine or the
hardware qualification fleet. Security suppressions need a specific documented
reason and maintainer approval; broad exclusions are not acceptable.
