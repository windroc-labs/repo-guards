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

fn assert_keys_unique(entries: &[Value]) {
    let mut keys: Vec<[&str; 4]> = entries
        .iter()
        .map(|e| ["unit", "parent", "kind", "name"].map(|f| e[f].as_str().unwrap()))
        .collect();
    keys.sort();
    let count = keys.len();
    keys.dedup();
    assert_eq!(keys.len(), count, "duplicate keys in {entries:#?}");
}

const FROM_U32: &str = "impl core::convert::From<u32> for same_name::Id";
const FROM_STRING: &str = "impl core::convert::From<alloc::string::String> for same_name::Id";

#[test]
fn trait_impl_methods_have_impl_parents() {
    let entries = generate("same_name_v1", "same-name-from").entries();
    assert_keys_unique(&entries);
    assert_eq!(signature(&entries, "impl", "Id", FROM_U32), FROM_U32);
    assert_eq!(signature(&entries, "impl", "Id", FROM_STRING), FROM_STRING);
    assert_eq!(
        signature(&entries, "function", FROM_U32, "from"),
        "fn from(u32) -> Self"
    );
    assert_eq!(
        signature(&entries, "function", FROM_STRING, "from"),
        "fn from(alloc::string::String) -> Self"
    );
}

#[test]
fn inherent_and_trait_len_distinct() {
    let entries = generate("same_name_v1", "same-name-len").entries();
    assert_keys_unique(&entries);
    assert_eq!(
        signature(&entries, "function", "impl same_name::Id", "len"),
        "pub fn len(&self) -> usize"
    );
    assert_eq!(
        signature(
            &entries,
            "function",
            "impl same_name::Len for same_name::Id",
            "len"
        ),
        "fn len(&self) -> usize"
    );
    assert_eq!(
        signature(&entries, "function", "Len", "len"),
        "fn len(&self) -> usize"
    );
    assert_eq!(
        signature(&entries, "impl", "Id", "impl same_name::Id"),
        "impl same_name::Id"
    );
}

#[test]
fn one_signature_change_is_one_changed_entry() {
    let old = generate("same_name_v1", "same-name-old");
    let missing = temp("same-name-none").join("none.json");
    let (_, _, snapshot) = compare(&old.output, &missing, "same-name-first");
    let snapshot_file = temp("same-name-snapshot").join("api-rust.json");
    fs::write(&snapshot_file, snapshot.unwrap()).unwrap();

    let new = generate("same_name_v2", "same-name-new");
    assert_keys_unique(&new.entries());
    let (code, log, _) = compare(&new.output, &snapshot_file, "same-name-second");
    assert_eq!(code, 1, "{log}");
    let listed: Vec<&str> = log
        .lines()
        .filter(|l| l.starts_with("多 ") || l.starts_with("少 ") || l.starts_with("变 "))
        .collect();
    assert_eq!(listed.len(), 1, "{log}");
    assert!(listed[0].starts_with("变 "), "{log}");
    assert!(
        listed[0].contains(r#""parent": "impl same_name::Id""#),
        "{log}"
    );
    assert!(listed[0].contains(r#""name": "len""#), "{log}");
}

#[test]
fn signature_rendering() {
    let entries = generate("shapes", "shapes").entries();
    assert_keys_unique(&entries);
    let expected = [
        (
            "function",
            "",
            "generic",
            "pub fn generic<'a, T, const N: usize>(&'a T, fn(u8) -> u8, alloc::boxed::Box<dyn core::ops::function::Fn(u8) -> bool + core::marker::Send + 'a>, impl core::iter::traits::iterator::Iterator<Item = u8>) -> [u8; N] where T: core::marker::Sync + core::convert::AsRef<str> + ?core::marker::Sized",
        ),
        (
            "function",
            "",
            "raw",
            r#"pub const unsafe extern "C" fn raw(*const u8, *mut u8) -> (u8, u16)"#,
        ),
        ("function", "", "later", "pub async fn later(&mut [u8])"),
        ("struct", "", "Unit", "pub struct Unit"),
        ("struct", "", "Tuple", "pub struct Tuple(pub u8, _)"),
        (
            "struct",
            "",
            "Wrapper",
            "pub struct Wrapper<T: core::clone::Clone = u8> { pub inner: alloc::vec::Vec<T> }",
        ),
        (
            "enum",
            "",
            "Shape",
            "pub enum Shape<T> { Point, Circle(T), Rect { w: T, h: T } }",
        ),
        ("enum", "", "Code", "pub enum Code { A = 1, B = 2 }"),
        (
            "union",
            "",
            "Bits",
            "pub union Bits { pub int: u32, pub float: f32 }",
        ),
        ("static", "", "COUNTER", "pub static mut COUNTER: i64"),
        (
            "type_alias",
            "",
            "Pair",
            "pub type Pair<'a> = (&'a str, core::option::Option<u8>)",
        ),
        (
            "trait",
            "",
            "Visitor",
            "pub unsafe trait Visitor<T>: core::fmt::Debug where T: core::clone::Clone",
        ),
        (
            "assoc_type",
            "Visitor",
            "Out",
            "type Out: core::default::Default",
        ),
        ("assoc_const", "Visitor", "LIMIT", "const LIMIT: usize = 4"),
        (
            "function",
            "Visitor",
            "visit",
            "fn visit(&mut self, T) -> <Self as shapes::Visitor>::Out",
        ),
        (
            "function",
            "Visitor",
            "done",
            "fn done(&self) -> bool { ... }",
        ),
        (
            "impl",
            "Wrapper",
            "impl<T: core::clone::Clone> shapes::Wrapper<T>",
            "impl<T: core::clone::Clone> shapes::Wrapper<T>",
        ),
        (
            "function",
            "impl<T: core::clone::Clone> shapes::Wrapper<T>",
            "first",
            "pub fn first(&self) -> core::option::Option<&T>",
        ),
        (
            "assoc_type",
            "impl core::iter::traits::iterator::Iterator for shapes::Unit",
            "Item",
            "type Item = u8",
        ),
        (
            "function",
            "impl core::iter::traits::iterator::Iterator for shapes::Unit",
            "next",
            "fn next(&mut self) -> core::option::Option<<Self as core::iter::traits::iterator::Iterator>::Item>",
        ),
        (
            "impl",
            "Unit",
            "impl core::marker::Send for shapes::Unit",
            "impl core::marker::Send for shapes::Unit",
        ),
        (
            "impl",
            "Wrapper",
            "impl<T> core::marker::Send for shapes::Wrapper<T>",
            "impl<T> core::marker::Send for shapes::Wrapper<T>",
        ),
        (
            "impl",
            "NotSend",
            "impl !core::marker::Send for shapes::NotSend",
            "impl !core::marker::Send for shapes::NotSend",
        ),
        (
            "macro",
            "",
            "square",
            "macro_rules! square { ($x:expr) => { ... }; }",
        ),
    ];
    for (kind, parent, name, want) in expected {
        assert_eq!(
            signature(&entries, kind, parent, name),
            want,
            "{kind} {parent} {name}"
        );
    }
    let impls: Vec<&str> = entries
        .iter()
        .filter(|e| e["kind"] == "impl")
        .map(|e| e["name"].as_str().unwrap())
        .collect();
    for dropped in ["Borrow", "Into", "Any", "Freeze", "UnsafeUnpin"] {
        assert!(
            impls.iter().all(|i| !i.contains(dropped)),
            "{dropped} in {impls:#?}"
        );
    }
    for kept in ["Sync", "Unpin", "UnwindSafe", "RefUnwindSafe"] {
        assert!(
            impls
                .iter()
                .any(|i| i.contains(&format!("{kept} for shapes::Unit"))),
            "{kept} not in {impls:#?}"
        );
    }
}
