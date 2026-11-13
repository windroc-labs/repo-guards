pub mod driver {
    pub use core::cell::Cell;

    pub struct Id(pub u32);

    impl Id {
        pub fn get(&self) -> u32 {
            self.0
        }
    }

    pub trait Device {
        fn open(&self) -> bool;
    }

    impl Device for Id {
        fn open(&self) -> bool {
            true
        }
    }

    pub mod io {
        pub fn read() -> u8 {
            0
        }
    }
}

pub mod driverx {
    pub fn other() {}

    pub fn added() {}
}

pub mod net {
    pub use crate::driver::Id as DriverId;
    pub use core::cell::RefCell;

    #[derive(Clone)]
    pub struct Socket;

    impl From<crate::driver::Id> for Socket {
        fn from(_: crate::driver::Id) -> Self {
            Socket
        }
    }

    pub trait Ping {
        fn ping(&self);
    }

    impl Ping for &crate::driver::Id {
        fn ping(&self) {}
    }

    impl Ping for Socket {
        fn ping(&self) {}
    }

    pub trait Wrap<T> {
        fn wrap(t: T) -> Self;
    }

    impl Wrap<crate::driver::Id> for u8 {
        fn wrap(_: crate::driver::Id) -> Self {
            0
        }
    }
}

pub use driver::io::read;

pub fn top() {}
