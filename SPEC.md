# Spec: Omarchy, bare metal, on the M4 MacBook Air

> Original plan from August 2026, not a list of delivered features.
> See [README.md](README.md) for the final checkpoint and current project status.
> Raw captures cited here are withheld; see [recon/README.md](recon/README.md).

**Status:** v2 — 2026-08-31 (supersedes v1's VM plan; VM path dropped entirely)
**Hardware:** MacBook Air 13" M4 — Mac16,12, board J713, SoC T8132 ("Donan"),
4P everest + 6E sawtooth, soc-generation H16.

## 1. Goal (Doug, 2026-08-31)

One installer that puts Omarchy on this machine as the boot OS. Power on →
Omarchy session, working as well as it does on the OptiPlex PC. macOS never
seen in daily use. No VM — bare metal only.

## 2. Platform ground rules (cannot be coded around)

1. **Apple Silicon cannot boot an ISO/USB installer.** iBoot only launches
   signed objects from the internal disk. Every OS install runs as an
   installer app from macOS/recoveryOS (the Asahi model). The deliverable is
   therefore an **installer app**, not an ISO.
2. **Apple firmware/recovery partitions stay forever** (iBoot, recoveryOS,
   ~few GB). macOS itself gets shrunk to a dormant stub — kept only for
   firmware updates — and never appears at boot. That is the maximum
   erasure the platform allows, and it fully satisfies the daily-driver goal.

## 3. The stack that must exist (bottom → top)

| Layer | Status 2026-08-31 | Our lever |
|---|---|---|
| m1n1 bootloader on T8132 | Real groundwork upstream (chickens, UART, NVMe, MCC, ATC) | Test/extend; needs 2nd machine for proxy-mode dev |
| Kernel device trees (t8132.dtsi + J713) | Skeletal upstream — CPUs/AIC/pmgr/i2c/UART only | **← current workstream: gap analysis + DT extension** |
| Kernel drivers (NVMe, SMC, USB, Wi-Fi, audio) | Mostly exist for M1/M2; need T8132 wiring + quirks | DT wiring first; driver patches where H16 diverges |
| Display | iBoot framebuffer (software) until a DCP driver | Usable early; not pretty |
| GPU (AGX on M4) | Nonexistent; the long pole | Track Asahi; Hyprland needs this for real parity |
| Arch Linux ARM userspace + Omarchy quattro aarch64 | Proven on ARM (ports exist; 45/112 PKGBUILDs already declare aarch64) | Port work is known-shape |
| **Omarchy installer app** (Asahi-installer derivative that lays down Omarchy as default boot) | Buildable today for M1/M2; blocked on all of the above for M4 | End deliverable |

"Works as well as the PC" is gated by the GPU driver — everything below it
can be brought up first (serial → framebuffer → daily-usable-but-soft-
rendered → full parity when AGX lands).

## 4. Working method

Study upstream (Asahi) platform bring-up and its contribution requirements; keep
Omarchy-specific glue (installer, packaging, config parity with the
OptiPlex) in this repo. Every hardware claim traces to this machine's ADT
dump (`recon/iodevicetree-full.txt`) — never guessed.

## 5. Current workstream

Initial research: T8132 gap-analysis matrix (ADT × upstream DT × driver
precedent) + first DT extension patch (SMC, internal NVMe/ANS, mailboxes,
dockchannel — the evidence-backed set). Repo:
https://github.com/Kb2uka/Omarchy-M4-Native.

## 6. Explicitly out of scope

- Any VM or emulation path (dropped 2026-08-31 by Doug).
- Dual-boot as a *visible* experience — macOS stub stays but is never shown.

## 7. References

- Asahi M4 feature support: https://asahilinux.org/docs/platform/feature-support/m4/
- Asahi installer (the model for ours): https://github.com/AsahiLinux/asahi-installer
- m1n1: https://github.com/AsahiLinux/m1n1
- Omarchy aarch64 drift: basecamp/omarchy discussion #7739
