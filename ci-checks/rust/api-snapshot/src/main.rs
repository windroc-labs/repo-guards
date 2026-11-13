//! Writes the public API of every library crate in a Cargo workspace as API snapshot entries, from rustdoc JSON.
//! Usage: api-snapshot --project <dir> --output <file> [--config <repo-guards.toml> --working-directory <dir>].
//! With the config, only the modules its api-snapshot-scope.rust names (see scope.rs). Exit 0 on success, 2 on any error.

mod cargo;
mod render;
mod rustdoc;
mod scope;
mod signature;
mod walk;

use scope::Scope;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const USAGE: &str = "usage: api-snapshot --project <dir> --output <file> [--config <repo-guards.toml> --working-directory <dir>]";

struct Args {
    project: PathBuf,
    output: PathBuf,
    scope: Scope,
}

fn parse_args(args: Vec<OsString>) -> Result<Args, String> {
    let [mut project, mut output, mut config, mut working_directory] = [None, None, None, None];
    let mut args = args.into_iter();
    while let Some(flag) = args.next() {
        let slot = match flag.to_str() {
            Some("--project") => &mut project,
            Some("--output") => &mut output,
            Some("--config") => &mut config,
            Some("--working-directory") => &mut working_directory,
            _ => return Err(USAGE.to_owned()),
        };
        *slot = Some(args.next().ok_or(USAGE)?);
    }
    let scope = match (config, working_directory) {
        (None, None) => scope::WHOLE,
        (Some(config), Some(working_directory)) => scope::read(
            Path::new(&config),
            working_directory
                .to_str()
                .ok_or("--working-directory is not UTF-8")?,
        )?,
        _ => return Err(USAGE.to_owned()),
    };
    Ok(Args {
        project: project.ok_or(USAGE)?.into(),
        output: output.ok_or(USAGE)?.into(),
        scope,
    })
}

fn run(args: Vec<OsString>) -> Result<(), String> {
    let Args {
        project,
        output,
        scope,
    } = parse_args(args)?;
    let mut entries = Vec::new();
    for krate in cargo::lib_crates(&project)? {
        let json = cargo::rustdoc_json(&project, &krate)?;
        entries.extend(render::entries(
            &krate.package,
            &rustdoc::load(&json)?,
            &scope,
        )?);
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
