// SPDX-License-Identifier: GPL-2.0-only
use alsa::ctl::{Ctl, ElemId, ElemIface, ElemType, ElemValue};
use std::ffi::CString;
use std::time::Instant;

pub const FRAMES: usize = 1024;
pub const BUFFER_FRAMES: usize = 2048;
const MODEL_FRAMES: usize = 512;
const MAX_BACKLOG: i64 = 512;
const CHANNELS: usize = 8;
const RATE: u64 = 48000;
const LEASE_NS: u64 = 50_000_000;

pub fn level(reduction: f32) -> f32 {
    assert!(reduction.is_finite() && reduction <= 0.0, "J713 invalid thermal reduction");
    reduction
}

pub fn model_gain(samples: &[i16], mut run: impl FnMut(&[i16]) -> f32) -> f32 {
    assert_eq!(samples.len(), FRAMES * CHANNELS, "J713 model geometry");
    samples.chunks_exact(MODEL_FRAMES * CHANNELS).map(|block| {
        let gain = run(block);
        assert!(gain.is_finite() && gain <= 0.0, "J713 invalid model gain");
        gain
    }).reduce(f32::min).unwrap()
}

fn value(name: &str) -> (ElemId, ElemValue) {
    let mut id = ElemId::new(ElemIface::Mixer);
    id.set_name(&CString::new(name).unwrap());
    let mut value = ElemValue::new(ElemType::Integer64).unwrap();
    value.set_id(&id);
    (id, value)
}

#[derive(Default)]
struct Freshness {
    generation: i64,
    sequence: i64,
    admitted: bool,
    pending: bool,
}

impl Freshness {
    fn period(&mut self, before: i64, after: i64, frames: usize, available: i64,
              read_ns: u64, rail: bool, rate: i32) -> bool {
        self.pending = false;
        assert!(frames == FRAMES && available >= 0, "J713 capture geometry/state");
        if before <= 0 || after <= 0 || before != after || rate == 0 {
            self.generation = 0;
            self.sequence = 0;
            self.admitted = false;
            return false;
        }
        assert_eq!(rate, RATE as i32, "J713 active sample rate");
        if self.generation != after {
            self.generation = after;
            self.sequence = 0;
            self.admitted = false;
        }
        if !self.admitted {
            // Discard the boundary period and all queued old-generation data.
            self.admitted = available == 0;
            return false;
        }
        assert!(available <= MAX_BACKLOG && read_ns < LEASE_NS,
                "J713 stale capture backlog/read");
        assert!(!rail, "J713 feedback rail");
        self.pending = true;
        true
    }

    fn renewal(&mut self, generation: i64, available: i64, processing_ns: u64) -> [i64; 2] {
        assert!(std::mem::take(&mut self.pending), "J713 no new period");
        assert!(self.admitted && self.generation > 0 && generation == self.generation,
                "J713 generation changed before renewal");
        assert!(available >= 0 && available <= MAX_BACKLOG, "J713 backlog before renewal");
        let oldest_ns = ((available as u64 + FRAMES as u64) * 1_000_000_000 + RATE - 1) / RATE;
        assert!(processing_ns.checked_add(oldest_ns).unwrap() < LEASE_NS,
                "J713 feedback too old for renewal");
        self.sequence = self.sequence.checked_add(1).expect("J713 sequence exhausted");
        [self.generation, self.sequence]
    }
}

pub struct Lease {
    generation_value: ElemValue,
    lease_value: ElemValue,
    freshness: Freshness,
    completed: Option<Instant>,
    monitors_generation: i64,
}

pub struct Read {
    generation: i64,
    started: Instant,
}

impl Lease {
    pub fn new(ctl: &Ctl, channels: usize, frames: usize, pcm: usize) -> Self {
        assert!(channels == CHANNELS && frames == FRAMES && pcm == 0,
                "J713 protection profile geometry");
        let (_, generation_value) = value("Speaker Protection Generation");
        let (lease_id, lease_value) = value("Speaker Protection Lease");
        // The caller already owns Volume Unlock and all limited child controls.
        ctl.elem_lock(&lease_id).expect("J713 lease exclusive ownership");
        Self { generation_value, lease_value, freshness: Freshness::default(), completed: None, monitors_generation: 0 }
    }

    fn generation(&mut self, ctl: &Ctl) -> i64 {
        ctl.elem_read(&mut self.generation_value).expect("J713 generation read");
        self.generation_value.get_integer64(0).expect("J713 generation type")
    }

    pub fn begin(&mut self, ctl: &Ctl) -> Read {
        self.completed = None;
        Read { generation: self.generation(ctl), started: Instant::now() }
    }

    pub fn period(&mut self, ctl: &Ctl, read: Read, frames: usize, available: i64,
                  rate: i32, samples: &[i16]) -> bool {
        assert!(samples.len() == FRAMES * CHANNELS, "J713 frame length");
        let generation = self.generation(ctl);
        let accepted = self.freshness.period(read.generation, generation, frames, available,
            read.started.elapsed().as_nanos().try_into().unwrap(),
            samples.iter().any(|x| *x == i16::MIN || *x == i16::MAX), rate);
        if accepted { self.completed = Some(Instant::now()); }
        accepted
    }

    pub fn needs_monitors(&self) -> bool {
        self.monitors_generation != self.freshness.generation
    }
    pub fn monitors_checked(&mut self) {
        assert!(self.completed.is_some() && self.freshness.generation > 0);
        self.monitors_generation = self.freshness.generation;
    }
    pub fn renew(&mut self, ctl: &Ctl, available: i64) {
        assert_eq!(self.monitors_generation, self.freshness.generation, "J713 monitors unchecked");
        let completed = self.completed.take().expect("J713 no validated period");
        let generation = self.generation(ctl);
        let pair = self.freshness.renewal(generation, available,
            completed.elapsed().as_nanos().try_into().unwrap());
        self.lease_value.set_integer64(0, pair[0]).unwrap();
        self.lease_value.set_integer64(1, pair[1]).unwrap();
        ctl.elem_write(&self.lease_value).expect("J713 lease rejected");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn ready() -> Freshness {
        let mut s = Freshness::default();
        assert!(!s.period(1, 1, FRAMES, 0, 1, false, 48000));
        assert!(s.period(1, 1, FRAMES, 0, 10_666_667, false, 48000));
        s
    }
    #[test]
    fn candidate_profile_geometry_and_models() {
        let mut c = configparser::ini::Ini::new_cs();
        c.read(include_str!("../../j713.conf").to_string()).unwrap();
        let g = crate::types::Globals::parse(&c);
        assert_eq!((g.channels, g.period, g.visense_pcm), (8, 1024, 0));
        assert_eq!(g.t_ambient, 50.0);
        for (index, name) in ["Left Woofer", "Right Woofer", "Left Tweeter", "Right Tweeter"].iter().enumerate() {
            let section = format!("Speaker/{}", name);
            assert_eq!(crate::helpers::parse_int::<usize>(&c, &section, "vs_chan"), index * 2);
            assert_eq!(crate::helpers::parse_int::<usize>(&c, &section, "is_chan"), index * 2 + 1);
            let expected = if index < 2 { (32.1, 20.0, 4.7, 66.5, 2.8) } else { (140.0, 200.0, 1.47, 52.0, 2.7) };
            for (key, val) in [("tr_coil",expected.0),("tr_magnet",expected.1),("tau_coil",expected.2),("tau_magnet",expected.3),("z_nominal",expected.4),("t_limit",120.0),("t_headroom",5.0),("vs_scale",14.0),("is_scale",3.75)] {
                assert_eq!(crate::helpers::parse_float(&c, &section, key), val);
            }
        }
    }
    #[test]
    fn idle_and_wrong_active_rate() {
        let mut s = ready();
        assert!(!s.period(1, 1, FRAMES, 0, 1, false, 0));
        assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(||s.renewal(1,0,1))).is_err());
        for rate in [44100, -1] {
            let mut s = ready();
            assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(||s.period(1,1,FRAMES,0,1,false,rate))).is_err());
        }
    }
    #[test]
    fn thermal_level() {
        assert_eq!(level(0.0), 0.0);
        assert_eq!(level(-12.0), -12.0);
        for bad in [0.5, f32::NAN, f32::INFINITY] {
            assert!(std::panic::catch_unwind(||level(bad)).is_err());
        }
    }
    #[test]
    fn silence_and_sequence() {
        let mut s = ready();
        assert_eq!(s.renewal(1, 0, 1), [1, 1]);
        assert!(s.period(1, 1, FRAMES, 0, 10_666_667, false, 48000));
        assert_eq!(s.renewal(1, 0, 1), [1, 2]);
        assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(||s.renewal(1,0,1))).is_err());
    }
    #[test]
    fn capture_only_idle_keeps_epoch_over_bootstrap_deadline() {
        let mut s = ready();
        for sequence in 1..=300 {
            assert_eq!(s.renewal(1, 0, 3_000_000), [1, sequence]);
            assert!(s.period(1, 1, FRAMES, 0, 10_666_667, false, 48000));
        }
        assert!(!s.period(1, 0, FRAMES, 0, 1, false, 0));
        assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(||s.renewal(1,0,1))).is_err());
    }
    #[test]
    fn no_epoch_and_read_boundary() {
        let mut s = Freshness::default();
        assert!(!s.period(0, 0, FRAMES, 0, 1, false, 48000));
        assert!(!s.period(0, 0, FRAMES, 0, 1, false, 48000));
        let mut s = ready();
        assert!(!s.period(2, 1, FRAMES, 0, 1, false, 48000));
        assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(||s.renewal(1,0,1))).is_err());
    }
    #[test]
    fn boundary_and_backlog_discard() {
        let mut s = ready();
        assert!(!s.period(1, 2, FRAMES, 512, 1, false, 48000));
        assert!(!s.period(2, 2, FRAMES, 512, 1, false, 48000));
        assert!(!s.period(2, 2, FRAMES, 0, 1, false, 48000));
        assert!(s.period(2, 2, FRAMES, 0, 1, false, 48000));
        assert_eq!(s.renewal(2, 0, 1), [2, 1]);
        assert!(!s.period(2, 0, FRAMES, 0, 1, false, 48000));
    }
    #[test]
    fn refusal_boundaries() {
        for (available, age, rail) in [(513, 1, false), (0, LEASE_NS, false), (0, 1, true)] {
            let mut s = ready();
            assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(||s.period(1,1,FRAMES,available,age,rail, 48000))).is_err());
        }
        for (generation, available, processing) in [(2,0,1),(1,513,1),(1,0,40_000_000)] {
            let mut s = ready();
            assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(||s.renewal(generation,available,processing))).is_err());
        }
        let mut s=ready();s.sequence=i64::MAX;
        assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(||s.renewal(1,0,1))).is_err());
    }
}
