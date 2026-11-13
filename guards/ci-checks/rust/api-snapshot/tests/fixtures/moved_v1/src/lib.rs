pub struct Config {
    pub depth: u8,
}

/// Opens with the default depth.
pub fn open(config: &Config) -> u8 {
    config.depth
}
