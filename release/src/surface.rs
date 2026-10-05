//! The public surface of wisent-optimizer: the names its package initialisers
//! re-export in `__all__` (`export:<module>:<name>`) and the `optimization_type`
//! strings `run_steering_optimization` dispatches on
//! (`optimization_type:<value>`). Both are read with a parser, never by
//! importing, so the answer does not depend on optuna, hyperopt or the sibling
//! wisent distributions, and the same reader runs against an unpacked artifact.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use tree_sitter::{Node, Parser, Tree};

const PACKAGE: [&str; 4] = ["wisent", "core", "control", "steering_optimizer"];
const DISPATCH_PARAMETER: &str = "optimization_type";
const ALL: &str = "__all__";
const INIT: &str = "__init__.py";

fn text<'s>(node: Node, source: &'s str) -> &'s str {
    &source[node.byte_range()]
}

fn parse(path: &Path) -> Result<(String, Tree), String> {
    let refuse = |reason: String| format!("{}: surface is unknown because it cannot be parsed: {reason}", path.display());
    let source = std::fs::read_to_string(path).map_err(|error| refuse(error.to_string()))?;
    let mut parser = Parser::new();
    parser
        .set_language(&tree_sitter_python::LANGUAGE.into())
        .map_err(|error| refuse(error.to_string()))?;
    let tree = parser.parse(&source, None).ok_or_else(|| refuse("no syntax tree".to_string()))?;
    if tree.root_node().has_error() {
        return Err(refuse("invalid Python".to_string()));
    }
    Ok((source, tree))
}

/// A plain string literal's value: no bytes or template prefix, no interpolation or escape.
fn string_literal(node: Node, source: &str) -> Option<String> {
    if node.kind() != "string" {
        return None;
    }
    let mut value = String::new();
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        match child.kind() {
            "string_start" => {
                let prefix = text(child, source).trim_end_matches(['"', '\'']).to_ascii_lowercase();
                if prefix.contains('b') || prefix.contains('f') || prefix.contains('t') {
                    return None;
                }
            }
            "string_content" => {
                let mut inner = child.walk();
                if child.children(&mut inner).next().is_some() {
                    return None;
                }
                value.push_str(text(child, source));
            }
            "string_end" => {}
            _ => return None,
        }
    }
    Some(value)
}

fn sequence(node: Node) -> bool {
    matches!(node.kind(), "list" | "tuple" | "set" | "expression_list")
}

fn every_node(root: Node) -> Vec<Node> {
    let mut found = Vec::new();
    let mut pending = vec![root];
    while let Some(node) = pending.pop() {
        found.push(node);
        let mut cursor = node.walk();
        let children: Vec<Node> = node.children(&mut cursor).collect();
        pending.extend(children.into_iter().rev());
    }
    found
}

/// Every entry of every `__all__` assignment, plain, annotated or augmented.
fn exported_names(root: Node, source: &str, path: &Path) -> Result<Vec<String>, String> {
    let mut found: Option<Vec<String>> = None;
    for node in every_node(root) {
        if !matches!(node.kind(), "assignment" | "augmented_assignment") {
            continue;
        }
        let Some(left) = node.child_by_field_name("left") else { continue };
        if left.kind() != "identifier" || text(left, source) != ALL {
            continue;
        }
        let value = node
            .child_by_field_name("right")
            .filter(|value| sequence(*value))
            .ok_or_else(|| format!("{}: __all__ is not a literal sequence", path.display()))?;
        let mut cursor = value.walk();
        for element in value.named_children(&mut cursor).filter(|element| element.kind() != "comment") {
            let name = string_literal(element, source)
                .ok_or_else(|| format!("{}: __all__ has a non-literal entry", path.display()))?;
            found.get_or_insert_with(Vec::new).push(name);
        }
        found.get_or_insert_with(Vec::new);
    }
    found.ok_or_else(|| format!("{}: package initializer has no __all__", path.display()))
}

/// Every string `optimization_type` is compared against with `==` or `in`.
fn dispatch_names(root: Node, source: &str) -> Vec<String> {
    let mut found = Vec::new();
    for node in every_node(root) {
        if node.kind() != "comparison_operator" {
            continue;
        }
        let mut cursor = node.walk();
        let children: Vec<Node> = node.children(&mut cursor).collect();
        let Some((left, rest)) = children.split_first() else { continue };
        if left.kind() != "identifier" || text(*left, source) != DISPATCH_PARAMETER {
            continue;
        }
        for pair in rest.chunks(2) {
            let [operator, comparator] = pair else { continue };
            if !matches!(operator.kind(), "==" | "in") {
                continue;
            }
            if sequence(*comparator) {
                let mut inner = comparator.walk();
                found.extend(comparator.named_children(&mut inner).filter_map(|item| string_literal(item, source)));
            } else if let Some(value) = string_literal(*comparator, source) {
                found.push(value);
            }
        }
    }
    found
}

fn python_files(directory: &Path, found: &mut Vec<PathBuf>) -> Result<(), String> {
    let entries = std::fs::read_dir(directory).map_err(|error| format!("{}: {error}", directory.display()))?;
    for entry in entries {
        let path = entry.map_err(|error| format!("{}: {error}", directory.display()))?.path();
        if path.is_dir() {
            python_files(&path, found)?;
        } else if path.extension().is_some_and(|extension| extension == "py") {
            found.push(path);
        }
    }
    Ok(())
}

/// The sorted public surface of the package under `root`.
pub fn public_surface(root: &Path) -> Result<Vec<String>, String> {
    let package = PACKAGE.iter().fold(root.to_path_buf(), |path, part| path.join(part));
    if !package.is_dir() {
        return Err(format!("{} is not a directory", package.display()));
    }
    let mut files = Vec::new();
    python_files(&package, &mut files)?;
    files.sort();
    let mut names = BTreeSet::new();
    for path in files {
        let (source, tree) = parse(&path)?;
        if path.file_name().is_some_and(|name| name == INIT) {
            let relative = path.parent().unwrap_or(&path).strip_prefix(root).unwrap_or(&path);
            let module: Vec<String> = relative.iter().map(|part| part.to_string_lossy().into_owned()).collect();
            let module = module.join(".");
            for name in exported_names(tree.root_node(), &source, &path)? {
                names.insert(format!("export:{module}:{name}"));
            }
        }
        for name in dispatch_names(tree.root_node(), &source) {
            names.insert(format!("{DISPATCH_PARAMETER}:{name}"));
        }
    }
    if names.is_empty() {
        return Err(format!("no promised names found under {}", package.display()));
    }
    Ok(names.into_iter().collect())
}
