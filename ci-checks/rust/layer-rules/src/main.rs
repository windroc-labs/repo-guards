//! Layer rules scanner for Rust: lists each reference site and the project files it resolves to.
//!
//! Protocol: ci-checks/python/layer-rules/imports.py. A site is one `use` or `extern crate` item, or one path of two or
//! more segments outside them that names a module of this crate or of a workspace crate without going through a `use`.
//! Paths resolve to module files by Cargo's layout conventions and the Cargo.toml files the core hands over.

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::path::Path;
use std::process::ExitCode;

use serde_json::{Value, json};
use syn::punctuated::Punctuated;
use syn::spanned::Spanned;
use syn::visit::{self, Visit};
use syn::{Attribute, Item, Meta, Token, UseTree};

const USAGE: &str = "usage: layer-rules <side root>  (JSON request on stdin)";
const DEPENDENCY_TABLES: [&str; 3] = ["dependencies", "dev-dependencies", "build-dependencies"];
const MAX_ALIAS_DEPTH: usize = 16;

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 1 {
        eprintln!("{USAGE}");
        return ExitCode::from(2);
    }
    let mut input = String::new();
    let result = std::io::stdin()
        .read_to_string(&mut input)
        .map_err(|e| e.to_string())
        .and_then(|_| run(Path::new(&args[0]), &input));
    match result {
        Ok(lines) => {
            for line in lines {
                println!("{line}");
            }
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("layer-rules: {error}");
            ExitCode::from(2)
        }
    }
}

// Paths: relative, "/"-separated, "" for the root.

fn join(dir: &str, rel: &str) -> String {
    if dir.is_empty() {
        rel.to_string()
    } else {
        format!("{dir}/{rel}")
    }
}

fn parent(path: &str) -> &str {
    path.rfind('/').map_or("", |i| &path[..i])
}

fn is_under(dir: &str, path: &str) -> bool {
    dir.is_empty()
        || path
            .strip_prefix(dir)
            .is_some_and(|rest| rest.starts_with('/'))
}

/// Lexically normalized; a path that leaves the root keeps its leading "..".
fn normalize(path: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/') {
        match part {
            "" | "." => {}
            ".." if parts.last().is_some_and(|p| *p != "..") => {
                parts.pop();
            }
            _ => parts.push(part),
        }
    }
    parts.join("/")
}

fn relative(value: &Value) -> Result<String, String> {
    let path = value.as_str().ok_or_else(|| format!("bad path {value}"))?;
    if path.is_empty() || path.starts_with('/') || path.split('/').any(|p| p == "..") {
        return Err(format!("bad path {value}"));
    }
    Ok(path.to_string())
}

fn paths(request: &Value, key: &str) -> Result<Vec<String>, String> {
    let list = request
        .get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("request has no list {key:?}"))?;
    list.iter().map(relative).collect()
}

// Cargo layout.

/// Where `crate::…` resolves: segments name files under module_dir; no segment names the root file.
#[derive(Clone, Debug, PartialEq)]
struct Crate {
    module_dir: String,
    root: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
enum Base {
    Crate(Crate),
    /// A crate outside the working-directory, as its normalized "../…" directory.
    Outside(String),
}

#[derive(Clone, Debug)]
struct Abs {
    base: Base,
    segs: Vec<String>,
}

struct Package {
    dir: String,
    lib_name: String,
    lib_root: Option<String>,
    roots: Vec<String>,
    externs: BTreeMap<String, Base>,
}

struct Project {
    files: BTreeSet<String>,
    packages: BTreeMap<String, Package>,
}

fn str_at<'a>(table: &'a toml::Table, keys: &[&str]) -> Option<&'a str> {
    let (last, path) = keys.split_last()?;
    let mut table = table;
    for key in path {
        table = table.get(*key)?.as_table()?;
    }
    table.get(*last)?.as_str()
}

fn snake(name: &str) -> String {
    name.replace('-', "_")
}

fn auto_root(rel: &str) -> bool {
    let parts: Vec<&str> = rel.split('/').collect();
    match parts.as_slice() {
        ["build.rs"] | ["src", "main.rs"] => true,
        ["src", "bin", file] => file.ends_with(".rs"),
        ["src", "bin", _, "main.rs"] => true,
        [dir, file] => ["tests", "examples", "benches"].contains(dir) && file.ends_with(".rs"),
        [dir, _, "main.rs"] => ["tests", "examples", "benches"].contains(dir),
        _ => false,
    }
}

impl Project {
    fn new(root: &Path, files: BTreeSet<String>, context: &[String]) -> Result<Project, String> {
        let mut manifests = BTreeMap::new();
        for path in context
            .iter()
            .filter(|p| *p == "Cargo.toml" || p.ends_with("/Cargo.toml"))
        {
            let text =
                std::fs::read_to_string(root.join(path)).map_err(|e| format!("{path}: {e}"))?;
            // Cargo itself rejects a manifest that does not parse; here it just tells nothing.
            if let Ok(table) = toml::from_str::<toml::Table>(&text) {
                manifests.insert(parent(path).to_string(), table);
            }
        }
        let mut packages = BTreeMap::new();
        for (dir, table) in &manifests {
            let Some(name) = str_at(table, &["package", "name"]) else {
                continue;
            };
            let lib_root = match str_at(table, &["lib", "path"]) {
                Some(path) => Some(normalize(&join(dir, path))),
                None => Some(join(dir, "src/lib.rs")),
            }
            .filter(|p| files.contains(p));
            let mut roots: Vec<String> = lib_root.iter().cloned().collect();
            for kind in ["bin", "test", "example", "bench"] {
                for target in table
                    .get(kind)
                    .and_then(|v| v.as_array())
                    .into_iter()
                    .flatten()
                {
                    if let Some(path) = target
                        .as_table()
                        .and_then(|t| t.get("path"))
                        .and_then(|p| p.as_str())
                    {
                        roots.push(normalize(&join(dir, path)));
                    }
                }
            }
            if let Some(build) = str_at(table, &["package", "build"]) {
                roots.push(normalize(&join(dir, build)));
            }
            let nested = |f: &String| {
                manifests
                    .keys()
                    .any(|d| d != dir && is_under(dir, d) && is_under(d, f))
            };
            for file in files.iter().filter(|f| is_under(dir, f) && !nested(f)) {
                let rel = if dir.is_empty() {
                    file.as_str()
                } else {
                    &file[dir.len() + 1..]
                };
                if auto_root(rel) {
                    roots.push(file.clone());
                }
            }
            roots.retain(|r| files.contains(r));
            roots.sort();
            roots.dedup();
            let lib_name = snake(str_at(table, &["lib", "name"]).unwrap_or(name));
            packages.insert(
                dir.clone(),
                Package {
                    dir: dir.clone(),
                    lib_name,
                    lib_root,
                    roots,
                    externs: BTreeMap::new(),
                },
            );
        }
        let lib_of = |packages: &BTreeMap<String, Package>, dir: &str| -> Option<(String, Base)> {
            let package = packages.get(dir)?;
            let root = package.lib_root.clone()?;
            Some((
                package.lib_name.clone(),
                Base::Crate(Crate {
                    module_dir: parent(&root).to_string(),
                    root: Some(root),
                }),
            ))
        };
        let mut all_externs = BTreeMap::new();
        for (dir, table) in &manifests {
            if !packages.contains_key(dir) {
                continue;
            }
            let mut externs = BTreeMap::new();
            for (key, spec) in dependencies(table) {
                let Some((location, renamed)) = dependency_dir(&manifests, dir, key, spec) else {
                    continue;
                };
                let name = snake(key);
                if location.starts_with("..") {
                    externs.insert(name, Base::Outside(location));
                } else if let Some((lib_name, base)) = lib_of(&packages, &location) {
                    externs.insert(if renamed { name } else { lib_name }, base);
                }
            }
            if let Some((lib_name, base)) = lib_of(&packages, dir) {
                externs.entry(lib_name).or_insert(base);
            }
            all_externs.insert(dir.clone(), externs);
        }
        for (dir, externs) in all_externs {
            packages.get_mut(&dir).expect("listed above").externs = externs;
        }
        Ok(Project { files, packages })
    }

    /// The file's package and crate; None when no package holds it.
    fn crate_of(&self, file: &str) -> Option<(&Package, Crate)> {
        let package = self
            .packages
            .values()
            .filter(|p| is_under(&p.dir, file))
            .max_by_key(|p| p.dir.len())?;
        if package.roots.iter().any(|r| r == file) {
            return Some((
                package,
                Crate {
                    module_dir: parent(file).to_string(),
                    root: Some(file.to_string()),
                },
            ));
        }
        let module_dir = package
            .roots
            .iter()
            .map(|r| parent(r))
            .filter(|d| is_under(d, file))
            .max_by_key(|d| d.len())?
            .to_string();
        let candidates: Vec<&String> = package
            .roots
            .iter()
            .filter(|r| parent(r) == module_dir)
            .collect();
        let main = join(&package.dir, "src/main.rs");
        let root = package
            .lib_root
            .as_ref()
            .filter(|r| candidates.contains(r))
            .or_else(|| candidates.iter().copied().find(|r| **r == main))
            .or(if candidates.len() == 1 {
                Some(candidates[0])
            } else {
                None
            })
            .cloned();
        Some((package, Crate { module_dir, root }))
    }

    fn resolve(&self, abs: &Abs) -> Option<String> {
        let krate = match &abs.base {
            Base::Outside(dir) => return Some(dir.clone()),
            Base::Crate(krate) => krate,
        };
        for end in (1..=abs.segs.len()).rev() {
            let base = join(&krate.module_dir, &abs.segs[..end].join("/"));
            for candidate in [format!("{base}.rs"), format!("{base}/mod.rs")] {
                if self.files.contains(&candidate) {
                    return Some(candidate);
                }
            }
        }
        krate.root.clone()
    }
}

fn dependencies(table: &toml::Table) -> Vec<(&String, &toml::Value)> {
    let mut tables: Vec<&toml::Table> = DEPENDENCY_TABLES
        .iter()
        .filter_map(|k| table.get(*k)?.as_table())
        .collect();
    for target in table
        .get("target")
        .and_then(|t| t.as_table())
        .into_iter()
        .flat_map(|t| t.values())
    {
        tables.extend(
            DEPENDENCY_TABLES
                .iter()
                .filter_map(|k| target.as_table()?.get(*k)?.as_table()),
        );
    }
    tables.into_iter().flatten().collect()
}

/// The dependency's normalized directory and whether `package =` renames it; None for registry and unknown ones.
fn dependency_dir(
    manifests: &BTreeMap<String, toml::Table>,
    dir: &str,
    key: &str,
    spec: &toml::Value,
) -> Option<(String, bool)> {
    let spec = spec.as_table()?;
    let mut renamed = spec.contains_key("package");
    if spec.get("workspace").and_then(|w| w.as_bool()) == Some(true) {
        let mut at = Some(dir);
        let workspace = loop {
            let here = at?;
            if let Some(table) = manifests.get(here).filter(|t| t.contains_key("workspace")) {
                break (here, table);
            }
            at = if here.is_empty() {
                None
            } else {
                Some(parent(here))
            };
        };
        let entry = workspace
            .1
            .get("workspace")?
            .as_table()?
            .get("dependencies")?
            .get(key)?
            .as_table()?;
        renamed |= entry.contains_key("package");
        return Some((
            normalize(&join(workspace.0, entry.get("path")?.as_str()?)),
            renamed,
        ));
    }
    Some((normalize(&join(dir, spec.get("path")?.as_str()?)), renamed))
}

// cfg(test).

/// `test`, or `all(...)` with a test-only member; `any(...)` and `not(...)` are not.
fn test_only(meta: &Meta) -> bool {
    match meta {
        Meta::Path(path) => path.is_ident("test"),
        Meta::List(list) if list.path.is_ident("all") => list
            .parse_args_with(Punctuated::<Meta, Token![,]>::parse_terminated)
            .is_ok_and(|members| members.iter().any(test_only)),
        _ => false,
    }
}

fn is_test(attrs: &[Attribute]) -> bool {
    attrs.iter().any(|attr| {
        attr.path().is_ident("cfg") && attr.parse_args::<Meta>().is_ok_and(|m| test_only(&m))
    })
}

fn ident(name: &syn::Ident) -> String {
    let text = name.to_string();
    text.strip_prefix("r#").map(str::to_string).unwrap_or(text)
}

/// The module paths, under one module dir, that only exist in test builds.
fn test_modules(items: &[Item], module: &[String], out: &mut BTreeSet<Vec<String>>) {
    for item in items {
        if let Item::Mod(m) = item {
            let path = [module, &[ident(&m.ident)]].concat();
            if is_test(&m.attrs) {
                out.insert(path);
            } else if let Some((_, items)) = &m.content {
                test_modules(items, &path, out);
            }
        }
    }
}

fn module_path(file: &str, krate: &Crate) -> Vec<String> {
    if krate.root.as_deref() == Some(file) {
        return Vec::new();
    }
    let rel = if krate.module_dir.is_empty() {
        file
    } else {
        &file[krate.module_dir.len() + 1..]
    };
    let mut parts: Vec<String> = rel
        .trim_end_matches(".rs")
        .split('/')
        .map(str::to_string)
        .collect();
    if parts.last().is_some_and(|p| p == "mod") {
        parts.pop();
    }
    parts
}

// Sites.

struct Binding {
    global: bool,
    segs: Vec<String>,
    scope: usize,
}

enum Name<'a> {
    Alias(&'a Binding),
    /// A child module of the scope at this index.
    Module(usize),
}

struct Scope {
    module: Vec<String>,
    aliases: BTreeMap<String, Binding>,
    mods: BTreeSet<String>,
    boundary: bool,
}

/// Each leaf of a use tree: its full path and the name it binds, if any.
fn use_leaves(
    tree: &UseTree,
    prefix: &mut Vec<String>,
    out: &mut Vec<(Vec<String>, Option<String>)>,
) {
    let leaf = |prefix: &Vec<String>, name: &syn::Ident| -> Vec<String> {
        if name == "self" {
            prefix.clone()
        } else {
            [prefix.as_slice(), &[ident(name)]].concat()
        }
    };
    match tree {
        UseTree::Path(p) => {
            prefix.push(ident(&p.ident));
            use_leaves(&p.tree, prefix, out);
            prefix.pop();
        }
        UseTree::Name(n) => {
            let path = leaf(prefix, &n.ident);
            out.push((path.clone(), path.last().cloned()));
        }
        UseTree::Rename(r) => out.push((
            leaf(prefix, &r.ident),
            Some(ident(&r.rename)).filter(|n| n != "_"),
        )),
        UseTree::Glob(_) => out.push((prefix.clone(), None)),
        UseTree::Group(g) => g
            .items
            .iter()
            .for_each(|item| use_leaves(item, prefix, out)),
    }
}

struct Walker<'a> {
    project: &'a Project,
    file: &'a str,
    own: Crate,
    externs: &'a BTreeMap<String, Base>,
    scopes: Vec<Scope>,
    sites: Vec<(usize, BTreeSet<String>)>,
}

impl Walker<'_> {
    fn push_scope(&mut self, module: Vec<String>, items: &[&Item], boundary: bool) {
        let scope = self.scopes.len();
        let mut aliases = BTreeMap::new();
        let mut mods = BTreeSet::new();
        for item in items {
            match item {
                Item::Use(u) if !is_test(&u.attrs) => {
                    let mut leaves = Vec::new();
                    use_leaves(&u.tree, &mut Vec::new(), &mut leaves);
                    for (segs, name) in leaves {
                        if let Some(name) = name {
                            aliases.insert(
                                name,
                                Binding {
                                    global: u.leading_colon.is_some(),
                                    segs,
                                    scope,
                                },
                            );
                        }
                    }
                }
                Item::ExternCrate(e) if !is_test(&e.attrs) => {
                    let name = e
                        .rename
                        .as_ref()
                        .map_or_else(|| ident(&e.ident), |(_, r)| ident(r));
                    let (global, segs) = if e.ident == "self" {
                        (false, vec!["crate".into()])
                    } else {
                        (true, vec![ident(&e.ident)])
                    };
                    if name != "_" {
                        aliases.insert(
                            name,
                            Binding {
                                global,
                                segs,
                                scope,
                            },
                        );
                    }
                }
                Item::Mod(m) if !is_test(&m.attrs) => {
                    mods.insert(ident(&m.ident));
                }
                _ => {}
            }
        }
        self.scopes.push(Scope {
            module,
            aliases,
            mods,
            boundary,
        });
    }

    fn absolute(&self, global: bool, segs: &[String], scope: usize, depth: usize) -> Option<Abs> {
        let (first, rest) = segs.split_first()?;
        if depth > MAX_ALIAS_DEPTH {
            return None;
        }
        let own = |segs: Vec<String>| {
            Some(Abs {
                base: Base::Crate(self.own.clone()),
                segs,
            })
        };
        if global {
            return Some(Abs {
                base: self.externs.get(first)?.clone(),
                segs: rest.to_vec(),
            });
        }
        match first.as_str() {
            "crate" => return own(rest.to_vec()),
            "self" | "super" => {
                let mut module = self.scopes[scope].module.clone();
                let mut segs = if first == "self" { rest } else { segs };
                while let Some((_, tail)) = segs.split_first().filter(|(head, _)| *head == "super")
                {
                    module.pop()?;
                    segs = tail;
                }
                return own([module, segs.to_vec()].concat());
            }
            _ => {}
        }
        match self.lookup(first, scope) {
            Some(Name::Alias(binding)) => {
                let expanded = [binding.segs.as_slice(), rest].concat();
                self.absolute(binding.global, &expanded, binding.scope, depth + 1)
            }
            Some(Name::Module(at)) => own([self.scopes[at].module.as_slice(), segs].concat()),
            None => Some(Abs {
                base: self.externs.get(first)?.clone(),
                segs: rest.to_vec(),
            }),
        }
    }

    /// What a leading name binds in scope, searching outward to the enclosing module's scope.
    fn lookup(&self, name: &str, scope: usize) -> Option<Name<'_>> {
        for at in (0..=scope).rev() {
            let here = &self.scopes[at];
            if let Some(binding) = here.aliases.get(name) {
                return Some(Name::Alias(binding));
            }
            if here.mods.contains(name) {
                return Some(Name::Module(at));
            }
            if here.boundary {
                break;
            }
        }
        None
    }

    fn site(&mut self, line: usize, targets: BTreeSet<String>) {
        let targets: BTreeSet<String> = targets.into_iter().filter(|t| t != self.file).collect();
        if !targets.is_empty() {
            self.sites.push((line, targets));
        }
    }

    fn scope(&self) -> usize {
        self.scopes.len() - 1
    }

    fn items(&mut self, items: &[Item]) {
        for item in items {
            self.visit_item(item);
        }
    }
}

impl<'ast> Visit<'ast> for Walker<'_> {
    fn visit_attribute(&mut self, _: &'ast Attribute) {}

    fn visit_visibility(&mut self, _: &'ast syn::Visibility) {}

    fn visit_item(&mut self, item: &'ast Item) {
        let attrs = match item {
            Item::Use(u) => &u.attrs,
            Item::ExternCrate(e) => &e.attrs,
            Item::Mod(m) => &m.attrs,
            Item::Fn(f) => &f.attrs,
            Item::Impl(i) => &i.attrs,
            Item::Trait(t) => &t.attrs,
            Item::Const(c) => &c.attrs,
            Item::Static(s) => &s.attrs,
            Item::Struct(s) => &s.attrs,
            Item::Enum(e) => &e.attrs,
            Item::Union(u) => &u.attrs,
            Item::Type(t) => &t.attrs,
            Item::Macro(m) => &m.attrs,
            Item::ForeignMod(f) => &f.attrs,
            Item::TraitAlias(t) => &t.attrs,
            _ => return visit::visit_item(self, item),
        };
        if is_test(attrs) {
            return;
        }
        match item {
            Item::Use(u) => {
                let mut leaves = Vec::new();
                use_leaves(&u.tree, &mut Vec::new(), &mut leaves);
                let scope = self.scope();
                let global = u.leading_colon.is_some();
                let targets = leaves
                    .iter()
                    .filter_map(|(segs, _)| self.absolute(global, segs, scope, 0))
                    .filter_map(|abs| self.project.resolve(&abs))
                    .collect();
                self.site(u.use_token.span.start().line, targets);
            }
            Item::ExternCrate(e) => {
                let segs = if e.ident == "self" {
                    vec!["crate".to_string()]
                } else {
                    vec![ident(&e.ident)]
                };
                let abs = self.absolute(e.ident != "self", &segs, self.scope(), 0);
                let targets = abs
                    .and_then(|a| self.project.resolve(&a))
                    .into_iter()
                    .collect();
                self.site(e.extern_token.span.start().line, targets);
            }
            Item::Mod(m) => {
                if let Some((_, items)) = &m.content {
                    let module = [
                        self.scopes[self.scope()].module.as_slice(),
                        &[ident(&m.ident)],
                    ]
                    .concat();
                    self.push_scope(module, &items.iter().collect::<Vec<_>>(), true);
                    self.items(items);
                    self.scopes.pop();
                }
            }
            _ => visit::visit_item(self, item),
        }
    }

    fn visit_block(&mut self, block: &'ast syn::Block) {
        let items: Vec<&Item> = block
            .stmts
            .iter()
            .filter_map(|s| {
                if let syn::Stmt::Item(i) = s {
                    Some(i)
                } else {
                    None
                }
            })
            .collect();
        let module = self.scopes[self.scope()].module.clone();
        self.push_scope(module, &items, false);
        visit::visit_block(self, block);
        self.scopes.pop();
    }

    fn visit_stmt(&mut self, stmt: &'ast syn::Stmt) {
        let skip = match stmt {
            syn::Stmt::Local(local) => is_test(&local.attrs),
            syn::Stmt::Expr(expr, _) => expr_attrs(expr).is_some_and(|attrs| is_test(attrs)),
            _ => false,
        };
        if !skip {
            visit::visit_stmt(self, stmt);
        }
    }

    fn visit_impl_item(&mut self, item: &'ast syn::ImplItem) {
        let attrs = match item {
            syn::ImplItem::Const(c) => &c.attrs,
            syn::ImplItem::Fn(f) => &f.attrs,
            syn::ImplItem::Type(t) => &t.attrs,
            syn::ImplItem::Macro(m) => &m.attrs,
            _ => return visit::visit_impl_item(self, item),
        };
        if !is_test(attrs) {
            visit::visit_impl_item(self, item);
        }
    }

    fn visit_trait_item(&mut self, item: &'ast syn::TraitItem) {
        let attrs = match item {
            syn::TraitItem::Const(c) => &c.attrs,
            syn::TraitItem::Fn(f) => &f.attrs,
            syn::TraitItem::Type(t) => &t.attrs,
            syn::TraitItem::Macro(m) => &m.attrs,
            _ => return visit::visit_trait_item(self, item),
        };
        if !is_test(attrs) {
            visit::visit_trait_item(self, item);
        }
    }

    fn visit_path(&mut self, path: &'ast syn::Path) {
        let segs: Vec<String> = path.segments.iter().map(|s| ident(&s.ident)).collect();
        if segs.len() >= 2 && segs[0] != "Self" {
            let scope = self.scope();
            let global = path.leading_colon.is_some();
            let target = self
                .absolute(global, &segs, scope, 0)
                .and_then(|abs| self.project.resolve(&abs));
            // Through a use alias, the path is a site only where it reaches past the file the alias names.
            let counts = global
                || !matches!(self.lookup(&segs[0], scope), Some(Name::Alias(_)))
                || target
                    != self
                        .absolute(false, &segs[..1], scope, 0)
                        .and_then(|abs| self.project.resolve(&abs));
            if let Some(target) = target.filter(|_| counts) {
                self.site(path.span().start().line, BTreeSet::from([target]));
            }
        }
        visit::visit_path(self, path);
    }
}

fn expr_attrs(expr: &syn::Expr) -> Option<&Vec<Attribute>> {
    use syn::Expr::*;
    Some(match expr {
        Call(e) => &e.attrs,
        MethodCall(e) => &e.attrs,
        Macro(e) => &e.attrs,
        Path(e) => &e.attrs,
        Assign(e) => &e.attrs,
        Block(e) => &e.attrs,
        If(e) => &e.attrs,
        Match(e) => &e.attrs,
        Unsafe(e) => &e.attrs,
        _ => return None,
    })
}

fn run(root: &Path, input: &str) -> Result<Vec<String>, String> {
    let request: Value =
        serde_json::from_str(input).map_err(|e| format!("request is not JSON: {e}"))?;
    let files: BTreeSet<String> = paths(&request, "files")?.into_iter().collect();
    let scan = paths(&request, "scan")?;
    let context = paths(&request, "context")?;
    let project = Project::new(root, files, &context)?;

    let mut parsed = Vec::new();
    let mut records: Vec<(String, usize, Value)> = Vec::new();
    for path in &scan {
        let bytes = std::fs::read(root.join(path)).map_err(|e| format!("{path}: {e}"))?;
        let error = match String::from_utf8(bytes) {
            Err(_) => "not UTF-8".to_string(),
            Ok(text) => match syn::parse_file(&text) {
                Ok(file) => {
                    parsed.push((path, file));
                    continue;
                }
                Err(e) => format!("line {}: {e}", e.span().start().line),
            },
        };
        let error: String = error
            .lines()
            .next()
            .unwrap_or("")
            .chars()
            .take(300)
            .collect();
        records.push((path.clone(), 0, json!({"file": path, "error": error})));
    }

    let mut tests: BTreeMap<String, BTreeSet<Vec<String>>> = BTreeMap::new();
    for (path, file) in &parsed {
        if let Some((_, krate)) = project.crate_of(path) {
            let module = module_path(path, &krate);
            let found = tests.entry(krate.module_dir.clone()).or_default();
            if is_test(&file.attrs) {
                found.insert(module.clone());
            }
            test_modules(&file.items, &module, found);
        }
    }

    for (path, file) in &parsed {
        let Some((package, krate)) = project.crate_of(path) else {
            continue;
        };
        let module = module_path(path, &krate);
        let in_test = tests
            .get(&krate.module_dir)
            .is_some_and(|t| t.iter().any(|m| module.starts_with(m)));
        if in_test {
            continue;
        }
        let mut walker = Walker {
            project: &project,
            file: path,
            own: krate,
            externs: &package.externs,
            scopes: Vec::new(),
            sites: Vec::new(),
        };
        walker.push_scope(module, &file.items.iter().collect::<Vec<_>>(), true);
        walker.items(&file.items);
        for (line, targets) in walker.sites {
            records.push((
                (*path).clone(),
                line,
                json!({"file": path, "line": line, "targets": targets}),
            ));
        }
    }
    records.sort_by(|a, b| (&a.0, a.1).cmp(&(&b.0, b.1)));
    Ok(records
        .into_iter()
        .map(|(_, _, record)| record.to_string())
        .collect())
}
