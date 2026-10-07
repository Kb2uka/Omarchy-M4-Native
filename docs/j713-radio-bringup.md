# J713 native Wi-Fi and Bluetooth bring-up

On 2026-10-07, the M4 Air J713/T8132 completed a native Linux RAM boot with
WPA2 association, DHCP, gateway and internet traffic, DNS and HTTP retrieval.
The Bluetooth controller reached `UP RUNNING` in the same boot. All ten CPU
memory checks and the read-only SSD checks passed, and the bounded watchdog
returned the Air to a verified recovery proxy after 126.376 seconds.

This is a hardware bring-up checkpoint, not an installer or full wireless
qualification. No Air filesystem was mounted or modified. Roaming, sustained
throughput, Bluetooth pairing and audio, suspend/resume and all radio modes
remain untested. The reported transmit PHY rate is not a throughput benchmark.

## Reused work

- [Yureka's M4/M5 PCIe branch](https://cyberchaos.dev/yuka/linux/-/tree/feature/m4-m5-pcie),
  pinned at `d94954d69d13be441e92be98ef96d6790235910c`, describes J713 radio
  power on SMC GPIO 19 and the PCI children used for platform firmware data.
- [Wallace's M4 Pro radio evidence](https://github.com/damsleth/wallace/blob/55f116dd0e0f28d1412c6be728a101f7d861c473/evidence/2026-07-29-t6040-WIFI-AND-BLUETOOTH-WORKING.md)
  identifies the same BCM4388/revision 6 C0 firmware timeout, working C2
  firmware, and scan result version 116. This was a useful firmware lead;
  T6040 addresses were not copied to T8132.
- [The Asahi firmware converter](https://github.com/AsahiLinux/asahi-installer/tree/e93743d52178b3e26e428fa05174ef549abf30cb/asahi_firmware)
  supplied the Wi-Fi and Bluetooth conversion. Its current Wi-Fi converter
  excludes C4388 C2 because of an older firmware problem. The Wi-Fi conversion
  used the earlier `c53d66dc71937efa2530d4323c81addaebb5a09b` converter on
  **Garden files only** from macOS 26.5.2 build 25F84. New `_gen` filenames
  caused an unrestricted conversion to fail; that failed output was not used.

## Why the initial attempts failed

1. PCIe PHY and reset state alone did not bring up the link. Adding the
   J713 radio power GPIO brought up endpoints `14e4:4434` and `14e4:5f72`.
   The same result passed with the original kernel, without the temporary
   PCIe register diagnostics or clock-gating experiment.
2. The live ADT identifies the radio module as `garden`. The driver reads
   chip `BCM4388/6`, OTP module `WLMT`, vendor `a`, version `4.9`, and antenna
   SKU `X0`. The original 4387 firmware assumption was wrong.
3. `brcm,board-type = "apple,garden"` was needed to find the board firmware.
   C0 firmware then loaded but failed its five-second startup wait. C2
   firmware started and registered `wlan0` and `phy0`.
4. The first scan returned no entries and logged `BSS info version 116
   unsupported`. Allowing version 116 produced 36 distinct BSSIDs with
   plausible channel and signal values. A later association and traffic test
   passed.
5. The first connection harness used unsupported `wpa_supplicant -f` logging.
   It printed usage before association. Running the foreground process as a
   shell background job with redirected output fixed the harness. This was
   not a password or radio failure.

## Device tree contract

The qualified diagnostic DT already contains the T8132 PCIe controller at
`/soc/pcie@1cb0000000`, its DART and SMC GPIO controller. The radio additions
are the following. Symbolic references must resolve against that board tree;
these snippets do not add a missing PCIe host to an older kernel DTS.

```dts
/ {
    aliases {
        wifi0 = &wifi0;
        bluetooth0 = &bluetooth0;
    };
};

/* J713 PCIe port 0, using the existing SMC GPIO controller. */
&{/soc/pcie@1cb0000000/pci@0,0} {
    pwren-gpios = <&smc_gpio 19 GPIO_ACTIVE_HIGH>;
    bus-range = <1 1>;

    wifi0: wifi@0,0 {
        compatible = "pci14e4,4434";
        reg = <0x10000 0 0 0 0>;
        local-mac-address = [00 00 00 00 00 00];
        apple,antenna-sku = "XX";
        brcm,board-type = "apple,garden";
    };

    bluetooth0: bluetooth@0,1 {
        compatible = "pci14e4,5f72";
        reg = <0x10100 0 0 0 0>;
        local-bd-address = [00 00 00 00 00 00];
        brcm,board-type = "apple,garden";
    };
};
```

The aliases let the existing bootloader copy the actual MAC addresses and
calibration from the live ADT. The captured FDT contained 5,888 Wi-Fi
calibration bytes and two 16,652-byte Bluetooth calibration blobs. Do not
ship the placeholder addresses or substitute another board's calibration.
The diagnostic used a fresh live TrustCache reservation for each boot; see
[the SMP fix](https://github.com/Kb2uka/Omarchy-M4-Native/pull/7).

## Driver and firmware

The accompanying experimental kernel patch makes revision 6 select the C2
filename before the existing broad C0 entry, leaving other revision mappings
unchanged. It also raises the accepted scan information version from 112 to
116, following the tested firmware compatibility change. The hardware-tested
module also included temporary firmware-loading logs. The clean functional
patch has not yet been separately built and booted.

The version change affects the driver's acceptance range on other devices,
which were not tested here. It does not prove complete parser validation for
all newer firmware layouts. Treat this as a pinned bring-up patch pending
broader review and qualification.

Wi-Fi files use natural `brcmfmac4388c2-pcie.apple,garden` names, with the
`-WLMT-a.txt` board NVRAM variant. Essential hashes:

| File | SHA-256 |
| --- | --- |
| Garden C2 binary | `7cfae8622feeb119c756ae707d26f3a94f1cde44becefacf27ecf1fdc586d93b` |
| Garden C2 signature | `9abb8c1afe0413339f5eca7706150c507a7bca5d244b0a4c6f79e4c28d2ce7cf` |
| Garden WLMT-a NVRAM | `a3cf85bdd68083f40e47e9a029e77a4e14b32047bb9251a46e6b04e2232591f6` |
| Garden-a Bluetooth binary | `2887244dae0cf9bd8122bd418002c38f880c66d4725242f3585b5f61b8802a6e` |
| Garden-a Bluetooth PTB | `249f4b637bfa5c3494ed9b8f6ef7d213ace06a1c0f188360b3b4bbf7d9405572` |

Proprietary firmware and network credentials are not included. The connection
configuration lived only in the diagnostic RAM filesystem and private temporary
build files, which were removed after verified recovery.

## Captured results

```text
Firmware: BCM4388/6 wl0: Feb  2 2026 19:18:30
version 23.50.20.0.41.51.208 FWID 01-ef259bc2

Scan: 36 distinct BSSIDs, signal -91..-44 dBm
Frequencies: 2412 2437 2462 5200 5240 5765 5785 6135 MHz

Bluetooth: BT_UP_RETURN=0
UP RUNNING
Name: BCM4388C2 Olympic PCIe macOS_Garden_CLPC_OS AMKOR
Version: 23.5.0224.1476
HCI Version: 5.4

Wi-Fi: wpa_state=COMPLETED
key_mgmt=WPA2-PSK, pairwise_cipher=CCMP, freq=5765
DHCP lease obtained
Gateway: 3 packets transmitted, 3 received, 0% packet loss
Internet: 3 packets transmitted, 3 received, 0% packet loss
HTTP command: timeout 12 wget -S -O /tmp/network-http-body http://example.com/
Connecting to example.com (104.20.23.154:80)
HTTP/1.1 200 OK, 577 bytes retrieved
WIFI_CONNECT_END

CPU_PASS cpu=0 through cpu=9
SMP_MEMORY_PASS
READPROBE_PASS direct 8388608 bytes twice at three offsets; hashes equal
45 complete HTTP observation pairs
No Oops, BUG, WARNING or kernel panic in captured final dmesg
Recovery: 126.376 seconds, fresh proxy verified, watchdog control=0
```

Retained limitations in the log: Wi-Fi `join_pref -52`; Bluetooth reports an
LE Coded PHY capability whose usage is unsupported. Neither is erased from
the evidence or claimed fixed by this checkpoint.

The successful connection boot was `20261007T175023Z`, with these inputs:

| Artifact | SHA-256 |
| --- | --- |
| Kernel Image.gz | `0ce617a2b965c3620c75b91a6080e2f33c9e9dbd6a814392c94729b974124bac` |
| Kernel configuration | `0dd7fdb75e953363c8db86bdec8425539dcad7c4039eb72bd16257126afdc295` |
| Diagnostic brcmfmac module | `494f80bb6af47fde1d11af8708267f3161779a9fca289fb3bf90b5a9e4784619` |
| Source r21 DTB before per-boot fixups | `6a216236144b0bbebe1ee137bb542621318bd81472e9b54d7d639a9d4f84a15c` |
| Temporary connection initramfs, since removed | `2e7f1f8aa36c133e649a8dd46116d9aff6debad92fa8495ad252a23e6ecaf226` |

`idle=nop` remains enabled; this does not qualify CPU idle, DVFS, thermals or
suspend. Omarchy, accelerated graphics, audio and the remaining machine
features still require their own evidence.
