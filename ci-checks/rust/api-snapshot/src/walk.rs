//! Finds every public path of the crate's items: through public modules and `pub use` re-exports,
//! breadth-first, so each item's first path is its shortest (its canonical path).

use crate::rustdoc::{item, kind_name};
use rustdoc_types::{Crate, Id, Item, ItemEnum, ItemKind, StructKind, Use};
use std::collections::{HashMap, HashSet};

/// Rust's namespaces, as bits: one name can be bound once in each.
const TYPE: u8 = 1;
const VALUE: u8 = 2;
const MACRO: u8 = 4;

/// The namespaces a binding of this kind takes. A unit or tuple struct is also a value (its
/// constructor); another crate's struct (no item to look at) is taken to be one, so it shadows both.
fn namespaces(kind: ItemKind, item: Option<&Item>) -> u8 {
    match kind {
        ItemKind::Struct => match item.map(|i| &i.inner) {
            Some(ItemEnum::Struct(s)) if matches!(s.kind, StructKind::Plain { .. }) => TYPE,
            _ => TYPE | VALUE,
        },
        ItemKind::Variant => TYPE | VALUE,
        ItemKind::Module
        | ItemKind::ExternCrate
        | ItemKind::Union
        | ItemKind::Enum
        | ItemKind::TypeAlias
        | ItemKind::Trait
        | ItemKind::TraitAlias
        | ItemKind::ExternType
        | ItemKind::AssocType
        | ItemKind::Primitive
        | ItemKind::Keyword => TYPE,
        ItemKind::Function
        | ItemKind::Constant
        | ItemKind::Static
        | ItemKind::AssocConst
        | ItemKind::StructField => VALUE,
        ItemKind::Macro | ItemKind::ProcAttribute | ItemKind::ProcDerive | ItemKind::Attribute => {
            MACRO
        }
        ItemKind::Use | ItemKind::Impl => 0,
    }
}

/// rustdoc's kind for a re-exported item of another crate, or `use` when rustdoc does not know it.
pub fn external_kind(krate: &Crate, re_export: &Use) -> &'static str {
    match re_export.id.and_then(|id| krate.paths.get(&id)) {
        Some(summary) if !re_export.is_glob => kind_name(summary.kind),
        _ => "use",
    }
}

enum Binding<'a> {
    Local(&'a Item),
    /// A re-export rustdoc does not resolve to an item of this crate: another crate's item, or a glob of one.
    External(&'a Use),
}

pub struct Public<'a> {
    /// Each (public path, item) pair once, shortest paths first.
    pub items: Vec<(Vec<String>, &'a Item)>,
    /// Re-exports of other crates' items: the re-exporting module's path, the `use`, its kind.
    pub external: Vec<(Vec<String>, &'a Use, &'static str)>,
    /// Each reachable item's canonical path; the crate root's is empty.
    pub canonical: HashMap<Id, Vec<String>>,
}

struct Walker<'a> {
    krate: &'a Crate,
}

impl<'a> Walker<'a> {
    fn item(&self, id: &Id) -> Result<&'a Item, String> {
        item(self.krate, id)
    }

    fn local(&self, id: Option<Id>) -> Option<&'a Item> {
        id.and_then(|id| self.krate.index.get(&id))
            .filter(|item| item.crate_id == 0)
    }

    /// The names a module binds, with each binding's kind, sorted by name and kind. A name bound
    /// explicitly shadows a glob's binding of that name in the same namespace, as in Rust; between
    /// globs, the first by source path wins. An external re-export whose kind rustdoc does not know
    /// takes every namespace.
    fn bindings(
        &self,
        module: &Id,
        globbed: &mut HashSet<Id>,
    ) -> Result<Vec<(String, &'static str, Binding<'a>)>, String> {
        let ItemEnum::Module(m) = &self.item(module)?.inner else {
            return Err(format!("item {} is not a module", module.0));
        };
        let mut bound = Vec::new();
        let mut taken: HashMap<String, u8> = HashMap::new();
        let mut globs = Vec::new();
        for id in &m.items {
            let item = self.item(id)?;
            let (name, target) = match &item.inner {
                ItemEnum::Use(u) if u.is_glob => {
                    globs.push(u);
                    continue;
                }
                ItemEnum::Use(u) => (u.name.clone(), self.local(u.id)),
                ItemEnum::Impl(_) | ItemEnum::Primitive(_) => continue,
                _ => match &item.name {
                    Some(name) => (name.clone(), Some(item)),
                    None => continue,
                },
            };
            let (kind, spaces, binding) = match (target, &item.inner) {
                (Some(target), _) => {
                    let kind = target.inner.item_kind();
                    (
                        kind_name(kind),
                        namespaces(kind, Some(target)),
                        Binding::Local(target),
                    )
                }
                (None, ItemEnum::Use(u)) => {
                    let spaces = match u.id.and_then(|id| self.krate.paths.get(&id)) {
                        Some(summary) => namespaces(summary.kind, None),
                        None => TYPE | VALUE | MACRO,
                    };
                    (external_kind(self.krate, u), spaces, Binding::External(u))
                }
                (None, _) => continue,
            };
            *taken.entry(name.clone()).or_default() |= spaces;
            bound.push((name, kind, binding));
        }
        globs.sort_by(|a, b| a.source.cmp(&b.source));
        for glob in globs {
            match self.local(glob.id) {
                Some(target) if matches!(target.inner, ItemEnum::Module(_)) => {
                    if !globbed.insert(target.id) {
                        continue;
                    }
                    for (name, kind, binding) in self.bindings(&target.id, globbed)? {
                        let spaces = match &binding {
                            Binding::Local(item) => namespaces(item.inner.item_kind(), Some(item)),
                            Binding::External(u) => {
                                match u.id.and_then(|id| self.krate.paths.get(&id)) {
                                    Some(summary) if !u.is_glob => namespaces(summary.kind, None),
                                    _ if u.is_glob => 0,
                                    _ => TYPE | VALUE | MACRO,
                                }
                            }
                        };
                        let taken = taken.entry(name.clone()).or_default();
                        if spaces != 0 && spaces & !*taken == 0 {
                            continue;
                        }
                        *taken |= spaces;
                        bound.push((name, kind, binding));
                    }
                }
                _ => bound.push((
                    format!("{}::*", glob.source),
                    "use",
                    Binding::External(glob),
                )),
            }
        }
        bound.sort_by(|a, b| (&a.0, a.1).cmp(&(&b.0, b.1)));
        Ok(bound)
    }
}

pub fn walk(krate: &Crate) -> Result<Public<'_>, String> {
    let walker = Walker { krate };
    let mut public = Public {
        items: Vec::new(),
        external: Vec::new(),
        canonical: HashMap::from([(krate.root, Vec::new())]),
    };
    let mut expanded = HashSet::from([krate.root]);
    let mut emitted = HashSet::new();
    let mut level = vec![(krate.root, Vec::<String>::new())];
    while !level.is_empty() {
        level.sort_by_key(|(_, path)| path.join("::"));
        let mut next = Vec::new();
        for (module, path) in level {
            for (name, _, binding) in walker.bindings(&module, &mut HashSet::from([module]))? {
                match binding {
                    Binding::External(u) => {
                        public
                            .external
                            .push((path.clone(), u, external_kind(krate, u)))
                    }
                    Binding::Local(item) => {
                        let mut at = path.clone();
                        at.push(name);
                        if !emitted.insert((at.join("::"), item.id)) {
                            continue;
                        }
                        public
                            .canonical
                            .entry(item.id)
                            .or_insert_with(|| at.clone());
                        if matches!(item.inner, ItemEnum::Module(_)) && expanded.insert(item.id) {
                            next.push((item.id, at.clone()));
                        }
                        public.items.push((at, item));
                    }
                }
            }
        }
        level = next;
    }
    Ok(public)
}
