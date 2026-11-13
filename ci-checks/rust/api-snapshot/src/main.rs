//! Writes the public API of every library crate in a Cargo workspace as API snapshot entries, from rustdoc JSON.
//! Usage: api-snapshot --project <dir> --output <file>. Exit 0 on success, 2 on any error.

mod cargo;
mod render;
mod rustdoc;
mod signature;

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::ExitCode;

const USAGE: &str = "usage: api-snapshot --project <dir> --output <file>";

fn parse_args(args: Vec<OsString>) -> Result<(PathBuf, PathBuf), String> {
    let mut project = None;
    let mut output = None;
    let mut args = args.into_iter();
    while let Some(flag) = args.next() {
        let slot = match flag.to_str() {
            Some("--project") => &mut project,
            Some("--output") => &mut output,
            _ => return Err(USAGE.to_owned()),
        };
        *slot = Some(PathBuf::from(args.next().ok_or(USAGE)?));
    }
    Ok((project.ok_or(USAGE)?, output.ok_or(USAGE)?))
}

fn run(args: Vec<OsString>) -> Result<(), String> {
    let (project, output) = parse_args(args)?;
    let mut entries = Vec::new();
    for krate in cargo::lib_crates(&project)? {
        let json = cargo::rustdoc_json(&project, &krate)?;
        entries.extend(render::entries(&krate.package, &rustdoc::load(&json)?)?);
    }
    let mut text = serde_json::to_string_pretty(&entries).map_err(|e| e.to_string())?;
    text.push('\n');
    std::fs::write(&output, text).map_err(|e| format!("cannot write {}: {e}", output.display()))
}

fn main() -> ExitCode {
    match run(std::env::args_os().skip(1).collect()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("api-snapshot: {message}");
            ExitCode::from(2)
        }
    }
}
