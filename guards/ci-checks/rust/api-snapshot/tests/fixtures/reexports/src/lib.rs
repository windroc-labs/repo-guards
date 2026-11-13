mod private {
    pub struct Hidden(pub u8);

    impl Hidden {
        pub fn get(&self) -> u8 {
            self.0
        }
    }

    pub fn make() -> Hidden {
        Hidden(0)
    }
}

pub use private::Hidden;
pub use private::make as build;

pub mod public {
    pub struct Twice;

    pub fn takes(t: &Twice) {
        let _ = t;
    }
}

pub use public::Twice;

mod globbed {
    pub struct FromGlob;
}

pub use globbed::*;

pub use core::fmt::Debug;

pub mod prelude {
    pub use core::fmt::*;
    pub use core::ops::*;
}

pub use crate as root;
