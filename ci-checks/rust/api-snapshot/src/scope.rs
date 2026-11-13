//! The modules to snapshot, from `API_SNAPSHOT_SCOPE`: a JSON array of module path prefixes such as
//! `kvm_op::driver`. Unset, blank or `[]` means the whole crate.

use std::ffi::OsString;

pub const VAR: &str = "API_SNAPSHOT_SCOPE";

/// Each prefix split into `::` segments; `None` is the whole crate.
#[derive(Debug, PartialEq)]
pub struct Scope(Option<Vec<Vec<String>>>);

impl Scope {
    pub fn parse(value: Option<OsString>) -> Result<Scope, String> {
        let Some(value) = value else {
            return Ok(Scope(None));
        };
        let text = value
            .into_string()
            .map_err(|_| format!("{VAR} is not UTF-8"))?;
        if text.trim().is_empty() {
            return Ok(Scope(None));
        }
        let prefixes: Vec<String> = serde_json::from_str(&text)
            .map_err(|e| format!("{VAR} is not a JSON array of strings: {e}"))?;
        Ok(Scope(if prefixes.is_empty() {
            None
        } else {
            Some(
                prefixes
                    .iter()
                    .map(|p| p.split("::").map(str::to_owned).collect())
                    .collect(),
            )
        }))
    }

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

    fn parse(value: &str) -> Result<Scope, String> {
        Scope::parse(Some(value.into()))
    }

    fn path(module: &str) -> Vec<String> {
        module
            .split("::")
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .collect()
    }

    #[test]
    fn unset_blank_and_empty_are_the_whole_crate() {
        for scope in [Scope::parse(None), parse(""), parse(" \n"), parse("[]")] {
            assert_eq!(scope, Ok(Scope(None)));
        }
        assert!(Scope(None).contains("kvm_op", &path("anything::below")));
    }

    #[test]
    fn unreadable_values_are_errors() {
        for value in [
            "kvm_op::driver",
            r#""kvm_op::driver""#,
            r#"{"kvm_op": 1}"#,
            r#"["kvm_op::driver", 1]"#,
            "[null]",
        ] {
            let error = parse(value).unwrap_err();
            assert!(
                error.starts_with("API_SNAPSHOT_SCOPE is not a JSON array of strings"),
                "{value}: {error}"
            );
        }
    }

    #[test]
    fn prefixes_match_by_segment() {
        let scope = parse(r#"["kvm_op::driver", "kvm_op::net::tcp"]"#).unwrap();
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
        let scope = parse(r#"["kvm_op"]"#).unwrap();
        assert!(scope.contains("kvm_op", &path("")));
        assert!(scope.contains("kvm_op", &path("driver::io")));
        assert!(!scope.contains("kvm", &path("")));
    }
}
