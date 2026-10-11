# Contributing

Omarchy M4 Native is maintained by **studioxradio aka kb2uka**
([Kb2uka](https://github.com/Kb2uka)). It is experimental on the M4 Air and
very experimental on other M4 machines. Only the 13-inch M4 Air, Mac16,12 /
J713, has this project's tested installer profile.

## Branch policy

**All contributor pull requests must target `develop`.** This includes fixes,
features, documentation and packaging changes. Create your working branch from
the latest `develop` and select `develop` as the pull request's base branch.

`main` is reserved for releases. Only the maintainer, **studioxradio aka kb2uka**,
promotes a validated `develop` revision to `main` for a release. Contributors
must not open pull requests against `main` or push changes directly to it.
Retarget a contribution opened against the wrong branch to `develop` before
review.

Merging a contribution into `develop` does not publish an installer, tag a
release or qualify another Mac model. Release promotion and publication remain
the maintainer's decisions.

## Preparing a contribution

- Keep the change focused and explain the problem and resulting behavior.
- For hardware changes, identify the exact model, board, kernel and relevant
  userspace versions. Distinguish source inspection, offline tests and actual
  hardware observations.
- Include relevant test results and before/after evidence. Run `git diff --check`
  and the checks that exercise the changed path. State checks you could not run.
- Preserve existing copyright and license notices, identify imported code and
  provide corresponding source where its license requires it.
- Keep credentials, personal browser data, machine identities, personalized
  firmware and raw session transcripts out of commits and pull requests. Use
  concise, redacted evidence.

Fresh installation, repeat boot and hardware behavior must be verified on the
supported target before they are described as working. A passing offline check
does not establish support for another board, codec or power state.
