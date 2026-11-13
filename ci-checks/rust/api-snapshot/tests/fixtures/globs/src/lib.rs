pub use g1::*;
pub use g2::*;
pub use inner::*;

pub enum Local {
    A,
}

impl Local {
    pub fn new() -> Self {
        Local::A
    }
}

pub mod inner {
    pub struct Local(pub u8);

    impl Local {
        pub fn new() -> Self {
            Local(0)
        }
    }
}

pub mod g1 {
    pub use core::ops::*;
}

pub mod g2 {
    pub use core::ops::*;
}

mod hidden {
    pub struct Tie;
}

pub mod t {
    pub use crate::hidden::Tie;
}

pub mod t0 {
    pub use crate::hidden::Tie;
}
