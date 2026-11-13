use rustdoc_types::{Crate, FORMAT_VERSION, Id, Item, ItemKind};
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

pub fn item<'a>(krate: &'a Crate, id: &Id) -> Result<&'a Item, String> {
    krate
        .index
        .get(id)
        .ok_or_else(|| format!("rustdoc JSON has no item {}", id.0))
}

/// rustdoc's own name for each kind, as its JSON spells it.
pub fn kind_name(kind: ItemKind) -> &'static str {
    match kind {
        ItemKind::Module => "module",
        ItemKind::ExternCrate => "extern_crate",
        ItemKind::Use => "use",
        ItemKind::Struct => "struct",
        ItemKind::StructField => "struct_field",
        ItemKind::Union => "union",
        ItemKind::Enum => "enum",
        ItemKind::Variant => "variant",
        ItemKind::Function => "function",
        ItemKind::TypeAlias => "type_alias",
        ItemKind::Constant => "constant",
        ItemKind::Trait => "trait",
        ItemKind::TraitAlias => "trait_alias",
        ItemKind::Impl => "impl",
        ItemKind::Static => "static",
        ItemKind::ExternType => "extern_type",
        ItemKind::Macro => "macro",
        ItemKind::ProcAttribute => "proc_attribute",
        ItemKind::ProcDerive => "proc_derive",
        ItemKind::AssocConst => "assoc_const",
        ItemKind::AssocType => "assoc_type",
        ItemKind::Primitive => "primitive",
        ItemKind::Keyword => "keyword",
        ItemKind::Attribute => "attribute",
    }
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
