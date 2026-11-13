//! A library with public and private items.

/// Opens a path.
pub fn open(path: &str) -> u32 {
    helper();
    path.len() as u32
}

fn helper() {}

pub struct Point {
    pub x: i32,
    y: i32,
}

impl Point {
    pub fn y(&self) -> i32 {
        self.y
    }
}

pub mod shapes {
    pub fn area() -> f64 {
        0.0
    }

    pub(crate) fn crate_only() {}
}

pub const MAX: u64 = 5;
