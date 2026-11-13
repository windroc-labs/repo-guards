use std::fs;
use std::io::{ErrorKind, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde_json::{Value, json};

fn temp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "layer-rules-protocol-{}-{name}",
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn run(root: &Path, args: &[&Path], stdin: &[u8]) -> (i32, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_layer-rules"))
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .current_dir(root)
        .spawn()
        .unwrap();
    // A child that exits before reading (a usage error) closes the pipe; its exit code still tells.
    match child.stdin.take().unwrap().write_all(stdin) {
        Err(error) if error.kind() == ErrorKind::BrokenPipe => {}
        written => written.unwrap(),
    }
    let output = child.wait_with_output().unwrap();
    (
        output.status.code().unwrap(),
        String::from_utf8(output.stdout).unwrap(),
    )
}

const APP: &str = "[package]\nname = \"app\"\nversion = \"0.1.0\"\nedition = \"2024\"\n";

/// The core's side directory: only the scanned files and the manifests are written; "files" names every .rs file.
fn scan(name: &str, project: &[(&str, &str)], scanned: &[&str]) -> Vec<Value> {
    let root = temp(name);
    let mut files = Vec::new();
    let mut context = Vec::new();
    for (path, text) in project {
        if path.ends_with(".rs") {
            files.push(*path);
        } else {
            context.push(*path);
        }
        if !path.ends_with(".rs") || scanned.contains(path) {
            let file = root.join(path);
            fs::create_dir_all(file.parent().unwrap()).unwrap();
            fs::write(file, text).unwrap();
        }
    }
    let request = json!({"files": files, "scan": scanned, "context": context, "settings": {"source-dirs": ["src"]}});
    let (code, out) = run(&root, &[&root], request.to_string().as_bytes());
    assert_eq!(code, 0, "{out}");
    let _ = fs::remove_dir_all(&root);
    out.lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

fn site(file: &str, line: u64, targets: &[&str]) -> Value {
    json!({"file": file, "line": line, "targets": targets})
}

fn app(files: &[(&'static str, &'static str)]) -> Vec<(&'static str, &'static str)> {
    let mut project = vec![
        ("Cargo.toml", APP),
        ("src/lib.rs", "mod api;\nmod core;\nmod store;\n"),
        ("src/api/mod.rs", ""),
        ("src/core/mod.rs", ""),
        ("src/core/inner.rs", ""),
        ("src/core/a.rs", ""),
        ("src/core/b.rs", ""),
        ("src/store/mod.rs", ""),
        ("src/store/inner.rs", ""),
        ("src/store/other.rs", ""),
    ];
    for (path, text) in files {
        project.retain(|(p, _)| p != path);
        project.push((path, text));
    }
    project
}

const API: &str = "src/api/mod.rs";
const INNER: &str = "src/store/inner.rs";
const OTHER: &str = "src/store/other.rs";

#[test]
fn a_use_item_is_one_site_with_the_union_of_its_targets() {
    let text = "use crate::store::inner::{a, b};\nuse crate::store::{inner::a as x, other::b as y};\nuse crate::core;\n";
    let records = scan("use", &app(&[(API, text)]), &[API]);
    assert_eq!(
        records,
        [
            site(API, 1, &[INNER]),
            site(API, 2, &[INNER, OTHER]),
            site(API, 3, &["src/core/mod.rs"])
        ]
    );
}

#[test]
fn full_paths_count_each_and_alias_paths_only_past_the_use() {
    let text = "\
use crate::store::inner::a;
use crate::core;
fn f() {
    a();
    crate::store::inner::f();
    core::inner::g();
    core::h();
    let _v: Vec<crate::store::other::T> = Vec::new();
    std::mem::drop(1);
    self::local::z();
    local::z();
}
pub(crate) mod local {
    pub fn z() {}
}
use crate::Root;
";
    let records = scan("paths", &app(&[(API, text)]), &[API]);
    assert_eq!(
        records,
        [
            site(API, 1, &[INNER]),
            site(API, 2, &["src/core/mod.rs"]),
            site(API, 5, &[INNER]),
            site(API, 6, &["src/core/inner.rs"]),
            site(API, 8, &[OTHER]),
            site(API, 16, &["src/lib.rs"]),
        ]
    );
}

#[test]
fn mod_declarations_attributes_and_visibilities_are_not_sites() {
    let text = "\
mod store;
pub mod x;
#[crate::store::inner::attr]
pub(in crate::store) fn f() {}
#[derive(crate::store::inner::D)]
pub(crate) struct S;
";
    assert_eq!(
        scan("mods", &app(&[(API, text)]), &[API]),
        Vec::<Value>::new()
    );
}

#[test]
fn cfg_test_items_files_and_predicates() {
    let api = "\
#[cfg(test)]
mod tests {
    use crate::store::inner::a;
}
#[cfg(test)]
mod more;
#[cfg(all(test, unix))]
fn t() { crate::store::inner::f(); }
#[cfg(any(test, unix))]
fn u() { crate::store::inner::f(); }
#[cfg(not(test))]
fn v() { crate::store::other::f(); }
fn w() {
    #[cfg(test)]
    let _ = crate::store::inner::f();
    #[cfg(test)]
    crate::store::inner::g();
}
struct S;
impl S {
    #[cfg(test)]
    fn x() { crate::store::inner::f(); }
}
";
    let project = app(&[
        (API, api),
        ("src/api/more.rs", "use crate::store::inner::a;\n"),
        (
            "src/core/only.rs",
            "#![cfg(test)]\nuse crate::store::inner::a;\n",
        ),
        ("src/core/mod.rs", "#[cfg(test)]\nmod t;\n"),
        ("src/core/t.rs", "use crate::store::inner::a;\n"),
    ]);
    let records = scan(
        "cfg",
        &project,
        &[API, "src/api/more.rs", "src/core/only.rs", "src/core/t.rs"],
    );
    // src/core/mod.rs is not scanned (its layer is not checked), so its #[cfg(test)] mod t; is unseen and t.rs is code.
    assert_eq!(
        records,
        [
            site(API, 10, &[INNER]),
            site(API, 12, &[OTHER]),
            site("src/core/t.rs", 1, &[INNER])
        ]
    );
}

#[test]
fn an_inline_module_is_its_file() {
    let a = "\
mod nested {
    use super::super::b::B;
    pub fn f() { super::helper(); }
}
fn helper() {}
use self::nested::f;
";
    let records = scan("inline", &app(&[("src/core/a.rs", a)]), &["src/core/a.rs"]);
    assert_eq!(records, [site("src/core/a.rs", 2, &["src/core/b.rs"])]);
}

#[test]
fn workspace_dependencies_resolve() {
    let project = [
        (
            "Cargo.toml",
            "[workspace]\nmembers = [\"crates/*\"]\n[workspace.dependencies]\ncorelib = { path = \"crates/corelib\" }\n\
             renamed = { path = \"crates/other\", package = \"other\" }\n",
        ),
        (
            "crates/api/Cargo.toml",
            "[package]\nname = \"api\"\n[dependencies]\ncorelib = { workspace = true }\nrenamed = { workspace = true }\n\
             direct-dep = { path = \"../direct\", package = \"direct\" }\nlibnamed = { path = \"../libnamed\" }\n\
             serde = \"1\"\nshared = { path = \"../../../shared\" }\n[target.'cfg(unix)'.dev-dependencies]\nunixdep = { path = \"../unixdep\" }\n",
        ),
        (
            "crates/corelib/Cargo.toml",
            "[package]\nname = \"corelib\"\n",
        ),
        ("crates/other/Cargo.toml", "[package]\nname = \"other\"\n"),
        ("crates/direct/Cargo.toml", "[package]\nname = \"direct\"\n"),
        (
            "crates/libnamed/Cargo.toml",
            "[package]\nname = \"libnamed\"\n[lib]\nname = \"ln\"\n",
        ),
        (
            "crates/unixdep/Cargo.toml",
            "[package]\nname = \"unixdep\"\n",
        ),
        (
            "crates/api/src/lib.rs",
            "use corelib::inner::x;\nuse renamed::thing;\nuse direct_dep::m::y;\nuse ln::z;\nuse serde::Serialize;\n\
             use shared::q;\nfn f() { ::corelib::inner::x(); unixdep::u(); std::fmt::write(); }\n",
        ),
        (
            "crates/api/src/main.rs",
            "use api::thing;\nfn main() { crate::m::f(); }\nmod m;\n",
        ),
        ("crates/api/src/m.rs", ""),
        ("crates/corelib/src/lib.rs", ""),
        ("crates/corelib/src/inner.rs", ""),
        ("crates/other/src/lib.rs", ""),
        ("crates/direct/src/lib.rs", ""),
        ("crates/direct/src/m.rs", ""),
        ("crates/libnamed/src/lib.rs", ""),
        ("crates/unixdep/src/lib.rs", ""),
    ];
    let lib = "crates/api/src/lib.rs";
    let main = "crates/api/src/main.rs";
    let records = scan("workspace", &project, &[lib, main]);
    assert_eq!(
        records,
        [
            site(lib, 1, &["crates/corelib/src/inner.rs"]),
            site(lib, 2, &["crates/other/src/lib.rs"]),
            site(lib, 3, &["crates/direct/src/m.rs"]),
            site(lib, 4, &["crates/libnamed/src/lib.rs"]),
            site(lib, 6, &["../shared"]),
            site(lib, 7, &["crates/corelib/src/inner.rs"]),
            site(lib, 7, &["crates/unixdep/src/lib.rs"]),
            site(main, 1, &[lib]),
            site(main, 2, &["crates/api/src/m.rs"]),
        ]
    );
}

#[test]
fn a_file_that_does_not_parse_is_reported() {
    let records = scan("broken", &app(&[(API, "fn (\n")]), &[API]);
    assert_eq!(records.len(), 1);
    assert_eq!(records[0]["file"], API);
    assert!(
        records[0]["error"].as_str().unwrap().starts_with("line 1:"),
        "{}",
        records[0]
    );
}

#[test]
fn bad_usage_or_request_exits_2() {
    let root = temp("usage");
    assert_eq!(run(&root, &[], b"{}").0, 2);
    // More than a pipe buffer: the usage error exits without reading, so the write always hits EPIPE.
    assert_eq!(run(&root, &[], &vec![b' '; 1 << 20]).0, 2);
    assert_eq!(run(&root, &[&root], b"not json").0, 2);
    let request = json!({"files": ["../x.rs"], "scan": [], "context": [], "settings": {}});
    assert_eq!(run(&root, &[&root], request.to_string().as_bytes()).0, 2);
    let _ = fs::remove_dir_all(&root);
}

#[test]
fn cfg_test_on_any_expression_statement_is_skipped() {
    let text = "\
fn w() {
    #[cfg(test)]
    for _ in crate::store::inner::it() {}
    #[cfg(test)]
    while crate::store::inner::w() {}
    #[cfg(test)]
    loop { crate::store::inner::f(); }
    #[cfg(test)]
    (crate::store::inner::f(), 1);
    for _ in crate::store::other::it() {}
}
";
    assert_eq!(
        scan("cfg-expr", &app(&[(API, text)]), &[API]),
        [site(API, 10, &[OTHER])]
    );
}

#[test]
fn a_use_alias_does_not_resolve_through_itself() {
    let api = "\
mod h;
use h::h;
use self::h as here;
fn f() { here::g(); }
";
    let project = app(&[(API, api), ("src/api/h.rs", "")]);
    assert_eq!(
        scan("self-alias", &project, &[API]),
        [
            site(API, 2, &["src/api/h.rs"]),
            site(API, 3, &["src/api/h.rs"])
        ]
    );
    let project = [
        (
            "crates/api/Cargo.toml",
            "[package]\nname = \"api\"\n[dependencies]\ncorelib = { path = \"../corelib\" }\n",
        ),
        (
            "crates/corelib/Cargo.toml",
            "[package]\nname = \"corelib\"\n",
        ),
        (
            "crates/api/src/lib.rs",
            "use corelib;\nfn f() {\n    corelib::inner::x();\n}\n",
        ),
        ("crates/corelib/src/lib.rs", ""),
        ("crates/corelib/src/inner.rs", ""),
    ];
    let lib = "crates/api/src/lib.rs";
    assert_eq!(
        scan("self-extern", &project, &[lib]),
        [
            site(lib, 1, &["crates/corelib/src/lib.rs"]),
            site(lib, 3, &["crates/corelib/src/inner.rs"])
        ]
    );
}

#[test]
fn a_dependency_on_the_parent_directory_is_outside() {
    let manifest = "[package]\nname = \"app\"\n[dependencies]\nup = { path = \"..\" }\ndots = { path = \"..dots\" }\n";
    let project = [
        ("Cargo.toml", manifest),
        ("..dots/Cargo.toml", "[package]\nname = \"dots\"\n"),
        ("src/lib.rs", "use up::x;\nuse dots::y;\n"),
        ("..dots/src/lib.rs", ""),
    ];
    assert_eq!(
        scan("parent", &project, &["src/lib.rs"]),
        [
            site("src/lib.rs", 1, &[".."]),
            site("src/lib.rs", 2, &["..dots/src/lib.rs"])
        ]
    );
}

#[test]
fn cfg_test_on_macros_arms_fields_and_variants_is_skipped() {
    let text = "\
fn w(x: u8) {
    #[cfg(test)]
    crate::store::inner::m!();
    match x {
        #[cfg(test)]
        0 => crate::store::inner::f(),
        _ => (),
    }
    let _ = S {
        #[cfg(test)]
        a: crate::store::inner::f(),
    };
}
struct S {
    #[cfg(test)]
    a: crate::store::inner::T,
}
enum E {
    #[cfg(test)]
    V(crate::store::inner::T),
}
extern \"C\" {
    #[cfg(test)]
    fn g(t: crate::store::inner::T);
}
fn z() { crate::store::other::f(); }
";
    assert_eq!(
        scan("cfg-more", &app(&[(API, text)]), &[API]),
        [site(API, 26, &[OTHER])]
    );
}
