use rustdoc_types::{Crate, FORMAT_VERSION};
use serde::Deserialize;
use std::path::Path;

#[derive(Deserialize)]
struct Version {
    format_version: u32,
}

/// Reads a rustdoc JSON file, checking its format version before the full parse so a toolchain mismatch says so.
pub fn load(path: &Path) -> Result<Crate, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let version: Version = serde_json::from_str(&text)
        .map_err(|e| format!("{} is not rustdoc JSON: {e}", path.display()))?;
    if version.format_version != FORMAT_VERSION {
        return Err(format!(
            "rustdoc JSON format {}, expected {FORMAT_VERSION}: the toolchain in api-snapshot.toml does not match rustdoc-types",
            version.format_version
        ));
    }
    serde_json::from_str(&text).map_err(|e| {
        format!(
            "{} is not rustdoc JSON format {FORMAT_VERSION}: {e}",
            path.display()
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(name: &str, text: &str) -> std::path::PathBuf {
        let path = std::env::temp_dir().join(format!(
            "api-snapshot-rustdoc-{}-{name}.json",
            std::process::id()
        ));
        std::fs::write(&path, text).unwrap();
        path
    }

    #[test]
    fn format_version_mismatch_is_error() {
        let error = load(&write("old", r#"{"format_version": 60}"#)).unwrap_err();
        assert_eq!(
            error,
            format!(
                "rustdoc JSON format 60, expected {FORMAT_VERSION}: the toolchain in api-snapshot.toml does not match rustdoc-types"
            )
        );
    }

    #[test]
    fn not_json_is_error() {
        assert!(
            load(&write("bad", "not json"))
                .unwrap_err()
                .contains("is not rustdoc JSON")
        );
    }
}
