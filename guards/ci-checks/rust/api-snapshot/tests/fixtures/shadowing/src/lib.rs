pub use core::cmp::Ordering;
pub use core::cmp::Reverse;
pub use inner::*;

pub enum Local {
    A,
}

pub mod inner {
    pub struct Reverse;

    pub struct Ordering {
        pub rank: u8,
    }

    pub struct Local {
        pub x: u8,
    }

    pub fn only_glob() {}
}
