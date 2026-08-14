#![forbid(unsafe_code)]

pub mod v1 {
    include!(concat!(env!("OUT_DIR"), "/ump.v1.rs"));
}
