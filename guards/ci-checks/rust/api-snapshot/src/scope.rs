//! The modules to snapshot: `api-snapshot-scope.rust` in the caller's repo-guards.toml section
//! `[project."<working-directory>"]`, module path prefixes such as `kvm_op::driver`. Unset means the whole crate.

use std::path::Path;
use toml::{Table, Value};

const KEY: &str = "api-snapshot-scope";
const LANGUAGE: &str = "rust";

/// Each prefix split into `::` segments; `None` is the whole crate.
#[derive(Debug, PartialEq)]
pub struct Scope(Option<Vec<Vec<String>>>);

pub const WHOLE: Scope = Scope(None);

/// Reads the scope; a missing file, section or setting is the whole crate. A setting that is not a non-empty array
/// of module paths is an error naming it, as config_overrides.py words it for the other settings.
pub fn read(config: &Path, working_directory: &str) -> Result<Scope, String> {
    let text = match std::fs::read_to_string(config) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(WHOLE),
        Err(e) => return Err(format!("cannot read {}: {e}", config.display())),
    };
    let table: Table = text
        .parse()
        .map_err(|e: toml::de::Error| format!("{}: {e}", config.display()))?;
    from_table(&table, working_directory)
}

fn from_table(table: &Table, working_directory: &str) -> Result<Scope, String> {
    let section = format!(
        "[project.{}]",
        serde_json::to_string(working_directory).expect("a string serializes")
    );
    let Some(project) = sub_table(table, "project", "[project]")? else {
        return Ok(WHOLE);
    };
    let Some(settings) = sub_table(project, working_directory, &section)? else {
        return Ok(WHOLE);
    };
    let Some(languages) = sub_table(settings, KEY, &format!("{section} {KEY}"))? else {
        return Ok(WHOLE);
    };
    let Some(value) = languages.get(LANGUAGE) else {
        return Ok(WHOLE);
    };
    let at = format!("{section} {KEY}.{LANGUAGE}");
    let prefixes: Vec<&str> = value
        .as_array()
        .and_then(|values| values.iter().map(Value::as_str).collect())
        .ok_or_else(|| format!("{at} is not an array of strings"))?;
    if prefixes.is_empty() {
        return Err(format!(
            "{at} = []: must list at least one module prefix; leave it unset to freeze the whole project"
        ));
    }
    for prefix in &prefixes {
        if let Some(error) = prefix_error(prefix) {
            return Err(format!(
                "{at}: {}: {error}",
                serde_json::to_string(prefix).expect("a string serializes")
            ));
        }
    }
    Ok(Scope(Some(
        prefixes
            .iter()
            .map(|p| p.split("::").map(str::to_owned).collect())
            .collect(),
    )))
}

fn sub_table<'a>(table: &'a Table, key: &str, name: &str) -> Result<Option<&'a Table>, String> {
    match table.get(key) {
        None => Ok(None),
        Some(Value::Table(sub)) => Ok(Some(sub)),
        Some(_) => Err(format!("{name} is not a table")),
    }
}

/// Why a prefix is not a module path the generator can name, if it is not.
fn prefix_error(prefix: &str) -> Option<&'static str> {
    if prefix.chars().any(char::is_control) {
        return Some("must not contain control characters");
    }
    let identifier = |segment: &str| {
        let mut chars = segment.chars();
        chars
            .next()
            .is_some_and(|c| unicode_ident::is_xid_start(c) || c == '_')
            && chars.all(unicode_ident::is_xid_continue)
    };
    if prefix.split("::").all(identifier) {
        None
    } else {
        Some(r#"must be a module path such as "kvm_op::driver""#)
    }
}

impl Scope {
    /// Whether the module `crate_name::module` is a prefix or under one, segment by segment.
    pub fn contains(&self, crate_name: &str, module: &[String]) -> bool {
        let Some(prefixes) = &self.0 else {
            return true;
        };
        prefixes.iter().any(|prefix| {
            prefix.len() <= module.len() + 1
                && std::iter::once(crate_name)
                    .chain(module.iter().map(String::as_str))
                    .zip(prefix)
                    .all(|(segment, want)| segment == want)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(text: &str) -> Result<Scope, String> {
        from_table(&text.parse().unwrap(), "crates/kvm")
    }

    fn scope(prefixes: &str) -> Scope {
        parse(&format!(
            "[project.\"crates/kvm\"]\napi-snapshot-scope.rust = {prefixes}\n"
        ))
        .unwrap()
    }

    fn path(module: &str) -> Vec<String> {
        module
            .split("::")
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .collect()
    }

    #[test]
    fn missing_file_section_or_setting_is_the_whole_crate() {
        let missing =
            std::env::temp_dir().join(format!("api-snapshot-none-{}.toml", std::process::id()));
        assert_eq!(read(&missing, "."), Ok(WHOLE));
        let directory = read(&std::env::temp_dir(), ".").unwrap_err();
        assert!(directory.starts_with("cannot read "), "{directory}");
        for text in [
            "",
            "[\"ci-checks/coverage/coverage.toml\"]\ntotal-min = 70\n",
            "[project.\".\"]\napi-snapshot-scope.rust = [\"kvm_op\"]\n",
            "[project.\"crates/kvm\"]\napi-snapshot.rust = \".github/checks/api.json\"\n",
            "[project.\"crates/kvm\"]\napi-snapshot-scope.python = [\"pkg\"]\n",
        ] {
            assert_eq!(parse(text), Ok(WHOLE), "{text}");
        }
        assert!(WHOLE.contains("kvm_op", &path("anything::below")));
    }

    #[test]
    fn unreadable_settings_are_errors() {
        let at = r#"[project."crates/kvm"] api-snapshot-scope.rust"#;
        for (text, error) in [
            ("project = 1", "[project] is not a table".to_owned()),
            (
                "[project]\n\"crates/kvm\" = 1",
                r#"[project."crates/kvm"] is not a table"#.to_owned(),
            ),
            (
                "[project.\"crates/kvm\"]\napi-snapshot-scope = [\"kvm_op\"]",
                r#"[project."crates/kvm"] api-snapshot-scope is not a table"#.to_owned(),
            ),
            (
                "[project.\"crates/kvm\"]\napi-snapshot-scope.rust = \"kvm_op\"",
                format!("{at} is not an array of strings"),
            ),
            (
                "[project.\"crates/kvm\"]\napi-snapshot-scope.rust = [\"kvm_op\", 1]",
                format!("{at} is not an array of strings"),
            ),
            (
                "[project.\"crates/kvm\"]\napi-snapshot-scope.rust = []",
                format!(
                    "{at} = []: must list at least one module prefix; leave it unset to freeze the whole project"
                ),
            ),
        ] {
            assert_eq!(parse(text), Err(error), "{text}");
        }
    }

    #[test]
    fn prefixes_must_be_module_paths() {
        for (prefix, error) in [
            ("kvm_op::", "must be a module path"),
            ("kvm-op", "must be a module path"),
            ("kvm_op.driver", "must be a module path"),
            ("kvm_op::driver::*", "must be a module path"),
            ("::kvm_op", "must be a module path"),
            ("1kvm", "must be a module path"),
            ("kvm²", "must be a module path"),
            ("", "must be a module path"),
            ("kvm_op\\u001b[31m", "must not contain control characters"),
        ] {
            let text = format!(
                "[project.\"crates/kvm\"]\napi-snapshot-scope.rust = [\"kvm_op\", \"{prefix}\"]"
            );
            let message = parse(&text).unwrap_err();
            assert!(
                message.starts_with(r#"[project."crates/kvm"] api-snapshot-scope.rust: ""#),
                "{message}"
            );
            assert!(message.contains(error), "{prefix:?}: {message}");
        }
        assert!(scope(r#"["_private::ünï::a·b"]"#).contains("_private", &path("ünï::a·b")));
    }

    #[test]
    fn prefixes_match_by_segment() {
        let scope = scope(r#"["kvm_op::driver", "kvm_op::net::tcp"]"#);
        assert!(scope.contains("kvm_op", &path("driver")));
        assert!(scope.contains("kvm_op", &path("driver::io")));
        assert!(scope.contains("kvm_op", &path("net::tcp")));
        assert!(!scope.contains("kvm_op", &path("driverx")));
        assert!(!scope.contains("kvm_op", &path("")));
        assert!(!scope.contains("kvm_op", &path("net")));
        assert!(!scope.contains("other", &path("driver")));
    }

    #[test]
    fn crate_prefix_covers_its_root() {
        let scope = scope(r#"["kvm_op"]"#);
        assert!(scope.contains("kvm_op", &path("")));
        assert!(scope.contains("kvm_op", &path("driver::io")));
        assert!(!scope.contains("kvm", &path("")));
    }
}
