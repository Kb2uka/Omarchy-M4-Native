# Updates for the experimental M4 build

**v0.1.0-alpha.1 has no coordinated M4 update channel.** Keep its working stack
until a release explicitly provides an upgrade path. The normal Omarchy update
command can upgrade distribution and desktop packages, but it does not currently
coordinate this project's custom kernel, boot payload, Mesa, decoder and audio
components. Compatibility after a general rolling update has not been qualified.

The image's package configuration does not pin these components as a coordinated
set. `HoldPkg` is not version pinning. Do not treat a successful update command
or a repository merge as proof that the resulting M4 stack works. Do not install
isolated older core libraries to undo an update.

Releases are published from `main`; contributions target `develop`. Pushing to
either branch does **not** update installed machines. The versioned installer
and its hashes remain tied to their release, rather than following moving main.

Future releases will state whether they offer an in-place update, its supported
starting versions and recovery steps, or require another installation. A signed
package channel and qualified Omarchy integration are open work. Until then,
follow release notes and keep a recoverable copy of your working installation.
No automatic M4 update command is promised by this first alpha.
