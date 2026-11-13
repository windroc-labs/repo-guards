use crate::Config;

/// Opens a config at its depth; the docs changed and the function moved files.
pub fn open(config: &Config) -> u8 {
    config.depth
}
