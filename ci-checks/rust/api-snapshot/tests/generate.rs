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

/// The working-directory the scoped tests' repo-guards.toml has a section for.
const WORKING_DIRECTORY: &str = "crates/demo";

/// Runs the generator; with `config`, writes it as the repo-guards.toml the generator reads (`None` inside: no file).
fn generate_with(project: &Path, tag: &str, config: Option<Option<&str>>) -> Run {
    let dir = temp(tag);
    let output = dir.join("entries.json");
    let mut command = Command::new(env!("CARGO_BIN_EXE_api-snapshot"));
    command
        .arg("--project")
        .arg(project)
        .arg("--output")
        .arg(&output)
        .env("CARGO_TARGET_DIR", dir.join("target"));
    if let Some(text) = config {
        let file = dir.join("repo-guards.toml");
        if let Some(text) = text {
            fs::write(&file, text).unwrap();
        }
        command
            .arg("--config")
            .arg(file)
            .args(["--working-directory", WORKING_DIRECTORY]);
    }
    let result = command.output().unwrap();
    Run {
        code: result.status.code().unwrap_or(-1),
        stderr: String::from_utf8_lossy(&result.stderr).into_owned(),
        output,
    }
}

fn generate_in(project: &Path, tag: &str) -> Run {
    generate_with(project, tag, None)
}

fn generate(name: &str, tag: &str) -> Run {
    generate_in(&fixture(name), tag)
}

/// Runs the generator with `api-snapshot-scope.rust = <scope>` (a TOML value) for its working-directory.
fn generate_scoped(name: &str, tag: &str, scope: &str) -> Run {
    let config = format!("[project.\"{WORKING_DIRECTORY}\"]\napi-snapshot-scope.rust = {scope}\n");
    generate_with(&fixture(name), tag, Some(Some(&config)))
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

/// Snapshots same_name_v1, then compares `changed` with it; returns the 多/少/变 lines of the log.
fn changes_against_v1(changed: &str, tag: &str) -> Vec<String> {
    let old = generate("same_name_v1", &format!("{tag}-old"));
    let missing = temp(&format!("{tag}-none")).join("none.json");
    let (_, _, snapshot) = compare(&old.output, &missing, &format!("{tag}-first"));
    let snapshot_file = temp(&format!("{tag}-snapshot")).join("api-rust.json");
    fs::write(&snapshot_file, snapshot.unwrap()).unwrap();

    let new = generate(changed, &format!("{tag}-new"));
    assert_keys_unique(&new.entries());
    let (code, log, _) = compare(&new.output, &snapshot_file, &format!("{tag}-second"));
    assert_eq!(code, 1, "{log}");
    log.lines()
        .filter(|l| l.starts_with("多 ") || l.starts_with("少 ") || l.starts_with("变 "))
        .map(str::to_owned)
        .collect()
}

#[test]
fn one_signature_change_is_one_changed_entry() {
    let listed = changes_against_v1("same_name_v2", "same-name-len-change");
    assert_eq!(listed.len(), 1, "{listed:#?}");
    assert!(listed[0].starts_with("变 "), "{listed:#?}");
    assert!(
        listed[0].contains(r#""parent": "impl same_name::Id""#),
        "{listed:#?}"
    );
    assert!(listed[0].contains(r#""name": "len""#), "{listed:#?}");
}

#[test]
fn one_from_impl_change_is_one_changed_entry() {
    let listed = changes_against_v1("same_name_v3", "same-name-from-change");
    assert_eq!(listed.len(), 1, "{listed:#?}");
    assert!(listed[0].starts_with("变 "), "{listed:#?}");
    assert!(
        listed[0].contains(&format!(r#""parent": "{FROM_STRING}""#)),
        "{listed:#?}"
    );
    assert!(listed[0].contains(r#""name": "from""#), "{listed:#?}");
}

#[test]
fn explicit_reexports_shadow_globs() {
    let entries = generate("shadowing", "shadowing").entries();
    assert_keys_unique(&entries);
    assert_eq!(
        signature(&entries, "struct", "", "Reverse"),
        "pub use core::cmp::Reverse"
    );
    assert_eq!(
        signature(&entries, "enum", "", "Ordering"),
        "pub use core::cmp::Ordering"
    );
    assert_eq!(
        signature(&entries, "enum", "", "Local"),
        "pub enum Local { A }"
    );
    assert_eq!(
        signature(&entries, "function", "", "only_glob"),
        "pub fn only_glob()"
    );
    for (kind, name) in [("struct", "Ordering"), ("struct", "Local")] {
        let at_root = entries
            .iter()
            .filter(|e| e["kind"] == kind && e["parent"] == "" && e["name"] == name);
        assert_eq!(
            at_root.count(),
            0,
            "{kind} {name} is shadowed at the root: {entries:#?}"
        );
    }
    assert_eq!(
        signature(&entries, "struct", "inner", "Reverse"),
        "pub struct Reverse"
    );
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

#[test]
fn reexports_keys_unique() {
    let run = generate("reexports", "reexports");
    let entries = run.entries();
    assert_keys_unique(&entries);
    assert!(!run.text().contains("private"), "{}", run.text());
    assert!(!run.text().contains("globbed"), "{}", run.text());
    assert_eq!(
        signature(&entries, "struct", "", "Hidden"),
        "pub struct Hidden(pub u8)"
    );
    assert_eq!(
        signature(&entries, "function", "impl reexports::Hidden", "get"),
        "pub fn get(&self) -> u8"
    );
    assert_eq!(
        signature(&entries, "impl", "Hidden", "impl reexports::Hidden"),
        "impl reexports::Hidden"
    );
    assert_eq!(
        signature(&entries, "function", "", "build"),
        "pub fn build() -> reexports::Hidden"
    );
    assert_eq!(
        signature(&entries, "struct", "", "Twice"),
        "pub struct Twice"
    );
    assert_eq!(
        signature(&entries, "struct", "public", "Twice"),
        "pub struct Twice"
    );
    assert_eq!(
        signature(&entries, "function", "public", "takes"),
        "pub fn takes(&reexports::Twice)"
    );
    assert_eq!(
        signature(
            &entries,
            "impl",
            "Twice",
            "impl core::marker::Send for reexports::Twice"
        ),
        "impl core::marker::Send for reexports::Twice"
    );
    assert_eq!(
        signature(&entries, "struct", "", "FromGlob"),
        "pub struct FromGlob"
    );
    assert!(signature(&entries, "trait", "", "Debug").starts_with("pub use core::fmt::"));
    assert!(signature(&entries, "proc_derive", "", "Debug").starts_with("pub use core::fmt::"));
    assert_eq!(
        signature(&entries, "use", "prelude", "core::fmt::*"),
        "pub use core::fmt::*"
    );
    assert_eq!(
        signature(&entries, "use", "prelude", "core::ops::*"),
        "pub use core::ops::*"
    );
    assert_eq!(signature(&entries, "module", "", "root"), "pub mod root");
    assert!(
        entries
            .iter()
            .all(|e| !e["parent"].as_str().unwrap().starts_with("root")),
        "{entries:#?}"
    );
}

#[test]
fn doc_change_and_move_are_identical() {
    let old = generate("moved_v1", "moved-old");
    let new = generate("moved_v2", "moved-new");
    assert_eq!(old.text(), new.text());
    assert_eq!(
        signature(&new.entries(), "function", "", "open"),
        "pub fn open(&moved::Config) -> u8"
    );

    let (_, _, snapshot) = compare(
        &old.output,
        &temp("moved-none").join("none.json"),
        "moved-first",
    );
    let snapshot_file = temp("moved-snapshot").join("api-rust.json");
    fs::write(&snapshot_file, snapshot.unwrap()).unwrap();
    let (code, log, _) = compare(&new.output, &snapshot_file, "moved-second");
    assert_eq!(code, 0, "{log}");
}

#[test]
fn two_runs_byte_identical() {
    let first = generate("reexports", "twice-first").text();
    let second = generate("reexports", "twice-second").text();
    assert_eq!(first, second);
    let root = fixture("reexports");
    assert!(!first.contains(root.to_str().unwrap()));
    assert!(!first.contains(std::env::temp_dir().to_str().unwrap()));
}

#[test]
fn partly_shadowed_and_repeated_globs_keep_keys_unique() {
    let entries = generate("globs", "globs").entries();
    assert_keys_unique(&entries);
    assert_eq!(
        signature(&entries, "enum", "", "Local"),
        "pub enum Local { A }"
    );
    assert!(
        !entries
            .iter()
            .any(|e| e["kind"] == "struct" && e["parent"] == "" && e["name"] == "Local")
    );
    assert_eq!(
        signature(&entries, "function", "impl globs::Local", "new"),
        "pub fn new() -> Self"
    );
    assert_eq!(
        signature(&entries, "function", "impl globs::inner::Local", "new"),
        "pub fn new() -> Self"
    );
    assert_eq!(
        signature(&entries, "use", "", "core::ops::*"),
        "pub use core::ops::*"
    );
    assert_eq!(signature(&entries, "struct", "t", "Tie"), "pub struct Tie");
    assert_eq!(signature(&entries, "struct", "t0", "Tie"), "pub struct Tie");
    // Equal-length paths: the canonical one is the smaller string, "t0::Tie" < "t::Tie" ('0' < ':').
    assert_eq!(
        signature(
            &entries,
            "impl",
            "t0::Tie",
            "impl core::marker::Send for globs::t0::Tie"
        ),
        "impl core::marker::Send for globs::t0::Tie"
    );
}

const DRIVER_SCOPE: &str = r#"["scope_demo::driver"]"#;

/// Each entry as `kind parent name`, sorted.
fn keys(entries: &[Value]) -> Vec<String> {
    let mut keys: Vec<String> = entries
        .iter()
        .map(|e| format!("{} {} {}", e["kind"], e["parent"], e["name"]))
        .map(|k| k.replace('"', ""))
        .collect();
    keys.sort();
    keys
}

#[test]
fn scope_keeps_only_entries_under_its_modules() {
    let entries = generate_scoped("scoped_v1", "scoped", DRIVER_SCOPE).entries();
    assert_keys_unique(&entries);
    assert!(entries.iter().all(|e| e["unit"] == "scope-demo"));
    let keys = keys(&entries);
    for key in [
        // Items whose module is scope_demo::driver or below it.
        "struct driver Id",
        "trait driver Device",
        "function driver::Device open",
        "module driver io",
        "function driver::io read",
        // Another crate's item re-exported here sits in the re-exporting module.
        "struct driver Cell",
        // Impls with a local path in an in-scope module: the self type, `&` stripped, or a trait argument.
        "impl driver::Id impl scope_demo::driver::Id",
        "function impl scope_demo::driver::Id get",
        "impl driver::Id impl scope_demo::driver::Device for scope_demo::driver::Id",
        "function impl scope_demo::driver::Device for scope_demo::driver::Id open",
        "impl driver::Id impl core::marker::Send for scope_demo::driver::Id",
        "impl  impl scope_demo::net::Ping for &scope_demo::driver::Id",
        "function impl scope_demo::net::Ping for &scope_demo::driver::Id ping",
        "impl net::Socket impl core::convert::From<scope_demo::driver::Id> for scope_demo::net::Socket",
        "impl  impl scope_demo::net::Wrap<scope_demo::driver::Id> for u8",
        "function impl scope_demo::net::Wrap<scope_demo::driver::Id> for u8 wrap",
    ] {
        assert!(
            keys.contains(&key.to_owned()),
            "{key} missing from {keys:#?}"
        );
    }
    for absent in [
        // Module entries sit in their parent module, the crate root here.
        "module  driver",
        "module  driverx",
        "function driverx other",
        "function  top",
        // Another path to an in-scope item counts on its own.
        "function  read",
        "struct net DriverId",
        "struct net RefCell",
        "struct net Socket",
        "trait net Ping",
        "function net::Ping ping",
        "trait net Wrap",
        "function impl core::clone::Clone for scope_demo::net::Socket clone",
        "impl net::Socket impl core::clone::Clone for scope_demo::net::Socket",
        "impl net::Socket impl scope_demo::net::Ping for scope_demo::net::Socket",
        "impl net::Socket impl core::marker::Send for scope_demo::net::Socket",
    ] {
        assert!(!keys.contains(&absent.to_owned()), "{absent} in {keys:#?}");
    }
}

#[test]
fn no_scope_writes_every_entry() {
    let unset = generate("scoped_v1", "scoped-unset").text();
    let other_section = format!(
        "[project.\".\"]\napi-snapshot-scope.rust = [\"scope_demo::driver\"]\n\n\
         [project.\"{WORKING_DIRECTORY}\"]\napi-snapshot.rust = \".github/checks/api-rust.json\"\n\
         api-snapshot-scope.python = [\"pkg\"]\n"
    );
    for (tag, config) in [
        ("scoped-no-file", None),
        ("scoped-empty-file", Some("")),
        ("scoped-no-setting", Some(other_section.as_str())),
    ] {
        let run = generate_with(&fixture("scoped_v1"), tag, Some(config));
        assert_eq!(run.text(), unset, "{config:?}");
    }
    let entries: Vec<Value> = serde_json::from_str(&unset).unwrap();
    let keys = keys(&entries);
    for key in [
        "module  driverx",
        "function  top",
        "function  read",
        "struct net Socket",
        "struct net RefCell",
        "impl net::Socket impl core::clone::Clone for scope_demo::net::Socket",
    ] {
        assert!(
            keys.contains(&key.to_owned()),
            "{key} missing from {keys:#?}"
        );
    }
}

#[test]
fn scope_of_the_crate_writes_every_entry() {
    let whole = generate("scoped_v1", "scoped-whole").text();
    let crate_scope = generate_scoped("scoped_v1", "scoped-crate", r#"["scope_demo"]"#);
    assert_eq!(crate_scope.text(), whole);
    let other = generate_scoped("scoped_v1", "scoped-other", r#"["other_crate"]"#);
    assert_eq!(other.text(), "[]\n");
}

#[test]
fn invalid_scope_exits_2() {
    for (tag, scope, error) in [
        (
            "scope-string",
            r#""scope_demo::driver""#,
            " is not an array of strings",
        ),
        (
            "scope-number",
            r#"["scope_demo::driver", 1]"#,
            " is not an array of strings",
        ),
        (
            "scope-empty",
            "[]",
            " = []: must list at least one module prefix",
        ),
        (
            "scope-package",
            r#"["scope-demo"]"#,
            r#": "scope-demo": must be a module path"#,
        ),
        (
            "scope-glob",
            r#"["scope_demo::*"]"#,
            r#": "scope_demo::*": must be a module path"#,
        ),
    ] {
        let run = generate_scoped("scoped_v1", tag, scope);
        assert_fails_with_2(&run);
        let at = format!("[project.\"{WORKING_DIRECTORY}\"] api-snapshot-scope.rust");
        assert!(
            run.stderr.contains(&format!("{at}{error}")),
            "{}",
            run.stderr
        );
    }
    let broken = generate_with(
        &fixture("scoped_v1"),
        "scope-broken-toml",
        Some(Some("[project\n")),
    );
    assert_fails_with_2(&broken);
    let half = Command::new(env!("CARGO_BIN_EXE_api-snapshot"))
        .args([
            "--project",
            ".",
            "--output",
            "out.json",
            "--config",
            "repo-guards.toml",
        ])
        .output()
        .unwrap();
    assert_eq!(half.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&half.stderr).contains("usage: "));
}

/// Compares `changed` against a snapshot of scoped_v1, both generated with the driver scope.
fn scoped_compare(changed: &str, tag: &str) -> (i32, String) {
    let old = generate_scoped("scoped_v1", &format!("{tag}-old"), DRIVER_SCOPE);
    let missing = temp(&format!("{tag}-none")).join("none.json");
    let (_, _, snapshot) = compare(&old.output, &missing, &format!("{tag}-first"));
    let snapshot_file = temp(&format!("{tag}-snapshot")).join("api-rust.json");
    fs::write(&snapshot_file, snapshot.unwrap()).unwrap();
    let new = generate_scoped(changed, &format!("{tag}-new"), DRIVER_SCOPE);
    assert_eq!(new.code, 0, "{}", new.stderr);
    let (code, log, _) = compare(&new.output, &snapshot_file, &format!("{tag}-second"));
    (code, log)
}

#[test]
fn new_public_item_outside_scope_passes() {
    let (code, log) = scoped_compare("scoped_v2", "scoped-outside");
    assert_eq!(code, 0, "{log}");
}

#[test]
fn new_public_item_inside_scope_fails() {
    let (code, log) = scoped_compare("scoped_v3", "scoped-inside");
    assert_eq!(code, 1, "{log}");
    let listed: Vec<&str> = log
        .lines()
        .filter(|l| l.starts_with("多 ") || l.starts_with("少 ") || l.starts_with("变 "))
        .collect();
    assert_eq!(listed.len(), 1, "{log}");
    assert!(listed[0].starts_with("多 "), "{log}");
    assert!(listed[0].contains(r#""parent": "driver::io""#), "{log}");
    assert!(listed[0].contains(r#""name": "added""#), "{log}");
}
