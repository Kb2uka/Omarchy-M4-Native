# J713 suspend (s2idle) findings

Suspend is not qualified on the J713. This page records what blocks it, from a
staged investigation on the owner's Air on 2026-10-22 ([#92](https://github.com/Kb2uka/Omarchy-M4-Native/issues/92)).
Kernel `7.1.9-m4-gpu-backport-20261008`, `omarchy-m4-cpu` CPU idle loaded.
No suspend behavior is shipped by this page.

## Method

The only sleep state is `s2idle` (`/sys/power/mem_sleep`). `rtc0`
(`macsmc-rtc`) has no `wakealarm`, so every real cycle was woken by a short
power-button press. The kernel's `pm_test` levels were run first, one at a
time: `freezer`, `devices`, `platform`, then real s2idle. Panic text was
captured with `drm.panic_screen=kmsg` (write it with `printf kmsg`: the
parameter rejects a trailing newline) and `/sys/power/pm_print_times=1`.

## Results

| Stage | Result |
| --- | --- |
| `freezer` | Tasks froze and thawed. Speaker protection panicked (below). |
| `devices` | Kernel panic, `Asynchronous SError Interrupt`, in `s3c24xx_serial_resume` (below). With the UART unbound: passed; `nvme-apple` shut ANS down and rebooted RTKit on resume, and direct reads and filesystem writes worked afterwards. |
| `platform` | Returned, with `PS gfx: Failed to reach power state 0xf` on resume and a DCP `DPTXController` FIFO error during suspend. About a minute later the idle blank left the panel black with the backlight on, until a forced reboot. Holding the GPU and display domains on (next row) removed all three symptoms, so genpd powering them off is the inferred cause. |
| `platform`, domains held on | Clean, including DPMS off/on and new frames after resume. |
| Real s2idle, domains held on | Three cycles entered and left suspend-to-idle and woke on the power button; after the first, the owner confirmed screen, keyboard and trackpad. One ran with the UART bound: 44.6 s of wall time against 1.43 s of monotonic time. |

"Domains held on" means a bench module put the GPU and the boot
framebuffer's two genpd devices on the system-sleep wakeup path, so genpd kept
those `GENPD_FLAG_ACTIVE_WAKEUP` domains powered. It is a diagnostic, not a
shipped change.

## Blockers

1. **Speaker protection does not survive a suspend.** At thaw the capture
   stream returns an XRUN and `speakersafetyd-j713` panics in
   `src/main.rs` (`avail().expect("J713 capture availability")`, `EPIPE`).
   The lease lapses, so the kernel holds the amps low and marks the speaker
   group `CANCELLED`, which is the intended fail-closed result. Restarting the
   daemon then fails with `snd_pcm_open` `EBUSY` from `j713_port_open`,
   because `j713_audio_low_ready()` requires the group to be `LOW`. Two
   causes were found on 2026-10-24 ([#171](https://github.com/Kb2uka/Omarchy-M4-Native/pull/171)):
   - The group's suspend callback (`j713_suspend` in
     `audio/j713/kernel/j713-audio.c`) turns every state into `CANCELLED`,
     including an ownerless LOW reached by stopping protection cleanly before
     sleep. Only the last-stream close returns it to `LOW`, so protection
     cannot start again until a reboot.
   - If PipeWire was playing, its next start of the output after the lease
     lapsed fails the lease check and latches the MCA transport's `failed`
     flag until reboot (`J713 start of stream 0 failed until reboot: -125`
     with #171's logging). With the output idle, a `pm_test=freezer` cycle
     left the group at a clean LOW and protection started again.

   #171 keeps a verified, ownerless LOW across suspend and adds a sleep
   coordinator that stops user audio, then protection, before sleep and
   starts them again after resume. With it, `pm_test=devices` cycles
   recovered protection and playback, idle and while playing.
2. **GPU and display power domains.** `asahi_m4` has no system-sleep
   callbacks, and nothing keeps `gfx`, `disp_cpu` or `dptx_phy` on. Without
   them the GPU firmware and the display pipe are lost. Marking the domains
   `apple,always-on` or adding sleep support to the drivers are the options.
   [#172](https://github.com/Kb2uka/Omarchy-M4-Native/pull/172) adds
   `apple,always-on` in the devicetree. With it, on 2026-10-24, a real 36 s
   s2idle cycle resumed with the display, the GPU and (with #171) the speakers
   working.
3. **GPU mailbox wake storm.** While asleep, the GPU coprocessor mailbox
   (`302608000.mailbox-recv`) interrupted about 230 times a second, against
   about 59 a second awake, and woke the s2idle loop roughly 9,500 times in
   30 seconds. Traced on 2026-10-24:
   - Every interrupt is one firmware message, RTKit endpoint `0x20`,
     `0x0042000000000000`: `MSG_RX_DOORBELL`, the firmware asking the host to
     drain its rings. `g16.rs` answers it by waking its ring worker.
   - The mailbox driver requests its receive interrupt with `IRQF_NO_SUSPEND`
     (`drivers/soc/apple/mailbox.c`), for every coprocessor, so suspend does
     not mask it. Its `wakeup` is disabled; it does not end s2idle, it only
     brings cpu0 (and cpu9) out of the deep `CPU PD` state each time.
   - The rate is not caused by sleep. Awake, it was 68 to 79 a second with the
     screen lit and 219 a second with the screen blanked (DPMS off), back to
     68 after waking it. Over 8 s with the screen dark the firmware sent about
     360 doorbells a second while the host sent the GPU no messages, so it is
     not a reply loop: the firmware rings on its own whenever the GPU idles.
     The same rate therefore applies to an idle Air with the screen off, or
     locked with the lid closed (#92).
   - The ring it fills is not yet known. The drain (`drain_trace_rx`,
     `g16_runtime.rs`) is inlined, and the module exposes no parameters, so
     finding it needs an instrumented GPU test module.

   CPU idle itself works in s2idle: cores 1 to 8 entered `CPU PD` 2 to 8 times
   each during a 36 s cycle and stayed there; cpu0 and cpu9 re-entered it about
   8,600 times each. (`s2idle/time` under-reports, because the scheduler clock
   is suspended once every CPU is frozen.)
4. **Bluetooth** logged `hci0: command 0x0c01 tx timeout` on resume, which
   stretched resume to 2 s. Bluetooth stayed powered.
5. **The screen does not lock before sleep after the first unlock.**
   Omarchy 4.0.4's shell (`shell/plugins/lock/Service.qml`) reads its
   `locked` binding to decide a lock request. After an unlock that binding can
   stay `true` while the request, the session lock and its secure state are
   all `false`, so every later lock request returns `ok` and does nothing. On
   2026-10-24 a real s2idle cycle slept unlocked:
   `omarchy-system-sleep-lock: suspending without a secure lock (the shell did
   not secure the session within 12000ms)`, with no lock events after the
   earlier unlock. Upstream fixed it in
   [basecamp/omarchy#14978](https://github.com/basecamp/omarchy/pull/14978)
   (`d1a614ce6`), first released in `v4.5.0-rc1`, not in 4.0.5 or 4.0.6.
   Restarting the shell (`omarchy-restart-shell`) restores one lock.

## UART: a `pm_test` artifact only

The `devices`-stage panic is in `samsung-uart 3ad200000.serial`. The port is
never opened, so the driver leaves it runtime-suspended and genpd turns its
`uart0` domain off. `s3c24xx_serial_resume` then writes the port's registers
without a runtime-PM reference. At `pm_test=devices` the noirq phases are
skipped, the domain is still off, and the write faults. In a real suspend,
`genpd_finish_resume()` powers the domain on in the noirq phase first. With
the UART bound, a real s2idle cycle resumed it cleanly
(`genpd_resume_noirq` returned 0, then `s3c24xx_serial_resume` returned 0).
No kernel change is made. Unbind the UART before using `pm_test=devices`:

```sh
echo 3ad200000.serial | sudo tee /sys/bus/platform/drivers/samsung-uart/unbind
```

It cannot be bound again in the same session.

## Not measured

Sleep power: the Air was charging during the real cycles, and the kernel
reports no hardware sleep statistics. Lid-close suspend, long sleeps, audio
after resume and repeated cycles are untested.
