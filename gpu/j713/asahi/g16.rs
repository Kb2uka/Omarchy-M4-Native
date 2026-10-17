// SPDX-License-Identifier: GPL-2.0-only
// Copyright The Gravity Linux Contributors
// Adapted from Niklas Sheth's linux-m4-integration prototype.

//! G16 firmware bootstrap. Its 43-bit address space and initialization graph
//! must not be passed to a G13/G14 firmware manager.

use kernel::io::Io;
use kernel::{c_str, device::Core, platform, prelude::*};

const PAGE_SIZE: u64 = 0x4000;

fn firmware_allowed(firmware: &[u8], board: &[u8], probe_only: bool) -> bool {
    firmware == b"mBoot-18000.161.10\0"
        || (probe_only
            && firmware == b"mBoot-18000.120.36\0"
            && board.ends_with(&[0])
            && board.split(|&v| v == 0).any(|v| v == b"apple,j713")
            && board.split(|&v| v == 0).any(|v| v == b"apple,t8132"))
}

fn air_bootstrap_allowed(firmware: &[u8], board: &[u8], enabled: u32, probe_only: u32) -> bool {
    enabled == 1
        && probe_only == 0
        && firmware == b"mBoot-18000.120.36\0"
        && firmware_allowed(firmware, board, true)
}

/// The loader derives the GPU power limit per chip: top-state voltage times
/// its 24800 mA clamp. Only a 1000 mV top state reaches 24800 mW, so accept
/// each Air's own value up to that ceiling instead of one unit's exact value.
const AIR_MAX_POWER_MW: u32 = 24800;

/// The Air GPU has ten core positions. The loader reports the ten-core GPU as
/// 0x3ff and an eight-core GPU as any two positions fused off (one measured
/// Air reports 0x3bd). Both keep ten performance groups. Only Mesa receives
/// the mask; the firmware initialization graph does not carry it, so which
/// two positions are fused off changes nothing this module gives the firmware.
fn air_core_mask_allowed(core_mask: u32) -> bool {
    core_mask & !0x3ff == 0 && matches!(core_mask.count_ones(), 8 | 10)
}

/// Names the first Air platform value outside the qualified Air GPUs.
fn air_platform_rejection(core_mask: u32, groups: usize, max_mhz: u32, max_power: u32)
    -> Option<&'static str> {
    if !air_core_mask_allowed(core_mask) {
        Some("core mask")
    } else if groups != 10 {
        Some("performance groups")
    } else if max_mhz != 1470 {
        Some("maximum frequency")
    } else if max_power == 0 || max_power > AIR_MAX_POWER_MW {
        Some("maximum power")
    } else {
        None
    }
}

/// Validate reserved resources after the caller selects an allowed qualification mode.
pub(crate) fn validate_boot_resources(pdev: &platform::Device<Core>, probe_only: bool) -> Result {
    let dev = pdev.as_ref();
    let node = dev.of_node().ok_or(EINVAL)?;
    let fwnode = dev.fwnode().ok_or(EINVAL)?;
    // This is the firmware build exercised by the Python M4 shim. The
    // loader's generic macOS-version lookup currently does not recognize it.
    let firmware: KVec<u8> = node.get_property(c_str!("apple,firmware-build"))?;
    let board: KVec<u8> = kernel::of::root().ok_or(EINVAL)?.get_property(c_str!("compatible"))?;
    if !firmware_allowed(&firmware, &board, probe_only) {
        dev_err!(dev, "G16: unqualified boot firmware identifier\n");
        return Err(ENOTSUPP);
    }
    let private_vm: KVec<u64> = fwnode
        .property_read_array_vec(c_str!("apple,rtkit-private-vm-region"), 2)?
        .required_by(dev)?;

    if private_vm.len() != 2 {
        return Err(EINVAL);
    }
    if private_vm[0] != 0xffff_fc00_0000_0000 || private_vm[1] != 0x20_0000_0000 {
        dev_err!(
            dev,
            "G16: unsupported firmware private VM {:?}\n",
            private_vm
        );
        return Err(EINVAL);
    }

    dev_info!(dev, "G16: resource policy accepted; air_resources={}\n", probe_only);
    let mut regions = KVec::<(u64, u64)>::new();
    for (name, min_size) in [
        (c_str!("ttbs"), PAGE_SIZE),
        (c_str!("pagetables"), PAGE_SIZE),
        (c_str!("l2"), PAGE_SIZE),
        (c_str!("handoff"), PAGE_SIZE),
        (c_str!("firmware"), PAGE_SIZE),
    ] {
        let res = node.reserved_mem_region_to_resource_byname(name)?;
        let base = res.start() as u64;
        let size = res.size() as u64;
        let end = base.checked_add(size).ok_or(EINVAL)?;
        if base == 0 || size < min_size || (base | size) & (PAGE_SIZE - 1) != 0 {
            dev_err!(
                dev,
                "G16: invalid {} region {:#x}+{:#x}\n",
                name,
                base,
                size
            );
            return Err(EINVAL);
        }
        for &(other_base, other_end) in &regions {
            if base < other_end && other_base < end {
                dev_err!(dev, "G16: overlapping boot memory regions\n");
                return Err(EINVAL);
            }
        }
        regions.push((base, end), GFP_KERNEL)?;
        dev_info!(dev, "G16: {} {:#x}+{:#x}\n", name, base, size);
    }

    dev_info!(
        dev,
        "G16: boot memory contract validated; firmware initialization pending\n"
    );
    Ok(())
}

use kernel::{
    devres::Devres,
    io::{self, mem::IoMem},
    iosys_map::IoSysMapRef,
    soc::apple::rtkit,
    sync::Arc,
    sync::aref::ARef,
    types::ForeignOwnable,
};

struct BootData {
    dev: ARef<platform::Device>,
    notifications: Arc<Notifications>,
}

struct CrashBuffer {
    map: io::mem::Mem,
    offset: usize,
    size: usize,
    iova: usize,
}

impl rtkit::Buffer for CrashBuffer {
    fn iova(&self) -> Result<usize> {
        Ok(self.iova)
    }

    fn buf(&mut self) -> Result<IoSysMapRef<'_, u8>> {
        self.map.as_iosys_map(self.offset, self.size)
    }
}

struct BootOps;

#[vtable]
impl rtkit::Operations for BootOps {
    type Data = Arc<BootData>;
    type Buffer = CrashBuffer;

    fn shmem_map(
        data: <Self::Data as ForeignOwnable>::Borrowed<'_>,
        iova: usize,
        size: usize,
    ) -> Result<Self::Buffer> {
        let node = data.dev.as_ref().of_node().ok_or(EINVAL)?;
        let res = node.reserved_mem_region_to_resource_byname(c_str!("firmware"))?;
        let offset = iova.checked_sub(res.start() as usize).ok_or(EINVAL)?;
        if offset.checked_add(size).ok_or(EINVAL)? > res.size() as usize {
            return Err(EINVAL);
        }
        // SAFETY: The loader reserved this RAM for the already loaded GPU
        // firmware. Only its advertised crash-buffer subrange is exposed.
        let map = unsafe { io::mem::Mem::try_new(res, io::mem::MemFlag::WB.into())? };
        dev_info!(
            data.dev.as_ref(),
            "G16: crash buffer {:#x}+{:#x}\n",
            iova,
            size
        );
        Ok(CrashBuffer {
            map,
            offset,
            size,
            iova,
        })
    }

    fn recv_message(data: <Self::Data as ForeignOwnable>::Borrowed<'_>, ep: u8, msg: u64) {
        if ep == 0x20 && msg == 0x0042_0000_0000_0000 {
            // RTKit delivers this callback in process context after the
            // mailbox receive barrier. The worker drains the firmware rings.
            data.notifications.notify(false);
            return;
        }
        dev_info!(
            data.dev.as_ref(),
            "G16: firmware message {:#x}:{:#018x}\n",
            ep,
            msg
        );
    }

    fn crashed(data: <Self::Data as ForeignOwnable>::Borrowed<'_>, _log: Option<&[u8]>) {
        data.notifications.notify(true);
        dev_err!(data.dev.as_ref(), "G16: firmware crashed\n");
    }
}

/// Resources retained while the autonomous firmware session is running.
/// No render node is published until the submission backend is available.
#[path = "g16_runtime.rs"]
mod render_runtime;
pub(crate) use render_runtime::{FirmwareQueues, Stamp, Support};

#[path = "g16_notify.rs"]
mod notify;
pub(crate) use notify::Notifications;

// Keep the opening barrier transport separate from render queues. Firmware
// retains queue and item references after consuming this bootstrap prefix.
const BOOT_QUEUE: u64 = 0xffff_fc20_c000_4980;
const BOOT_POINTERS: u64 = 0xffff_fc20_0001_50c0;
const BOOT_RING: u64 = 0xffff_fc20_c001_50c0;
const BOOT_JOBS: u64 = 0xffff_fc20_0000_0030;
const BOOT_CONTEXT: u64 = 0xffff_fc20_c050_8080;
const BOOT_BARRIER: u64 = 0xffff_fc20_c052_0080;

pub(crate) struct Bootstrap {
    dev: ARef<platform::Device>,
    pub(crate) notifications: Arc<Notifications>,
    _rtkit: Pin<KBox<rtkit::RtKit<BootOps>>>,
    _asc: Pin<KBox<Devres<IoMem<0x4000>>>>,
    _sgx: Pin<KBox<Devres<IoMem<0x4000000>>>>,
    _handoff: io::mem::Mem,
    _address_space: Option<crate::g16_vm::AddressSpace>,
    _firmware_space: Option<crate::g16_vm::FirmwareSpace>,
    _opening_client: Option<crate::g16_vm::AddressSpace>,
    timestamp_ranges: KVec<core::ops::Range<u64>>,
    opening_notification_pending: bool,
    pub(crate) gpu_revision: u32,
    pub(crate) core_mask: u32,
    pub(crate) maximum_frequency_khz: u32,
    tvb_pools: KVec<crate::g16_tvb::Tvb>,
    render_publication: u64,
    next_work_va: u64,
    work_arenas: [core::ops::Range<u64>; 2],
    free_work: [KVec<u64>; 2],
    compute_publication: u64,
    queue_events: Arc<core::sync::atomic::AtomicU64>,
    render_failed: bool,
    flights: KVec<render_runtime::Flight>,
    publications: KVec<render_runtime::Publication>,
    batch_works: usize,
    context_roots: [Option<crate::g16_vm::Roots>; 64],
    context_users: [u32; 64],
    pressure_reported: core::sync::atomic::AtomicU64,
    render_support: kernel::sync::Arc<core::sync::atomic::AtomicU64>,
}

// SAFETY: The session owns its mapped memory and page tables; none are
// thread-local. Runtime access is exclusive (under the DRM frontend mutex).
// RTKit callbacks access only their independently synchronized callback data.
unsafe impl Send for Bootstrap {}

impl Drop for Bootstrap {
    fn drop(&mut self) {
        if let Err(e) = self._rtkit.as_mut().shutdown() {
            dev_err!(
                self.dev.as_ref(),
                "G16: shutdown failed {:?}; retaining GPU mappings\n",
                e
            );
            // A failed shutdown is not proof that firmware stopped accessing
            // these pages. Keep their tables and backing memory alive.
            core::mem::forget(self._address_space.take());
            core::mem::forget(self._firmware_space.take());
            core::mem::forget(self._opening_client.take());
        }
    }
}

impl Bootstrap {
    pub(crate) fn new(pdev: &platform::Device<Core>) -> Result<Self> {
        let qualification = *crate::module_parameters::air_bootstrap_test.value();
        if qualification != 0 {
            let node = pdev.as_ref().of_node().ok_or(EINVAL)?;
            let firmware: KVec<u8> = node.get_property(c_str!("apple,firmware-build"))?;
            let board: KVec<u8> = kernel::of::root().ok_or(EINVAL)?.get_property(c_str!("compatible"))?;
            if !air_bootstrap_allowed(&firmware, &board, qualification,
                                     *crate::module_parameters::probe_only.value()) {
                dev_err!(pdev.as_ref(), "G16: Air bootstrap refused: firmware-build {} or board not qualified\n",
                         core::str::from_utf8(&firmware).unwrap_or("?").trim_end_matches('\0'));
                return Err(ENOTSUPP);
            }
        }
        validate_boot_resources(pdev, qualification == 1)?;
        let platform = crate::g16_platform::Platform::new(pdev.as_ref())?;
        if qualification == 1 {
            let max_mhz = platform.maximum_frequency_mhz();
            if let Some(field) = air_platform_rejection(platform.core_mask, platform.freq_a.len(),
                                                        max_mhz, platform.maximum_power) {
                dev_err!(pdev.as_ref(),
                         "G16: Air platform rejected ({}): mask={:#x} groups={} max-frequency={} MHz max-power={} mW\n",
                         field, platform.core_mask, platform.freq_a.len(), max_mhz, platform.maximum_power);
                return Err(EINVAL);
            }
            dev_info!(pdev.as_ref(), "G16: Air platform accepted; max-power={} mW\n",
                      platform.maximum_power);
        }
        use core::mem::ManuallyDrop;
        // This bounded qualification build retains transport and mapped memory
        // on any constructor failure. Its launcher never unloads the module.

        // All board inputs are checked before any firmware is started.
        let mut bundle = platform.bundle(unsafe { kernel::bindings::get_random_u32() })?;
        // The firmware Timestamp handler accepts user destinations only in
        // this 64 MiB aperture, based at header+0x28.
        bundle[0x28..0x30].copy_from_slice(&crate::g16_fw::TIMESTAMP_BASE.to_le_bytes());
        let region_c = platform.region_c()?;
        let dev = pdev.as_ref();
        let mut address_space = ManuallyDrop::new(crate::g16_vm::AddressSpace::new()?);
        address_space.check_geometry(dev)?;
        address_space.check_borrowed_root(dev)?;
        let asc = ManuallyDrop::new(KBox::pin_init(
            pdev.io_request_by_name(c_str!("asc"))
                .ok_or(EINVAL)?
                .iomap_sized::<0x4000>(),
            GFP_KERNEL,
        )?);
        let sgx = ManuallyDrop::new(KBox::pin_init(
            pdev.io_request_by_name(c_str!("sgx"))
                .ok_or(EINVAL)?
                .iomap_sized::<0x4000000>(),
            GFP_KERNEL,
        )?);
        let res = dev
            .of_node()
            .ok_or(EINVAL)?
            .reserved_mem_region_to_resource_byname(c_str!("handoff"))?;
        // SAFETY: This is the reserved firmware handoff page from the loader,
        // not an allocator-owned page. No new DMA address is introduced here.
        let handoff = ManuallyDrop::new(unsafe { io::mem::Mem::try_new(res, io::mem::MemFlag::WB.into())? });
        let asc_guard = asc.try_access().ok_or(ENODEV)?;
        let asc_io = &*asc_guard;
        let control = asc_io.relaxed().read32(0x44);
        let status = asc_io.relaxed().read32(0x48);
        drop(asc_guard);
        dev_info!(
            dev,
            "G16: cold ASC control={:#x} status={:#x}\n",
            control,
            status
        );
        if control & 0x10 != 0 || status & 3 != 2 {
            return Err(EBUSY);
        }
        let sgx_guard = sgx.try_access().ok_or(ENODEV)?;
        let sgx_io = &*sgx_guard;
        let gpu_id = sgx_io.relaxed().read32(crate::ID_VERSION);
        if gpu_id == 0 || gpu_id == u32::MAX || (qualification == 1 &&
            (gpu_id != 0x0a021100 || sgx_io.relaxed().read32(crate::ID_COUNTS_1) != 0x0011010a)) {
            dev_err!(dev, "Invalid GPU ID {:#010x}\n", gpu_id);
            return Err(ENODEV);
        }
        // ID_VERSION retains the revision byte used by the Asahi UAPI:
        // 0x00 = A0, 0x10 = B0, 0x11 = B1. This is not the firmware enum.
        let gpu_revision = (gpu_id >> 8) & 0xff;
        dev_info!(dev, "G16: GPU ID={:#010x} revision={:#04x}\n", gpu_id, gpu_revision);
        for offset in [0x1000104, 0x1000108] {
            sgx_io.relaxed().write32(sgx_io.relaxed().read32(offset) | 1, offset);
        }
        sgx_io.relaxed().write32(6, 0xd06030);
        drop(sgx_guard);
        // SAFETY: The region validation guarantees a full aligned page. The
        // coprocessor is stopped, so these host-owned bootstrap fields are
        // not concurrently accessed by firmware.
        unsafe {
            handoff
                .ptr()
                .cast::<u64>()
                .write_volatile(0x4b1d000000000002);
            handoff
                .ptr()
                .add(0x18)
                .cast::<u32>()
                .write_volatile(u32::MAX);
            handoff.ptr().add(0x640).cast::<u64>().write_volatile(0);
            for off in (0..PAGE_SIZE as usize).step_by(64) {
                core::arch::asm!("dc cvac, {addr}", addr = in(reg) handoff.ptr().add(off));
            }
        }
        crate::mem::sync();

        let notifications = Arc::pin_init(Notifications::new(), GFP_KERNEL)?;
        let data = Arc::new(
            BootData {
                dev: pdev.into(),
                notifications: notifications.clone(),
            },
            GFP_KERNEL,
        )?;
        let mut rtkit = ManuallyDrop::new(KBox::pin(
            rtkit::RtKit::<BootOps>::new(dev, None, 0, data)?,
            GFP_KERNEL,
        )?);
        rtkit.as_mut().set_early_crashlog();
        let asc_guard = asc.try_access().ok_or(ENODEV)?;
        asc_guard.relaxed().write32(control | 0x10, 0x44);
        drop(asc_guard);
        crate::mem::sync();
        rtkit.as_mut().boot()?;
        for ep in [0x20, 0x21] {
            if !rtkit.as_mut().has_endpoint(ep) {
                return Err(ENODEV);
            }
            rtkit.as_mut().start_endpoint(ep)?;
        }
        dev_info!(
            dev,
            "G16: autonomous RTKit boot complete; firmware endpoints ready\n"
        );
        let mut firmware_space = ManuallyDrop::new(crate::g16_vm::FirmwareSpace::attach(dev)?);
        firmware_space.alloc(
            crate::g16_fw::ROOT_ADDRESS,
            PAGE_SIZE as usize,
            crate::pgtable::prot::PROT_FW_SHARED_RO,
        )?;
        firmware_space.write(
            crate::g16_fw::ROOT_ADDRESS,
            &crate::g16_fw::RootPointers::bootstrap().encode(),
        )?;
        firmware_space.alloc(
            crate::g16_fw::BUNDLE_ADDRESS,
            crate::g16_fw::BUNDLE_SIZE + PAGE_SIZE as usize,
            crate::pgtable::prot::PROT_FW_PRIV_RW,
        )?;
        firmware_space.write(crate::g16_fw::BUNDLE_ADDRESS, &bundle)?;
        // All root support objects and receive rings occupy this shared extent.
        firmware_space.alloc(
            0xffff_fc20_0003_0000,
            0xc4000,
            crate::pgtable::prot::PROT_FW_SHARED_RW,
        )?;
        let pointers = crate::g16_fw::RootPointers::bootstrap();
        for va in [pointers.region_a, pointers.region_c] {
            firmware_space.alloc(
                va,
                PAGE_SIZE as usize,
                crate::pgtable::prot::PROT_FW_SHARED_RO,
            )?;
        }
        for va in [pointers.status_a, pointers.status_b] {
            firmware_space.write(va + 4, &1u32.to_le_bytes())?;
        }
        firmware_space.write(pointers.region_c, &region_c)?;
        // Extra policy objects, computed descriptors, PB/UMA tables and
        // scheduler counters begin zero, exactly as the current shim.
        firmware_space.alloc(
            0xffff_fc20_c050_0000,
            PAGE_SIZE as usize,
            crate::pgtable::prot::PROT_FW_PRIV_RW,
        )?;
        for r in &platform.registers {
            let prot = if r.flags & 2 != 0 {
                crate::pgtable::prot::PROT_FW_MMIO_RW
            } else {
                crate::pgtable::prot::PROT_FW_MMIO_RO
            };
            if r.slot == 3 {
                for (i, physical) in platform.mcc.iter().enumerate() {
                    firmware_space.map_external(
                        r.address + i as u64 * u64::from(r.stride),
                        *physical,
                        r.stride as usize,
                        prot,
                    )?;
                }
            } else {
                let offset = r.physical & (PAGE_SIZE - 1);
                let size = (offset + u64::from(r.size) + PAGE_SIZE - 1) & !(PAGE_SIZE - 1);
                firmware_space.map_external(
                    r.address - offset,
                    r.physical - offset,
                    size as usize,
                    prot,
                )?;
            }
        }
        // Work-channel cursor pages use shared attributes, unlike other MMIO.
        firmware_space.map_external(
            0xffff_fc20_0002_0000,
            0x300d60000,
            PAGE_SIZE as usize,
            crate::pgtable::prot::PROT_FW_SHARED_RO,
        )?;
        firmware_space.map_external(
            0xffff_fc20_0002_8000,
            0x300d64000,
            PAGE_SIZE as usize,
            crate::pgtable::prot::PROT_FW_SHARED_RW,
        )?;
        for (va, size) in [
            (0xffff_fc20_c002_0000, 0x8c000),
            (0xffff_fc20_c00b_0000, 0xc0000),
            (0xffff_fc20_c017_8000, 0x94000),
            (0xffff_fc20_c021_0000, 0x84000),
            (0xffff_fc20_c029_8000, 0xac000),
            (0xffff_fc20_c034_8000, 0x4000),
            (0xffff_fc20_c035_0000, 0x4000),
            (0xffff_fc20_c035_8000, 0x8000),
            (0xffff_fc20_c036_8000, 0x8000),
            (0xffff_fc20_c037_8000, 0xc000),
            (0xffff_fc20_c038_8000, 0x34000),
            (0xffff_fc20_c03c_0000, 0xc4000),
            (0xffff_fc20_c048_8000, 0x34000),
            (0xffff_fc20_c04c_0000, 0x10000),
            (0xffff_fc20_c051_0000, 0x4000),
            (0xffff_fc20_c053_0000, 0x4000),
            (0xffff_fc20_c05e_8000, 0x4000),
            (0xffff_fc20_c05e_c000, 0x4000),
        ] {
            firmware_space.alloc(va, size, crate::pgtable::prot::PROT_FW_PRIV_RW)?;
        }
        for va in [
            0xffff_fc20_0011_0000,
            0xffff_fc20_0011_8000,
            0xffff_fc20_0012_0000,
            0xffff_fc20_0012_8000,
            0xffff_fc20_0013_0000,
            0xffff_fc20_0015_0000,
            0xffff_fc20_0014_0000,
            0xffff_fc20_001b_0000,
            0xffff_fc20_001c_0000,
            0xffff_fc20_001c_4000,
            0xffff_fc20_001c_8000,
            0xffff_fc20_001c_c000,
            0xffff_fc20_c052_4000,
        ] {
            firmware_space.alloc(
                va,
                PAGE_SIZE as usize,
                crate::pgtable::prot::PROT_FW_SHARED_RW,
            )?;
        }
        firmware_space.alloc(
            0xffff_fc20_001b_8000,
            PAGE_SIZE as usize,
            crate::pgtable::prot::PROT_FW_SHARED_RO,
        )?;
        // Shared support version and read-only resume records also serve the
        // render support object; construct them before the firmware starts.
        firmware_space.write(0xffff_fc20_001b_0000, &1u32.to_le_bytes())?;
        for record in 0..96u64 {
            firmware_space.write(
                0xffff_fc20_001b_8000 + record * 0x40,
                &0x0000_0070_0000_1822u64.to_le_bytes(),
            )?;
        }
        for (va, size) in [
            (0x70_0000_0000, 0x8c000),
            (0x70_0009_0000, 0xc0000),
            (0x70_0015_8000, 0x94000),
            (0x70_001f_0000, 0x84000),
        ] {
            address_space.alloc_low(va, size, crate::pgtable::prot::PROT_GPU_SHARED_RO)?;
        }
        for (low, high) in [
            (0x70_0027_8000, 0xffff_fc20_0012_8000),
            (0x70_0028_0000, 0xffff_fc20_0013_0000),
        ] {
            let pa = firmware_space.physical(high)?;
            address_space.low.map_pages(
                low..low + PAGE_SIZE,
                pa,
                crate::pgtable::prot::PROT_GPU_SHARED_RW,
                false,
            )?;
            if address_space.low.translate(low)? != Some(pa) {
                return Err(EIO);
            }
        }
        firmware_space.alloc_render_support()?;
        use crate::g16_compute as compute;
        firmware_space.alloc(
            compute::QUEUE,
            0xc000,
            crate::pgtable::prot::PROT_FW_PRIV_RW,
        )?;
        firmware_space.alloc(
            compute::SHARED,
            0x4000,
            crate::pgtable::prot::PROT_FW_SHARED_RW,
        )?;
        let render = crate::g16_render::Addresses::bootstrap();
        // The Work register windows are firmware-owned pages exposed through
        // context zero's low root. Replace the initial zero backing explicitly.
        for (alias, work) in [
            (render.tiling_register_alias, render.tiling_work),
            (render.fragment_register_alias, render.fragment_work),
        ] {
            let base = alias & !(PAGE_SIZE - 1);
            let pa = firmware_space.physical(work & !(PAGE_SIZE - 1))?;
            address_space.low.unmap_pages(base..base + PAGE_SIZE)?;
            address_space.low.map_pages(
                base..base + PAGE_SIZE,
                pa,
                crate::pgtable::prot::PROT_GPU_SHARED_RW,
                false,
            )?;
        }
        address_space.sync();
        // Reserve the transport before firmware can cache a missing mapping.
        for (va, size, access) in [
            (
                0xffff_fc20_c000_0000,
                0x8000,
                crate::pgtable::prot::PROT_FW_PRIV_RW,
            ),
            (
                0xffff_fc20_0001_0000,
                0x8000,
                crate::pgtable::prot::PROT_FW_SHARED_RW,
            ),
            (
                0xffff_fc20_c001_0000,
                0x8000,
                crate::pgtable::prot::PROT_FW_PRIV_RW,
            ),
            (
                0xffff_fc20_0000_0000,
                0x4000,
                crate::pgtable::prot::PROT_FW_SHARED_RW,
            ),
            (
                0xffff_fc20_c050_8000,
                0x4000,
                crate::pgtable::prot::PROT_FW_SHARED_RW,
            ),
            (
                0xffff_fc20_c052_0000,
                0x4000,
                crate::pgtable::prot::PROT_FW_PRIV_RW,
            ),
            (
                0xffff_fc20_0013_8000,
                0x4000,
                crate::pgtable::prot::PROT_FW_SHARED_RW,
            ),
        ] {
            firmware_space.alloc(va, size, access)?;
        }
        use crate::g16_fw::queue as q;
        firmware_space.write(BOOT_POINTERS, &q::pointers(0x500, 2))?;
        firmware_space.write(BOOT_JOBS, &q::jobs(BOOT_JOBS))?;
        firmware_space.write(BOOT_CONTEXT, &q::context())?;
        firmware_space.write(
            BOOT_QUEUE,
            &q::Queue {
                pointers: BOOT_POINTERS,
                ring: BOOT_RING,
                jobs: BOOT_JOBS,
                private: BOOT_QUEUE + 0xb0,
                context: BOOT_CONTEXT,
                uuid: 1,
            }
            .encode(),
        )?;
        firmware_space.write(BOOT_BARRIER, &q::barrier(q::STAMP, 0, 0, 1, 1))?;
        for off in [0, 8] {
            firmware_space.write(BOOT_RING + off, &BOOT_BARRIER.to_le_bytes())?;
        }
        firmware_space.sync();
        let mut opening_client = ManuallyDrop::new(crate::g16_vm::AddressSpace::new()?);
        opening_client.alloc_low(
            0x70_0000_0000,
            0x400000,
            crate::pgtable::prot::PROT_GPU_SHARED_RW,
        )?;
        opening_client.alloc_low(
            crate::g16_fw::OPERAND_TABLE,
            0x10000,
            crate::pgtable::prot::PROT_GPU_SHARED_RW,
        )?;
        for index in 0..20 {
            opening_client.alloc_low(
                crate::g16_fw::operand_page(index * 512),
                0x200000,
                crate::pgtable::prot::PROT_GPU_SHARED_RW,
            )?;
        }
        for first in (0..20 * 512).step_by(32) {
            let mut row = [0u8; 256];
            for i in 0..32 {
                row[i * 8..i * 8 + 8]
                    .copy_from_slice(&crate::g16_fw::operand_page(first + i).to_le_bytes());
            }
            opening_client.write_low(0x70_0000_0000 + (first * 8) as u64, &row)?;
        }
        for (base, size) in compute::private_ranges() {
            opening_client.alloc_low(base, size, crate::pgtable::prot::PROT_GPU_SHARED_RW)?;
        }
        for buffer in 0..compute::BUFFER_COUNT {
            for first in (0..compute::BUFFER_SIZE / 0x1000).step_by(32) {
                let mut row = [0; 256];
                for i in 0..32 {
                    let va = compute::BUFFER_BASE
                        + buffer as u64 * compute::BUFFER_STRIDE
                        + (first + i) as u64 * 0x1000;
                    row[i * 8..i * 8 + 8].copy_from_slice(&va.to_le_bytes());
                }
                opening_client.write_low(
                    compute::DIRECTORY
                        + (buffer * (compute::BUFFER_SIZE / 0x1000) + first) as u64 * 8,
                    &row,
                )?;
            }
        }
        firmware_space.write_live(compute::SUPPORT, &compute::support())?;
        opening_client.sync();
        dev_info!(
            dev,
            "G16: Linux-built root/main descriptors mapped at {:#x}; not yet published\n",
            crate::g16_fw::ROOT_ADDRESS
        );
        // Complete every fallible allocation before transferring ownership.
        let queue_events = Arc::new(core::sync::atomic::AtomicU64::new(0), GFP_KERNEL)?;
        let render_support = kernel::sync::Arc::new(
            core::sync::atomic::AtomicU64::new(0), GFP_KERNEL)?;
        let mut session = Self {
            dev: pdev.into(),
            notifications,
            _rtkit: ManuallyDrop::into_inner(rtkit),
            _asc: ManuallyDrop::into_inner(asc),
            _sgx: ManuallyDrop::into_inner(sgx),
            _handoff: ManuallyDrop::into_inner(handoff),
            _address_space: Some(ManuallyDrop::into_inner(address_space)),
            _firmware_space: Some(ManuallyDrop::into_inner(firmware_space)),
            _opening_client: Some(ManuallyDrop::into_inner(opening_client)),
            timestamp_ranges: KVec::new(),
            opening_notification_pending: true,
            gpu_revision,
            core_mask: platform.core_mask,
            maximum_frequency_khz: platform.maximum_frequency_mhz() * 1000,
            tvb_pools: KVec::new(),
            render_publication: *crate::module_parameters::fw_counter_start.value(),
            next_work_va: 0xffff_fc22_0000_0000,
            work_arenas: [0..0, 0..0],
            free_work: [KVec::new(), KVec::new()],
            compute_publication: *crate::module_parameters::fw_counter_start.value(),
            queue_events,
            render_failed: false,
            flights: KVec::new(),
            publications: KVec::new(),
            batch_works: 0,
            context_roots: [None; 64],
            context_users: [0; 64],
            pressure_reported: core::sync::atomic::AtomicU64::new(0),
            render_support,
        };
        if *crate::module_parameters::fw_trace.value() & 1 != 0 {
            // Region C starts with the firmware KTrace mask, as in the shim.
            session
                ._firmware_space
                .as_mut()
                .ok_or(EIO)?
                .write_live(0xffff_fc20_000f_8000, &u32::MAX.to_le_bytes())?;
        }
        session.publish(pdev)?;
        session.open_control(pdev)?;
        session.submit_barrier(pdev)?;
        Ok(session)
    }

    fn submit_barrier(&mut self, pdev: &platform::Device<Core>) -> Result {
        use crate::g16_fw::queue as q;
        let dev = pdev.as_ref();
        let firmware = self._firmware_space.as_mut().ok_or(EINVAL)?;
        let sgx_guard = self._sgx.try_access().ok_or(ENODEV)?;
        let sgx = &*sgx_guard;
        for addr in [0xffff_fc20_0003_f190, 0xffff_fc20_0003_f198] {
            firmware.write_live(addr, &0u32.to_le_bytes())?;
        }
        let before = (
            sgx.relaxed().read32(0xd64040),
            sgx.relaxed().read32(0xd64048),
            sgx.relaxed().read32(0xd60020),
        );
        if before != (0, 0, 0) {
            return Err(EBUSY);
        }
        // Pair-zero fragment channel, with two satisfied barrier items.
        firmware.write_live(
            crate::g16_fw::BUNDLE_ADDRESS + 0x16240,
            &q::channel(BOOT_QUEUE, 2, 1, 1, true, 1),
        )?;
        sgx.relaxed().write32(1, 0xd60020);
        crate::mem::sync();
        drop(sgx_guard);
        let mut committed = false;
        for _ in 0..3000 {
            let cursor = firmware.read_u32(crate::g16_fw::CONTROL_DATA + 0x48)?;
            let aux = firmware.read_u32(crate::g16_fw::CONTROL_AUX)?;
            if cursor == 20 * 8 && (aux == 0 || aux == 1) {
                committed = true;
                break;
            }
            kernel::time::delay::fsleep(kernel::time::Delta::from_millis(1));
        }
        if !committed {
            dev_err!(
                dev,
                "G16: first-work registration stalled at {:#x}/{:#x}\n",
                firmware.read_u32(crate::g16_fw::CONTROL_DATA + 0x48)?,
                firmware.read_u32(crate::g16_fw::CONTROL_AUX)?
            );
            return Err(ETIMEDOUT);
        }
        self._rtkit
            .as_mut()
            .send_message(0x21, 0x0083_0000_0000_0001)?;
        for _ in 0..3000 {
            let sgx_guard = self._sgx.try_access().ok_or(ENODEV)?;
            let sgx = &*sgx_guard;
            if sgx.relaxed().read32(0xd64040) == 1
                && sgx.relaxed().read32(0xd64048) == 1
                && firmware.read_u32(BOOT_POINTERS + 0x30)? == 2
            {
                // Barriers consume the inner ring without advancing its
                // separate completion cursor, as in the working source path.
                dev_info!(dev, "G16: barrier channel retired; two items consumed\n");
                return Ok(());
            }
            drop(sgx_guard);
            kernel::time::delay::fsleep(kernel::time::Delta::from_millis(1));
        }
        let sgx_guard = self._sgx.try_access().ok_or(ENODEV)?;
        let sgx = &*sgx_guard;
        dev_err!(
            dev,
            "G16: barrier timeout; channel {}/{}/{} item done/read {}/{}\n",
            sgx.relaxed().read32(0xd64040),
            sgx.relaxed().read32(0xd64048),
            sgx.relaxed().read32(0xd60020),
            firmware.read_u32(BOOT_POINTERS)?,
            firmware.read_u32(BOOT_POINTERS + 0x30)?
        );
        Err(ETIMEDOUT)
    }

    fn open_control(&mut self, pdev: &platform::Device<Core>) -> Result {
        let dev = pdev.as_ref();
        let firmware = self._firmware_space.as_mut().ok_or(EINVAL)?;
        let client = self._opening_client.as_ref().ok_or(EINVAL)?;
        let res = dev
            .of_node()
            .ok_or(EINVAL)?
            .reserved_mem_region_to_resource_byname(c_str!("ttbs"))?;
        // SAFETY: This is the loader's validated context table reservation.
        let ttbs = unsafe { io::mem::Mem::try_new(res, io::mem::MemFlag::WB.into())? };
        unsafe {
            // Context one has ASID one, with independently allocated roots.
            ttbs.ptr()
                .add(16)
                .cast::<u64>()
                .write_volatile(client.low.ttb() | (1 << 48) | 1);
            ttbs.ptr()
                .add(24)
                .cast::<u64>()
                .write_volatile(client.high.ttb() | (1 << 48) | 1);
            core::arch::asm!("dc cvac, {addr}", addr = in(reg) ttbs.ptr());
        }
        crate::mem::sync();
        firmware.alloc(
            crate::g16_fw::CONTROL_DATA,
            PAGE_SIZE as usize,
            crate::pgtable::prot::PROT_FW_PRIV_RW,
        )?;
        firmware.alloc(
            crate::g16_fw::CONTROL_AUX,
            PAGE_SIZE as usize,
            crate::pgtable::prot::PROT_FW_SHARED_RW,
        )?;
        firmware.write(crate::g16_fw::CONTROL_DATA, &crate::g16_fw::opening_flist())?;
        // Own distinct shared pages for the mapping-notification state/ring.
        firmware.alloc(
            0xffff_fc21_8100_0000,
            PAGE_SIZE as usize,
            crate::pgtable::prot::PROT_FW_SHARED_RW,
        )?;
        firmware.alloc(
            0xffff_fc21_8100_8000,
            PAGE_SIZE as usize,
            crate::pgtable::prot::PROT_FW_SHARED_RW,
        )?;
        firmware.sync();
        firmware.write_live(
            crate::g16_fw::BUNDLE_ADDRESS + 0x1df40,
            &crate::g16_fw::opening_control(),
        )?;
        let sgx_guard = self._sgx.try_access().ok_or(ENODEV)?;
        let sgx = &*sgx_guard;
        let before = (
            sgx.relaxed().read32(0xd640c0),
            sgx.relaxed().read32(0xd640c8),
            sgx.relaxed().read32(0xd60060),
        );
        if before != (1, 1, 1) {
            dev_err!(dev, "G16: unexpected opening cursors {:?}\n", before);
            return Err(EIO);
        }
        sgx.relaxed().write32(3, 0xd60060);
        drop(sgx_guard);
        for (address, value) in [
            (0xffff_fc20_0003_4f88, 0xffff_fc21_8100_0000u64),
            (0xffff_fc20_0003_4f90, 0xffff_fc21_8100_8000u64),
        ] {
            firmware.write_live(address, &value.to_le_bytes())?;
        }
        firmware.write_live(0xffff_fc20_0003_4fd4, &1u32.to_le_bytes())?;
        crate::mem::tlbi_asid(1);
        crate::mem::sync();
        kernel::time::delay::fsleep(kernel::time::Delta::from_millis(100));
        self._rtkit
            .as_mut()
            .send_message(0x21, 0x0084_0000_0000_0011)?;
        for _ in 0..3000 {
            let sgx_guard = self._sgx.try_access().ok_or(ENODEV)?;
            let sgx = &*sgx_guard;
            let cursors = (
                sgx.relaxed().read32(0xd640c0),
                sgx.relaxed().read32(0xd640c8),
                sgx.relaxed().read32(0xd60060),
            );
            if cursors == (3, 3, 3) && firmware.read_u32(0xffff_fc20_0003_f19c)? == 1 {
                dev_info!(
                    dev,
                    "G16: opening control registration retired; cursors {:?}\n",
                    cursors
                );
                return Ok(());
            }
            drop(sgx_guard);
            kernel::time::delay::fsleep(kernel::time::Delta::from_millis(1));
        }
        let sgx_guard = self._sgx.try_access().ok_or(ENODEV)?;
        let sgx = &*sgx_guard;
        dev_err!(
            dev,
            "G16: control timeout; cursors {}/{}/{} completion {}\n",
            sgx.relaxed().read32(0xd640c0),
            sgx.relaxed().read32(0xd640c8),
            sgx.relaxed().read32(0xd60060),
            firmware.read_u32(0xffff_fc20_0003_f19c)?
        );
        Err(ETIMEDOUT)
    }

    fn publish(&mut self, pdev: &platform::Device<Core>) -> Result {
        let dev = pdev.as_ref();
        let address_space = self._address_space.as_ref().ok_or(EINVAL)?;
        let firmware_space = self._firmware_space.as_ref().ok_or(EINVAL)?;
        let res = dev
            .of_node()
            .ok_or(EINVAL)?
            .reserved_mem_region_to_resource_byname(c_str!("ttbs"))?;
        // SAFETY: This is the validated reserved hardware context table.
        let ttbs = unsafe { io::mem::Mem::try_new(res, io::mem::MemFlag::WB.into())? };
        let sgx_guard = self._sgx.try_access().ok_or(ENODEV)?;
        let sgx = &*sgx_guard;
        let main = crate::g16_fw::MainConfig::bootstrap(crate::g16_fw::BUNDLE_ADDRESS);
        for channel in &main.channels[..12] {
            for va in channel.state {
                let offset = if va >= 0xffff_fc20_0002_8000 {
                    0xd64000 + (va - 0xffff_fc20_0002_8000) as usize
                } else {
                    0xd60000 + (va - 0xffff_fc20_0002_0000) as usize
                };
                sgx.try_write32(0, offset)?;
                if sgx.try_read32(offset)? != 0 {
                    return Err(EIO);
                }
            }
        }
        // Slot zero (opcode 0x16) is already embedded in the main object.
        for (offset, value) in [(0xd640c0, 0), (0xd640c8, 0), (0xd60060, 1)] {
            sgx.try_write32(value, offset)?;
            if sgx.try_read32(offset)? != value {
                return Err(EIO);
            }
        }
        drop(sgx_guard);
        // Slot zero's high root is empty. Firmware keeps using its separate
        // bootstrap root from the loader reservation, not this context entry.
        unsafe {
            ttbs.ptr()
                .cast::<u64>()
                .write_volatile(address_space.low.ttb() | 1);
            ttbs.ptr()
                .add(8)
                .cast::<u64>()
                .write_volatile(address_space.high.ttb() | 1);
            core::arch::asm!("dc cvac, {addr}", addr = in(reg) ttbs.ptr());
        }
        crate::mem::sync();
        crate::mem::tlbi_asid(0);
        crate::mem::sync();
        self._rtkit
            .as_mut()
            .send_message(0x20, 0x0081_0c20_0010_0000)?;
        dev_info!(dev, "G16: published Linux-built initialization graph\n");
        for _ in 0..3000 {
            if firmware_space.read_u32(crate::g16_fw::RootPointers::bootstrap().status_a + 0x14)?
                == 1
            {
                dev_info!(dev, "G16: firmware accepted initialization graph\n");
                return Ok(());
            }
            kernel::time::delay::fsleep(kernel::time::Delta::from_millis(1));
        }
        dev_err!(
            dev,
            "G16: initialization timeout; status {:#x}/{:#x}/{:#x}\n",
            firmware_space.read_u32(0xffff_fc20_0003_f190)?,
            firmware_space.read_u32(0xffff_fc20_0003_f194)?,
            firmware_space.read_u32(0xffff_fc20_0003_f198)?
        );
        Err(ETIMEDOUT)
    }
}
