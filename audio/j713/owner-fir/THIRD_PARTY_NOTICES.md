# Owner filter generator notices

The original generator and wrapper are Copyright (c) 2026 KB2UKA and licensed
under the MIT license in `LICENSE.txt`. The native helpers dynamically use macOS
system frameworks; those frameworks and the owner's tuning data are not copied
into this distribution. No license grant over Apple inputs or generated filter
assets is made here.

The development reproduction bundle uses the unmodified NumPy 2.5.3 CPython
3.13 macOS arm64 wheel identified by URL and SHA-256 in `manifest.json`. The
installer does not include it. NumPy uses BSD terms and
incorporates components with their own notices. All license files supplied in
that exact wheel are preserved in `licenses/` and remain inside the wheel.
Preserve this entire notice set when redistributing the bundle.

Primary upstream references:

- [NumPy 2.5.3 release metadata](https://pypi.org/pypi/numpy/2.5.3/json)
- [NumPy source](https://github.com/numpy/numpy)

These notices cover the generator and its development numerical dependency. They
do not resolve redistribution rights for the separately generated FIR files.
