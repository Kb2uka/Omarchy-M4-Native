# J713 headphone jack source

Source for `omarchy-m4-headphones`: playback through the 3.5 mm jack as a second
card, `J713Headphones`, beside the protected speaker card. What it does, how it
was found and what was measured: [J713 headphone jack](../../../docs/j713-headphones.md).

## Contents

- `kernel/`: two GPL-2.0-only modules for the pinned
  `7.1.9-m4-gpu-backport-20261008` kernel.
  - `j713_hp_dt.ko`: the overlay loader. kbuild compiles `j713-headphones.dtso`
    and links the blob into the module, so the source and the blob cannot
    drift. Placeholder phandles (`0xfeed00NN`) are replaced at load time with
    nodes looked up by path.
  - `snd-soc-j713-headphones.ko`: the card and CPU DAI driver for MCA cluster 2.
    Its register values and the shared-register contract with the speaker
    driver are in `j713_hp_plan.h`, which performs no MMIO. A read-only
    debugfs view, `/sys/kernel/debug/j713_headphones/regs`, compares every
    recipe register with its live value.
  - `test/plan_test.c`: host test of that contract (`make -C kernel/test`). It
    also runs in the offline suite (`tests/test_j713_headphones.py`).
- `policy/`: MIT-licensed UCM (`J713Headphones`) and WirePlumber files, laid
  out at their installed paths. The WirePlumber hook is optional: the stock
  `policy.default-nodes` *wants* it, so a broken hook logs a warning and
  cannot stop WirePlumber or the speakers. `tests/j713_headphones_policy.lua`
  runs it on WirePlumber 0.5.18's own default-node hooks, together with the
  protected-speaker hook.

The codec driver is the kernel's stock `snd-soc-cs42l84`. No speaker file,
manifest-pinned path or `j713_audio_*` module is changed.

## Building

From the same pinned kernel inputs as the speaker modules
(`../kernel-inputs.sha256` and the supplemental sound exports
`9f3e2cf8…`), with an immutable local kernel-build image ID:

```sh
bash tools/build-j713-headphones.sh KERNEL_BUILD SOUND_EXPORTS KERNEL_IMAGE NEW_OUTPUT
```

The image used here rebuilds the five qualified speaker modules in
`../qualified.json` byte for byte (`apple-admac`, `snd-soc-apple-mca`,
`snd-soc-j713-group` `aaad802e…`, `snd-soc-macaudio`, `snd-soc-tas2764`) with
`tools/build-j713-audio.sh`'s command. It gives:

| Module | Bytes | SHA256 |
| --- | --- | --- |
| `j713_hp_dt.ko` | 10,656 | `9ba22f0eb586052606eb6e9d98fa9d05663c12e3197c1b41c4bdc833afe41f07` |
| `snd-soc-j713-headphones.ko` | 37,016 | `a73ef5655d453c1ebd424922b0b6141a77159c01cbe718d4c972be61ec5be87d` |

These are the pins in `channel.json` (`headphone_modules`), in
`tools/m4-headphones.py` and in the package recipe. The recipe downloads them
from the `package-inputs` release as `j713_hp_dt-9ba22f0e.ko` and
`snd-soc-j713-headphones-a73ef565.ko`.
