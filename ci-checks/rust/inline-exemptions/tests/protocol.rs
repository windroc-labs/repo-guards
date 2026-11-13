use std::fs;
use std::io::{ErrorKind, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

fn temp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "inline-exemptions-protocol-{}-{name}",
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn put(root: &Path, path: &str, bytes: &[u8]) {
    let file = root.join(path);
    fs::create_dir_all(file.parent().unwrap()).unwrap();
    fs::write(file, bytes).unwrap();
}

fn binary(root: &Path, stdin: &[u8]) -> (bool, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_inline-exemptions"))
        .arg(root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    // A child that exits before reading (a usage error) closes the pipe; its exit code still tells.
    match child.stdin.take().unwrap().write_all(stdin) {
        Err(error) if error.kind() == ErrorKind::BrokenPipe => {}
        written => written.unwrap(),
    }
    let output = child.wait_with_output().unwrap();
    (
        output.status.success(),
        String::from_utf8(output.stdout).unwrap(),
    )
}

#[test]
fn protocol_end_to_end() {
    let root = temp("protocol");
    put(
        &root,
        "a b/x.rs",
        b"// jscpd:ignore-start \"q\"\nfn f() {}\n",
    );
    put(&root, "Cargo.toml", b"[lints.clippy]\nx = \"allow\"\n");
    let (ok, stdout) = binary(&root, b"a b/x.rs\0Cargo.toml\0");
    assert!(ok);
    assert_eq!(
        stdout,
        "{\"tool\":\"jscpd\",\"file\":\"a b/x.rs\",\"rule\":\"ignore\",\"function\":\"f\"}\n\
         {\"tool\":\"clippy\",\"file\":\"Cargo.toml\",\"rule\":\"clippy::x\",\"function\":\"\"}\n"
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn s34_build_rs_is_never_run() {
    let root = temp("s34");
    let marker = root.join("pwned");
    let build = format!(
        "fn main() {{\n    std::fs::write({:?}, \"x\").unwrap();\n}}\n",
        marker.display().to_string()
    );
    put(&root, "Cargo.toml", b"[package]\nname = \"victim\"\nversion = \"0.1.0\"\nedition = \"2024\"\nbuild = \"build.rs\"\n");
    put(&root, "build.rs", build.as_bytes());
    put(&root, "src/main.rs", b"#[allow(dead_code)]\nfn main() {}\n");
    let (ok, stdout) = binary(&root, b"Cargo.toml\0build.rs\0src/main.rs");
    assert!(ok);
    assert_eq!(
        stdout,
        "{\"tool\":\"rustc\",\"file\":\"src/main.rs\",\"rule\":\"dead_code\",\"function\":\"main\"}\n"
    );
    assert!(!marker.exists());
    fs::remove_dir_all(root).unwrap();
}
