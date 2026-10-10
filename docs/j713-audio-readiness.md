# Experimental J713 audio readiness and startup

The helper and unit templates support the J713/T8132 protected audio package.
The pinned runtime completed automatic startup on the qualified J713 installation;
see [the qualification record](j713-audio.md). Adding these sources to a checkout
does not install files, enable units, load modules or restart services. The
registration helper and offline image installer are included in the same source
package. Kernel lease enforcement remains the
output boundary; readiness can be revoked immediately after a successful check.

## Package paths and ordering

| Source | Installed path |
| --- | --- |
| `tools/m4-j713-audio-ready.py` | `/usr/lib/omarchy/m4-j713-audio-ready.py` |
| `packaging/j713-audio/speakersafetyd-j713.service` | `/usr/lib/systemd/system/speakersafetyd-j713.service` |
| `packaging/j713-audio/wireplumber.service.d/80-j713-audio-ready.conf` | `/etc/systemd/user/wireplumber.service.d/80-j713-audio-ready.conf` |

The protection system service Requires/After `m4-j713-audio-register.service`.
Registration owns the cold-boot platform/provider/overlay/card checks and its
one-attempt latch. Before hardware mutation it calls the readiness helper's
root-only `invalidate` action. That action needs no functioning daemon, manifest
or module, and is idempotent when the dedicated runtime directory is absent.

Protection keeps the qualified daemon executable/config arguments, `Type=simple`,
`Restart=no`, limits and hardening. ExecStartPre invalidates any old record.
ExecStartPost runs root startup qualification and publishes on success.
ExecStopPost invalidates on normal stop or failed startup. A failed ExecStartPost
causes systemd to fail/stop that protection invocation; it never retries hardware.
The service is WantedBy multi-user.target, not a required dependency of login or
the display manager. Audio failure leaves the desktop usable, with audio unavailable.

The user WirePlumber drop-in calls the ordinary-user `gate` explicitly. A system
`Before=wireplumber.service` would not order that separate user manager. The drop-in
preserves WirePlumber's existing restart policy; its retries can only observe
state, not start/restart or unlock protection. Both templates use bounded startup
timeouts. A gate failure stops that WP start, not the desktop or protection daemon. It
blocks WirePlumber (the PipeWire session manager) as a whole, so other audio
devices it manages, including USB and Bluetooth, are also unavailable while
readiness fails. Other PipeWire media that depend on WirePlumber may also be
affected; camera and portal screen-sharing behavior has not been tested here.
If the existing WirePlumber restart policy retries, each gate attempt remains
read-only and bounded; this package does not cap the number of manager retries.
Only the J713 package should install this board-specific drop-in; no other board
or generic desktop policy is changed by merely storing these source templates.

## Root publication and cleanup

```sh
python3 /usr/lib/omarchy/m4-j713-audio-ready.py invalidate
python3 /usr/lib/omarchy/m4-j713-audio-ready.py startup --manifest /etc/omarchy/j713-audio-manifest.json --publish
```

The dedicated leaf is `/run/omarchy/j713-audio-readiness`, root-owned0755. Systemd's
`RuntimeDirectory=omarchy/j713-audio-readiness` makes it writable under
`ProtectSystem=strict` and removes that leaf after stop, preserving the sibling
registration latch. RuntimeDirectory removes only innermost directories, not the
shared parent ([systemd.exec](https://raw.githubusercontent.com/systemd/systemd/v260/man/systemd.exec.xml)).

The helper validates each parent before creating a missing component, refuses
symlinks/writable parents, and uses a root-owned0600 regular single-link flock
file. A new publication attempt removes the previous ready record and atomically
writes a unique attempt token. Qualification runs outside the short mutation
lock. Stop/registration invalidation removes both record and token; an old or
superseded attempt cannot later publish or remove a newer attempt's record.

On success it rechecks daemon identity, module notes and the current MUTE lease,
then writes and fsyncs a complete JSON temporary file, atomically replaces
`ready.json` and fsyncs its directory. The record is root-owned0644. Temporary
files are removed on failure. Ordinary exceptions during startup invalidate only
that attempt. ExecStopPost and RuntimeDirectory cleanup cover process termination;
the helper does not claim Python cleanup can run after SIGKILL. No manual shell
redirection over the final record is required. The `startup` command without
`--publish` remains an observation-only root diagnostic.

## Live checks and bounded waits

Startup pins the first live daemon PID/start/invocation and boot. It permits a
maximum30-second initialization wait for expected phase0/1 or phase2 before the
first lease, then requires at least 15 seconds of healthy MUTE, closed TX,
RUNNING RX and advancing unexpired leases. Phase2 with sequence0 uses the bounded
2-second configuration deadline; it is pending, never ready. After the first
renewal, the 50 ms lease horizon applies. Clean ownerless LOW may retain a prior
generation and cancelled=1; fresh GPIO LOW verification and closed TX are required
while waiting for the same daemon to establish a new owned generation.
Faults, failed services, ownership cancellation, identity changes and malformed
state refuse. Torn software status gets at most six reads10ms apart. Individual
systemctl calls are bounded to2s; `TimeoutStartSec=65` also bounds the complete
operation, including package hashing.

The user gate command is:

```sh
python3 /usr/lib/omarchy/m4-j713-audio-ready.py gate --manifest /etc/omarchy/j713-audio-manifest.json --ready /run/omarchy/j713-audio-readiness/ready.json
```

It first checks installed static payload/platform identity. If registration has
not loaded modules yet, it waits for the intended system protection job before
requiring exact loaded notes. Once a main PID exists it is pinned; mismatched
live modules fail. The gate waits at most30s for completed system startup and its
root record, then observes the live lease for at least1s. A present stale record
refuses. MUTE/closed TX and ACTIVE/RUNNING TX are accepted; other phase/PCM states
fail. RX must belong to the daemon and be RW_INTERLEAVED,S16_LE,8channels,48kHz,
period1024/ring2048. Epoch, error/fault/cancel/removal and lease progress/expiry
are checked independently of the record. No codec I2C/ALSA control enumeration
or device open occurs; the kernel group status itself reads GPIO/software state.

Root startup checks `/proc/PID/exe` path and bytes. The user gate cannot normally
read that root-only link: it binds the root-attested executable to the same live
PID/start/invocation and current installed hashes/module notes. This assumes the
qualified daemon remains in that executable; the reviewed v4 J713 loop has no
self-exec path. A future self-exec implementation needs contract review. This is
not protection against a hostile privileged actor changing the process.

## Shared manifest schema1

`/etc/omarchy/j713-audio-manifest.json` must be a root-owned regular JSON file
beneath root-owned, non-group/world-writable directories, without symlinks.
Maximum JSON size is256KiB. Required readiness fields:

```json
{
  "schema": 1,
  "service": "speakersafetyd-j713.service",
  "kernel_release": "EXACT_QUALIFIED_KERNEL_RELEASE",
  "files": {
    "/usr/lib/speakersafetyd-j713": "64_lowercase_hex_sha256",
    "/usr/share/speakersafetyd/apple/j713.conf": "64_lowercase_hex_sha256"
  },
  "modules": {
    "apple_admac": "full_raw_note_bytes_as_hex",
    "snd_soc_apple_mca": "full_raw_note_bytes_as_hex",
    "snd_soc_macaudio": "full_raw_note_bytes_as_hex",
    "snd_soc_tas2764": "full_raw_note_bytes_as_hex",
    "snd_soc_j713_group": "full_raw_note_bytes_as_hex",
    "j713_audio_bootstrap": "full_raw_note_bytes_as_hex",
    "j713_audio_dt3": "full_raw_note_bytes_as_hex"
  }
}
```

All seven modules must be live. Include every accepted installed graph, FIR, UCM,
policy, contract, helper and service file in `files`, not merely the mandatory
daemon/profile entries above. The package builder owns manifest completeness.
Additional registration fields such as package identity and `module_files` are
allowed; readiness does not interpret them. There is no installable private
native manifest in this source change. The record binds current boot, invocation,
PID/start, generation and manifest digest; it is never permission by itself.

## Qualification limits

Tests use the actual CLI logic and captured native PCM text in a temporary
filesystem, with mocked system-manager/time/ownership boundaries. They prove
failure handling and startup publication mechanics. The separate native evidence
records one automatic boot with exact payloads and module notes; neither that
boot nor these tests establish sound calibration, suspend recovery or broad
endurance. Keep
fatal faults latched: automatic recovery needs a separately qualified fresh-owner
path preserving thermal state. This change does not make protection Restart=always.
