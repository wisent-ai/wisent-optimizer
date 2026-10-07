//! `wisent-optimizer-release surface [root]` prints the package's public
//! surface; `wisent-optimizer-release baseline [--stdout]` rewrites (or prints)
//! `released-surface.json` from the version PyPI serves. Exit 1 is a refusal,
//! exit 2 an invocation the command does not take.

mod baseline;
mod surface;

use std::path::{Path, PathBuf};
use std::process::ExitCode;

const USAGE: &str = "usage: wisent-optimizer-release surface [root] | baseline [--stdout]";

fn main() -> ExitCode {
    let release = Path::new(env!("CARGO_MANIFEST_DIR"));
    let repository = release.parent().unwrap_or(release).to_path_buf();
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let outcome = match arguments
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["surface"] => surface::public_surface(&repository).map(Some),
        ["surface", root] => surface::public_surface(&PathBuf::from(root)).map(Some),
        ["baseline"] => baseline::run(
            &repository,
            &release.join("target").join("baseline-artifact"),
            false,
        )
        .map(|()| None),
        ["baseline", "--stdout"] => baseline::run(
            &repository,
            &release.join("target").join("baseline-artifact"),
            true,
        )
        .map(|()| None),
        _ => {
            eprintln!("{USAGE}");
            return ExitCode::from(2);
        }
    };
    match outcome {
        Ok(Some(names)) => {
            let document = serde_json::json!({ "surface": names });
            println!(
                "{}",
                serde_json::to_string_pretty(&document).unwrap_or_default()
            );
            ExitCode::SUCCESS
        }
        Ok(None) => ExitCode::SUCCESS,
        Err(refusal) => {
            eprintln!("{refusal}");
            ExitCode::FAILURE
        }
    }
}
