#[cfg(all(feature = "a", feature = "b"))]
compile_error!("features a and b are mutually exclusive");

pub fn open() {}
