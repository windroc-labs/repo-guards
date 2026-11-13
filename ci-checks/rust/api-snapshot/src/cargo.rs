use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// Target kinds that make a package a library unit.
const LIB_KINDS: [&str; 6] = ["lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro"];

pub struct LibCrate {
    pub package: String,
    id: String,
}

fn is_lib(target: &Value) -> bool {
    target["kind"].as_array().is_some_and(|kinds| {
        kinds
            .iter()
            .any(|k| LIB_KINDS.contains(&k.as_str().unwrap_or("")))
    })
}

/// Runs cargo in the project with stderr passed through to the log; returns stdout.
fn cargo(project: &Path, args: &[&str]) -> Result<String, String> {
    let output = Command::new("cargo")
        .args(args)
        .current_dir(project)
        .stdin(Stdio::null())
        .stderr(Stdio::inherit())
        .output()
        .map_err(|e| format!("cannot run cargo: {e}"))?;
    if !output.status.success() {
        return Err(format!("cargo {} failed ({})", args[0], output.status));
    }
    String::from_utf8(output.stdout)
        .map_err(|_| format!("cargo {} printed non-UTF-8 output", args[0]))
}

/// The workspace members with a library target, by package name.
pub fn lib_crates(project: &Path) -> Result<Vec<LibCrate>, String> {
    let text = cargo(
        project,
        &["metadata", "--format-version", "1", "--no-deps", "--locked"],
    )?;
    let metadata: Value =
        serde_json::from_str(&text).map_err(|e| format!("cargo metadata: {e}"))?;
    let members: Vec<&str> = metadata["workspace_members"]
        .as_array()
        .ok_or("cargo metadata: no workspace_members")?
        .iter()
        .filter_map(Value::as_str)
        .collect();
    let mut crates: Vec<LibCrate> = metadata["packages"]
        .as_array()
        .ok_or("cargo metadata: no packages")?
        .iter()
        .filter(|p| members.contains(&p["id"].as_str().unwrap_or("")))
        .filter(|p| {
            p["targets"]
                .as_array()
                .is_some_and(|t| t.iter().any(is_lib))
        })
        .map(|p| LibCrate {
            package: p["name"].as_str().unwrap_or("").to_owned(),
            id: p["id"].as_str().unwrap_or("").to_owned(),
        })
        .collect();
    crates.sort_by(|a, b| a.package.cmp(&b.package));
    Ok(crates)
}

/// Builds the crate's library with every feature on, then its docs as rustdoc JSON; returns the JSON file cargo
/// reports. rustdoc alone skips function bodies, so the check is what fails on code that does not compile.
pub fn rustdoc_json(project: &Path, krate: &LibCrate) -> Result<PathBuf, String> {
    cargo(
        project,
        &[
            "check",
            "--locked",
            "--all-features",
            "-p",
            &krate.id,
            "--lib",
            "--message-format",
            "short",
        ],
    )?;
    let text = cargo(
        project,
        &[
            "rustdoc",
            "--locked",
            "--all-features",
            "-p",
            &krate.id,
            "--lib",
            "-Z",
            "unstable-options",
            "--output-format",
            "json",
            "--message-format",
            "json-render-diagnostics",
        ],
    )?;
    for line in text.lines() {
        let Ok(message) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if message["reason"] != "compiler-artifact"
            || message["package_id"] != krate.id.as_str()
            || !is_lib(&message["target"])
        {
            continue;
        }
        let json = message["filenames"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .find(|f| f.ends_with(".json"));
        if let Some(json) = json {
            return Ok(PathBuf::from(json));
        }
    }
    Err(format!(
        "cargo rustdoc reported no JSON file for {}",
        krate.package
    ))
}
