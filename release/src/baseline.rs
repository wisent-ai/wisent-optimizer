//! The baseline: the surface of the version PyPI serves, never the version the
//! working tree declares. The artifact is the sdist when the release ships one,
//! else the pure-Python wheel, unpacked inside this crate's `target/` directory
//! and read with the same extractor as the working tree.

use std::io::Read;
use std::path::{Path, PathBuf};

use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::surface::public_surface;

const PROJECT: &str = "wisent-optimizer";

fn get(url: &str) -> Result<Vec<u8>, String> {
    let response = ureq::get(url)
        .call()
        .map_err(|error| format!("cannot reach {url}: {error}"))?;
    let mut body = Vec::new();
    response
        .into_reader()
        .read_to_end(&mut body)
        .map_err(|error| format!("cannot read {url}: {error}"))?;
    Ok(body)
}

/// The newest published version, its tier marker and the artifact to read.
fn published_artifact() -> Result<(Value, &'static str, String), String> {
    let index = format!("https://pypi.org/pypi/{PROJECT}/json");
    let metadata: Value = serde_json::from_slice(&get(&index)?)
        .map_err(|error| format!("{index} is not JSON: {error}"))?;
    let version = metadata["info"]["version"]
        .as_str()
        .ok_or_else(|| format!("{index} names no info.version"))?
        .to_string();
    let mut artifacts: Vec<Value> = metadata["releases"][&version]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter(|artifact| artifact["yanked"].as_bool() != Some(true))
        .collect();
    artifacts.sort_by(|left, right| left["filename"].as_str().cmp(&right["filename"].as_str()));
    for (kind, marker) in [("sdist", "pypi-sdist"), ("bdist_wheel", "pypi-wheel")] {
        let found = artifacts.iter().find(|artifact| {
            artifact["packagetype"].as_str() == Some(kind)
                && (kind != "bdist_wheel"
                    || artifact["filename"]
                        .as_str()
                        .is_some_and(|name| name.ends_with("-any.whl")))
        });
        if let Some(artifact) = found {
            return Ok((artifact.clone(), marker, version));
        }
    }
    Err(format!(
        "{PROJECT} {version} has no recoverable sdist or pure-Python wheel"
    ))
}

/// The surface of an artifact and the sha256 of the bytes it was read from.
fn recovered_surface(artifact: &Value, scratch: &Path) -> Result<(Vec<String>, String), String> {
    let url = artifact["url"]
        .as_str()
        .ok_or("the artifact names no url")?;
    let filename = artifact["filename"].as_str().unwrap_or_default();
    let payload = get(url)?;
    let digest = hex::encode(Sha256::digest(&payload));
    if scratch.exists() {
        std::fs::remove_dir_all(scratch)
            .map_err(|error| format!("{}: {error}", scratch.display()))?;
    }
    std::fs::create_dir_all(scratch).map_err(|error| format!("{}: {error}", scratch.display()))?;
    let root: PathBuf = if filename.ends_with(".whl") {
        zip::ZipArchive::new(std::io::Cursor::new(&payload))
            .and_then(|mut archive| archive.extract(scratch))
            .map_err(|error| format!("{filename} does not unpack: {error}"))?;
        scratch.to_path_buf()
    } else {
        tar::Archive::new(flate2::read::GzDecoder::new(payload.as_slice()))
            .unpack(scratch)
            .map_err(|error| format!("{filename} does not unpack: {error}"))?;
        let roots: Vec<PathBuf> = std::fs::read_dir(scratch)
            .map_err(|error| format!("{}: {error}", scratch.display()))?
            .filter_map(|entry| entry.ok().map(|entry| entry.path()))
            .filter(|path| path.is_dir())
            .collect();
        match roots.as_slice() {
            [root] => root.clone(),
            _ => return Err(format!("{filename} has {} roots", roots.len())),
        }
    };
    let names = public_surface(&root);
    let cleaned = std::fs::remove_dir_all(scratch);
    let names = names?;
    cleaned.map_err(|error| format!("{} was not removed: {error}", scratch.display()))?;
    Ok((names, digest))
}

#[derive(Serialize)]
struct Baseline {
    version: String,
    source: String,
    surface: Vec<String>,
}

/// Rewrite `released-surface.json` in `repository`, or print it with `--stdout`.
pub fn run(repository: &Path, scratch: &Path, to_stdout: bool) -> Result<(), String> {
    let (artifact, marker, version) = published_artifact()?;
    let (names, digest) = recovered_surface(&artifact, scratch)?;
    let filename = artifact["filename"].as_str().unwrap_or_default();
    let document = Baseline {
        version: version.clone(),
        source: format!(
            "{marker}:{filename} recovered by wisent-optimizer-release baseline from the artifact PyPI serves for \
             {PROJECT} {version} (sha256 {digest}), read with wisent-optimizer-release surface without importing it"
        ),
        surface: names,
    };
    let rendered =
        serde_json::to_string_pretty(&document).map_err(|error| error.to_string())? + "\n";
    if to_stdout {
        print!("{rendered}");
        return Ok(());
    }
    let path = repository.join("released-surface.json");
    std::fs::write(&path, rendered).map_err(|error| format!("{}: {error}", path.display()))?;
    eprintln!("wrote {}", path.display());
    Ok(())
}
