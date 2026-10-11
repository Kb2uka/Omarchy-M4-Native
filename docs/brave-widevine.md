# Optional Widevine setup for the desktop owner

Widevine is a proprietary browser component used by some licensed streaming
services. It is **not included in this repository or installer kit**. Hardware
video decoding does not install Widevine, and protected content may still use
software decoding. This tool does not change DRM or licensing restrictions.

On Linux ARM64, first start the intended Brave profile normally, then fully exit
**all Brave processes owned by your account**. Keep them closed until setup
finishes. Run the tool as your desktop user, without `sudo`, choosing one profile:

```sh
python3 tools/setup-brave-widevine.py --profile Brave-Browser
```

For the separate experimental hardware-browser profile:

```sh
python3 tools/setup-brave-widevine.py --profile Brave-Browser-M4
```

Before the first launch, an explicit initialization command can instead prepare an
absent or completely empty selected profile:

```sh
python3 tools/setup-brave-widevine.py --profile Brave-Browser --initialize --timeout 15
```

The same options support `Brave-Browser-M4`. Initialization never accepts a populated
profile without `Local State`. The component must validate successfully and Brave
must still be closed before the helper creates minimal profile state. An offline
or rejected download leaves a missing profile and `Local State` absent. Without
`--initialize`, missing owner state remains an error. Existing profiles keep their
unrelated preferences even when this option is supplied.

`--timeout` accepts 1–180 seconds and defaults to 180. It bounds the network transfer,
including blocking connection and read operations, with socket and Linux process
timers plus monotonic checks. It is not a bound on all local validation or disk
operations. The helper is a standalone Linux command and refuses an already active
process timer. A failed setup exits nonzero; a first-launch caller may leave its
retry marker and continue launching Brave for clear content. This helper neither
creates that marker nor retries or launches the browser itself.

These commands are run from the repository checkout. First-owner integration is
separate; this source change does not install a service, run automatically, change
the default browser, restart Brave or modify decoder settings. Start Brave normally
after the tool reports success. Streaming availability remains subject to the
provider and your account.

## What the tool changes

The tool downloads approximately 20 MB from a fixed Google HTTPS URL and requires
the exact byte count and SHA-512 for Linux ARM64 Widevine **4.10.3057.0**. It rejects
redirects, unexpected archive members, duplicate paths, links and special files.
It validates the four files individually, checks the unchanged upstream manifest
and ARM64 ELF identity, and preserves `LICENSE` and signed verification metadata.
No component library is patched or executed by setup.

The component is stored under
`$XDG_DATA_HOME/omarchy/widevine/4.10.3057.0`, or
`~/.local/share/omarchy/widevine/4.10.3057.0` when that variable is unset. It registers
that directory in the selected profile's
`WidevineCdm/latest-component-updated-widevine-cdm` file and sets only the JSON
preference `brave.widevine_opted_in` to `true`. Before changing `Local State`, it
retains an owner-private `Local State.before-widevine-*` copy. Unrelated preference
values are preserved; JSON whitespace may change. A completed repeat validates
the installed files and makes no changes or new download.

`Brave-Browser` uses `$XDG_CONFIG_HOME/BraveSoftware/Brave-Browser`, defaulting to
`~/.config/BraveSoftware/Brave-Browser`. `Brave-Browser-M4` uses
`~/.config/BraveSoftware/Brave-Browser-M4`, matching the hardware launcher even if
`XDG_CONFIG_HOME` is set. Config/data locations must be real owner-controlled paths
inside your home. Symlinked, foreign-owned or group/world-writable paths are refused.

The helper refuses a live same-owner Brave executable or a selected profile's
`SingletonLock`, including a dangling lock. It never kills a process or deletes
a browser lock. An unreadable executable link on an unrelated non-dumpable owner
service, such as `systemd --user`, is not evidence of a live browser. Process name
and the first command token also identify Brave candidates; an unreadable Brave
candidate is refused. This check is not a boundary against a malicious process
running as the same owner. It also refuses another registration, a changed component, or
preferences changed during download. Those cases require resolving the reported
conflict; setup does not overwrite the owner's existing component choice. If a
late refusal leaves the verified component or registration present, a later run
can reuse them after the browser is closed. No other profile is changed.

## Provenance and validation boundary

The acquisition/registration method follows
[Brave's ARM64 Linux instructions](https://support.brave.app/hc/en-us/articles/23881756488717-How-do-I-enable-Widevine-DRM-on-Linux).
The exact Google component URL, size and SHA-512 were published in
[Mozilla bug 1612995, comment 17](https://bugzilla.mozilla.org/show_bug.cgi?id=1612995#c17).
The archive's size and hash were independently matched before these constants
were recorded. The tool pins this version; it is not an automatic component
updater or permission to redistribute Google's binary.

Focused regression tests exercise the CLI with small synthetic archives, mocked
HTTPS responses and process records; they do not start a browser or contact a
streaming service. Successful setup is registration evidence, not a guarantee of
protected playback or hardware decoding on a particular service.

```sh
python3 -m unittest discover -s tests -p 'test_setup_brave_widevine.py' -v
```

Development tests for this repository run on the shared remote test hosts.

## Consented first-owner bootstrap

`bootstrap-brave-widevine.py` is an optional owner-side entry for an installer that
has **already obtained that owner's choice**. Brave describes Widevine as disabled
by default and explains that allowing it accepts Google's terms; see
[Brave's DRM explanation](https://support.brave.app/hc/en-us/articles/360023851591-How-do-I-view-DRM-protected-content).
A future public image must not seed this request for every owner without that
choice. The private requested install may record the owner's prior authorization.
No component binary is bundled, and this does not promise all streaming services
or hardware decoding of protected content.

Proposed installed paths (both regular root-owned files, mode0644):

- `/usr/lib/omarchy/bootstrap-brave-widevine.py`
- `/usr/lib/omarchy/setup-brave-widevine.py`

Call as the desktop owner, before the browser launcher execs the actual browser:

```sh
/usr/bin/python3 /usr/lib/omarchy/bootstrap-brave-widevine.py --profile Brave-Browser
```

The caller must keep launching Brave on a nonzero bootstrap result. It must skip
bootstrap for help/version queries and custom `--user-data-dir` selections. The
separate supported `Brave-Browser-M4` name has its own request. The helper never
stops or starts a browser; closure is checked by the setup helper. A waiting Python
launcher named `brave` is not the Brave binary when its readable executable proves
Python. An unreadable Brave candidate still refuses.

For the standard profile, the explicit pending request is
`~/.config/omarchy/pending/widevine-Brave-Browser.json`, containing:

```json
{"schema":1,"profile":"Brave-Browser","consent":"owner-requested"}
```

For the M4 profile, use `widevine-Brave-Browser-M4.json` with the matching profile
value. Keep the marker outside the browser profile. Its directories and file must
be real owner-controlled paths, without group/world write permissions. No marker
means no setup, no network and no new state for an existing owner. An invalid
marker refuses; it never implies consent. The fixed marker location does not
follow `XDG_CONFIG_HOME`; selected profile resolution still follows the setup
helper's documented rules.

The bootstrap acquires a nonblocking owner lock shared by both profile requests,
then calls the setup helper with `--initialize --timeout 15`. The direct child has
a 20-second outer lifetime bound. Failure, offline status, another setup, or a live
Brave leaves the request pending and returns 1 with a retry hint. There is no
background retry or network-connectivity service. On success only the exact,
unchanged request is removed. Later launches do not invoke the download helper.
The private lock file remains for future requests; it contains no credentials.

For an offline private image, the existing `prepare-image.py` boundary already
requires a loop-backed, owner-free pending image. That is the appropriate place to
install the two helper files and seed an explicitly authorized request under
`/etc/skel/.config/omarchy/pending/`, so account creation copies it to the actual
owner. The recorded `omarchy-provision-user` source leaves shipped configurations
to skel and performs user setup before default-browser finalization. This change
adds the callable entry and tests only: image seeding and launcher wiring remain
separate integration work. Config resync must not recreate a consumed consent
request or override an owner's later Widevine choice.

Run both focused suites on a remote development test host:

```sh
python3 -m unittest discover -s tests -p 'test_*brave_widevine.py' -v
```

## Chromium on the shipped ARM64 image

The shared helper also supports the shipped Chromium153 build:

```sh
python3 tools/setup-brave-widevine.py --profile Chromium
```

For an explicitly authorized first profile, add `--initialize --timeout 15`.
The compatibility filenames remain `setup-brave-widevine.py` and
`bootstrap-brave-widevine.py`; neither means Chromium uses Brave's profile or
preference scheme. Chromium's selected directory is `$XDG_CONFIG_HOME/chromium`,
defaulting to `~/.config/chromium`. A custom `CHROME_CONFIG_HOME` or
`CHROME_USER_DATA_DIR` environment is refused rather than registering the wrong
profile. Custom command-line user-data directories still require manual setup.

Chromium reads the same `WidevineCdm/latest-component-updated-widevine-cdm`
`{"Path":"..."}` hint. The setup helper registers the shared verified component
but does **not** add `brave.widevine_opted_in` or rewrite existing Chromium
`Local State`, preferences, flags or protected-content choices. For an absent or
empty initialized Chromium profile it creates only minimal `{}` Local State and
the component hint. A conflicting existing component registration is preserved
and refused. Same-owner Chromium processes and its SingletonLock must be absent;
a live Brave process alone does not prevent Chromium registration.

The bootstrap command accepts `--profile Chromium` and uses the independent
`~/.config/omarchy/pending/widevine-Chromium.json` prior-consent request:

```json
{"schema":1,"profile":"Chromium","consent":"owner-requested"}
```

The same consent, failure, concurrency and retry rules apply. Both browsers share
one verified component cache; completing the other browser's registration does
not download it again. There is no generic Chrome or Firefox support claim:
neither is installed in the audited image. Other Chromium versions/builds require
checking that library CDMs and Widevine component registration are enabled.

This contract follows Chromium153's
[Linux hint-file implementation](https://github.com/chromium/chromium/blob/153.0.8010.36/chrome/common/media/component_widevine_cdm_hint_file_linux.cc),
[profile-relative path](https://github.com/chromium/chromium/blob/153.0.8010.36/chrome/common/chrome_paths.cc),
[Linux profile selection](https://github.com/chromium/chromium/blob/153.0.8010.36/chrome/common/chrome_paths_linux.cc)
and [CDM registration](https://github.com/chromium/chromium/blob/153.0.8010.36/chrome/common/media/cdm_registration.cc).
Linux resolves the CDM before its zygote sandbox starts, so a later registration
requires a normal full browser restart. A valid marker, source support or library
file alone is not proof of EME negotiation, licensed playback or hardware decoding;
those require separate native verification. No DRM policy is bypassed.
