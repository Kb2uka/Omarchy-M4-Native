# Repository contribution instructions

Read [CONTRIBUTING.md](CONTRIBUTING.md) before preparing a contribution.

## Attribution

For every contributor and every pull request, do not name or attribute work to
AI assistants, models, tools or vendors in PR titles, descriptions, comments,
reviews, commit messages, author credits or co-author trailers. Do not add
generated-by signatures or automated assistant credits. Credit the actual
human contributors only. Check outgoing PR text and commit metadata before
submitting or updating them.

Preserve required upstream copyright and license notices. This rule does not
authorize removing or replacing another author's credit.

## Branches and validation

- Branch from `develop` and target contributor PRs at `develop`.
- Only the maintainer promotes validated changes to `main` for releases.
- Keep changes focused, run `git diff --check` and relevant checks, and report
  only results actually observed. Identify unverified hardware behavior.

## Updates

Every update targets the `omarchy-m4-update` channel. Owners get fixes and new
features only by running `omarchy-m4-update`: no opt-in packages, manual
`pacman -S` steps or other install paths. New packages go in `channel.json`
and the setup script's package list, and the next `omarchy-m4-boot` depends on
them. See "M4 packages" in [CONTRIBUTING.md](CONTRIBUTING.md).

Maintainer: **studioxradio aka kb2uka**.
