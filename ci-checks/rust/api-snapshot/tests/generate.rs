//! Runs the generator on the fixture projects in tests/fixtures. Needs the nightly named in ../api-snapshot.toml
//! (RUSTUP_TOOLCHAIN), because rustdoc JSON is unstable.

use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name)
}

fn temp(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("api-snapshot-test-{}-{tag}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

struct Run {
    code: i32,
    stderr: String,
    output: PathBuf,
}

impl Run {
    fn text(&self) -> String {
        assert_eq!(self.code, 0, "generator failed: {}", self.stderr);
        fs::read_to_string(&self.output).unwrap()
    }

    fn entries(&self) -> Vec<Value> {
        let entries: Vec<Value> = serde_json::from_str(&self.text()).unwrap();
        for entry in &entries {
            let fields: Vec<&str> = entry
                .as_object()
                .unwrap()
                .keys()
                .map(String::as_str)
                .collect();
            assert_eq!(fields.len(), 5, "{entry}");
            for field in ["unit", "kind", "parent", "name", "signature"] {
                assert!(entry[field].is_string(), "{field} in {entry}");
            }
        }
        entries
    }
}

fn generate_in(project: &Path, tag: &str) -> Run {
    let dir = temp(tag);
    let output = dir.join("entries.json");
    let result = Command::new(env!("CARGO_BIN_EXE_api-snapshot"))
        .arg("--project")
        .arg(project)
        .arg("--output")
        .arg(&output)
        .env("CARGO_TARGET_DIR", dir.join("target"))
        .output()
        .unwrap();
    Run {
        code: result.status.code().unwrap_or(-1),
        stderr: String::from_utf8_lossy(&result.stderr).into_owned(),
        output,
    }
}

fn generate(name: &str, tag: &str) -> Run {
    generate_in(&fixture(name), tag)
}

fn signature<'a>(entries: &'a [Value], kind: &str, parent: &str, name: &str) -> &'a str {
    let found: Vec<&Value> = entries
        .iter()
        .filter(|e| e["kind"] == kind && e["parent"] == parent && e["name"] == name)
        .collect();
    assert_eq!(found.len(), 1, "{kind} {parent:?} {name:?} in {entries:#?}");
    found[0]["signature"].as_str().unwrap()
}

/// Runs the shared comparer; returns its exit code, log and the snapshot it wrote (when any).
fn compare(entries: &Path, snapshot: &Path, tag: &str) -> (i32, String, Option<String>) {
    let dir = temp(tag);
    let output = dir.join("new.json");
    let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../api-snapshot/api_snapshot.py");
    let result = Command::new("python3")
        .arg(script)
        .arg("--entries")
        .arg(entries)
        .arg("--snapshot")
        .arg(snapshot)
        .arg("--output")
        .arg(&output)
        .args([
            "--artifact",
            "rust-api-snapshot-test",
            "--path",
            ".github/checks/api-rust.json",
        ])
        .env_remove("GITHUB_STEP_SUMMARY")
        .output()
        .unwrap();
    (
        result.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&result.stdout).into_owned(),
        fs::read_to_string(output).ok(),
    )
}

fn assert_fails_with_2(run: &Run) {
    assert_eq!(run.code, 2, "{}", run.stderr);
    assert!(run.stderr.contains("api-snapshot: "), "{}", run.stderr);
    assert!(!run.output.exists());
}

#[test]
fn public_entries_and_order() {
    let run = generate("basic", "basic");
    let entries = run.entries();
    let open = entries.iter().find(|e| e["name"] == "open").unwrap();
    assert_eq!(
        open,
        &serde_json::json!({
            "unit": "basic", "kind": "function", "parent": "", "name": "open",
            "signature": "pub fn open(&str) -> u32",
        })
    );
    assert!(!run.text().contains("helper"));
    assert!(!run.text().contains("crate_only"));
    assert_eq!(
        signature(&entries, "module", "", "shapes"),
        "pub mod shapes"
    );
    assert_eq!(
        signature(&entries, "function", "shapes", "area"),
        "pub fn area() -> f64"
    );
    assert_eq!(
        signature(&entries, "constant", "", "MAX"),
        "pub const MAX: u64"
    );
    assert_eq!(
        signature(&entries, "struct", "", "Point"),
        "pub struct Point { pub x: i32, /* private fields */ }"
    );

    let (code, _, snapshot) = compare(
        &run.output,
        &temp("basic-missing").join("none.json"),
        "basic-compare",
    );
    assert_eq!(code, 1);
    let snapshot: Vec<Value> = serde_json::from_str(&snapshot.unwrap()).unwrap();
    let keys: Vec<[String; 4]> = snapshot
        .iter()
        .map(|e| ["unit", "parent", "kind", "name"].map(|f| e[f].as_str().unwrap().to_owned()))
        .collect();
    let mut sorted = keys.clone();
    sorted.sort();
    assert_eq!(keys, sorted);
    assert_eq!(snapshot.len(), entries.len());
}

#[test]
fn optional_feature_included() {
    let entries = generate("features", "features").entries();
    assert_eq!(
        signature(&entries, "function", "", "extra_fn"),
        "pub fn extra_fn()"
    );
}

#[test]
fn workspace_lib_members_only() {
    let entries = generate("workspace", "workspace").entries();
    assert_eq!(signature(&entries, "function", "", "a"), "pub fn a()");
    assert!(entries.iter().all(|e| e["unit"] == "lib-a"), "{entries:#?}");
}

#[test]
fn build_failure_exits_2() {
    assert_fails_with_2(&generate("broken", "broken"));
}

#[test]
fn conflicting_features_exit_2() {
    assert_fails_with_2(&generate("conflicting", "conflicting"));
}

#[test]
fn no_manifest_exits_2() {
    assert_fails_with_2(&generate_in(&temp("empty-project"), "no-manifest"));
}
