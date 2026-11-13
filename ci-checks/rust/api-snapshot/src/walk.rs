//! Finds every public path of the crate's items: through public modules and `pub use` re-exports,
//! breadth-first, so each item's first path is its shortest (its canonical path).

use crate::render::kind_name;
use rustdoc_types::{Crate, Id, Item, ItemEnum, Use};
use std::collections::{HashMap, HashSet};

enum Binding<'a> {
    Local(&'a Item),
    /// A re-export rustdoc does not resolve to an item of this crate: another crate's item, or a glob of one.
    External(&'a Use),
}

pub struct Public<'a> {
    /// Each (public path, item) pair once, shortest paths first.
    pub items: Vec<(Vec<String>, &'a Item)>,
    /// Re-exports of other crates' items, with the path of the module re-exporting them.
    pub external: Vec<(Vec<String>, &'a Use)>,
    /// Each reachable item's canonical path; the crate root's is empty.
    pub canonical: HashMap<Id, Vec<String>>,
}

struct Walker<'a> {
    krate: &'a Crate,
}

impl<'a> Walker<'a> {
    fn item(&self, id: &Id) -> Result<&'a Item, String> {
        self.krate
            .index
            .get(id)
            .ok_or_else(|| format!("rustdoc JSON has no item {}", id.0))
    }

    fn local(&self, id: Option<Id>) -> Option<&'a Item> {
        id.and_then(|id| self.krate.index.get(&id))
            .filter(|item| item.crate_id == 0)
    }

    /// The names a module binds, with each binding's kind, sorted. A name bound explicitly shadows
    /// the same name and kind from a glob; between globs, the first by source path wins.
    fn bindings(
        &self,
        module: &Id,
        globbed: &mut HashSet<Id>,
    ) -> Result<Vec<(String, &'static str, Binding<'a>)>, String> {
        let ItemEnum::Module(m) = &self.item(module)?.inner else {
            return Err(format!("item {} is not a module", module.0));
        };
        let mut bound = Vec::new();
        let mut globs = Vec::new();
        for id in &m.items {
            let item = self.item(id)?;
            match &item.inner {
                ItemEnum::Use(u) if u.is_glob => globs.push(u),
                ItemEnum::Use(u) => match self.local(u.id) {
                    Some(target) => bound.push((
                        u.name.clone(),
                        kind_name(target.inner.item_kind()),
                        Binding::Local(target),
                    )),
                    None => bound.push((u.name.clone(), "use", Binding::External(u))),
                },
                ItemEnum::Impl(_) | ItemEnum::Primitive(_) => {}
                _ => {
                    if let Some(name) = &item.name {
                        bound.push((
                            name.clone(),
                            kind_name(item.inner.item_kind()),
                            Binding::Local(item),
                        ));
                    }
                }
            }
        }
        globs.sort_by(|a, b| a.source.cmp(&b.source));
        for glob in globs {
            match self.local(glob.id) {
                Some(target) if matches!(target.inner, ItemEnum::Module(_)) => {
                    if !globbed.insert(target.id) {
                        continue;
                    }
                    for (name, kind, binding) in self.bindings(&target.id, globbed)? {
                        if !bound.iter().any(|(n, k, _)| *n == name && *k == kind) {
                            bound.push((name, kind, binding));
                        }
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
                    Binding::External(u) => public.external.push((path.clone(), u)),
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
