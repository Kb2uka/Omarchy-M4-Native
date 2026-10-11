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
a browser lock. It also refuses another registration, a changed component, or
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
