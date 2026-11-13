//! Turns one crate's rustdoc JSON into entries: one per item at each public path.

use crate::rustdoc::{item, kind_name};
use crate::signature::Printer;
use crate::walk::{Public, walk};
use rustdoc_types::{
    Crate, Id, Item, ItemEnum, MacroKind, StructKind, Type, Use, Variant, VariantKind, Visibility,
};
use serde::Serialize;
use std::collections::{HashMap, HashSet};

/// Auto traits whose synthetic impls are API: removing one breaks callers. Unstable ones (`Freeze`,
/// `UnsafeUnpin`) belong to the toolchain, not the crate.
const AUTO_TRAITS: [&str; 5] = ["Send", "Sync", "Unpin", "UnwindSafe", "RefUnwindSafe"];

#[derive(Serialize, Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub unit: String,
    pub kind: String,
    pub parent: String,
    pub name: String,
    pub signature: String,
}

struct Renderer<'a> {
    unit: &'a str,
    krate: &'a Crate,
    printer: Printer<'a>,
    entries: Vec<Entry>,
    /// Each reachable item's canonical path, segments after the crate name.
    canonical: HashMap<Id, Vec<String>>,
    /// Impls of the emitted types and traits, in walk order, each once.
    impls: Vec<Id>,
    seen_impls: HashSet<Id>,
}

pub fn entries(unit: &str, krate: &Crate) -> Result<Vec<Entry>, String> {
    let Public {
        items,
        external,
        canonical,
    } = walk(krate)?;
    let crate_name = item(krate, &krate.root)?.name.clone().unwrap_or_default();
    let mut renderer = Renderer {
        unit,
        krate,
        printer: Printer::new(krate, &crate_name, &canonical),
        entries: Vec::new(),
        canonical,
        impls: Vec::new(),
        seen_impls: HashSet::new(),
    };
    for (path, item) in &items {
        let (name, parent) = path.split_last().expect("a public path has a name");
        let signature = renderer.signature(item, name)?;
        renderer.push(
            kind_name(item.inner.item_kind()),
            &parent.join("::"),
            name,
            signature,
        );
        if renderer.canonical.get(&item.id) == Some(path) {
            renderer.children(item, path)?;
        }
    }
    for (module, re_export, kind) in &external {
        let (name, signature) = renderer.external(re_export);
        renderer.push(kind, &module.join("::"), &name, signature);
    }
    renderer.render_impls()?;
    Ok(renderer.entries)
}

fn vis(item: &Item) -> &'static str {
    if item.visibility == Visibility::Public {
        "pub "
    } else {
        ""
    }
}

impl<'a> Renderer<'a> {
    fn item(&self, id: &Id) -> Result<&'a Item, String> {
        item(self.krate, id)
    }

    fn push(&mut self, kind: &str, parent: &str, name: &str, signature: String) {
        self.entries.push(Entry {
            unit: self.unit.to_owned(),
            kind: kind.to_owned(),
            parent: parent.to_owned(),
            name: name.to_owned(),
            signature,
        });
    }

    /// Another crate's item re-exported here, named by rustdoc's path for it; a glob by its source.
    fn external(&self, re_export: &Use) -> (String, String) {
        if re_export.is_glob {
            let name = format!("{}::*", re_export.source);
            return (name.clone(), format!("pub use {name}"));
        }
        let path = match re_export.id.and_then(|id| self.krate.paths.get(&id)) {
            Some(summary) => summary.path.join("::"),
            None => re_export.source.clone(),
        };
        let rename = if path.rsplit("::").next() == Some(re_export.name.as_str()) {
            String::new()
        } else {
            format!(" as {}", re_export.name)
        };
        (re_export.name.clone(), format!("pub use {path}{rename}"))
    }

    /// What hangs under an item at its canonical path: a trait's items, and the impls of types and traits.
    fn children(&mut self, item: &'a Item, path: &[String]) -> Result<(), String> {
        let impls = match &item.inner {
            ItemEnum::Struct(s) => &s.impls,
            ItemEnum::Enum(e) => &e.impls,
            ItemEnum::Union(u) => &u.impls,
            ItemEnum::Trait(t) => {
                for id in &t.items {
                    let member = self.item(id)?;
                    let name = member.name.clone().unwrap_or_default();
                    let signature = self.member_signature(member, &name, true)?;
                    self.push(
                        kind_name(member.inner.item_kind()),
                        &path.join("::"),
                        &name,
                        signature,
                    );
                }
                &t.implementations
            }
            _ => return Ok(()),
        };
        for id in impls {
            if self.seen_impls.insert(*id) {
                self.impls.push(*id);
            }
        }
        Ok(())
    }

    /// A trait's or impl's function, constant or type; `{ ... }` marks a trait method's default body.
    fn member_signature(
        &self,
        member: &Item,
        name: &str,
        in_trait: bool,
    ) -> Result<String, String> {
        let signature = self.signature(member, name)?;
        Ok(match &member.inner {
            ItemEnum::Function(f) if in_trait && f.has_body => format!("{signature} {{ ... }}"),
            _ => signature,
        })
    }

    /// Blanket impls come from other crates' generic impls, not this crate's types; a synthetic impl is
    /// kept only for a stable auto trait.
    fn kept(&self, imp: &rustdoc_types::Impl) -> bool {
        if imp.blanket_impl.is_some() {
            return false;
        }
        if !imp.is_synthetic {
            return true;
        }
        let Some(trait_) = &imp.trait_ else {
            return false;
        };
        let name = match self.krate.paths.get(&trait_.id) {
            Some(summary) => summary.path.last().cloned().unwrap_or_default(),
            None => trait_
                .path
                .rsplit("::")
                .next()
                .unwrap_or_default()
                .to_owned(),
        };
        AUTO_TRAITS.contains(&name.as_str())
    }

    fn render_impls(&mut self) -> Result<(), String> {
        let mut headers = HashSet::new();
        for id in std::mem::take(&mut self.impls) {
            let ItemEnum::Impl(imp) = &self.item(&id)?.inner else {
                return Err(format!("item {} is not an impl", id.0));
            };
            if !self.kept(imp) {
                continue;
            }
            let header = self.impl_header(imp);
            let parent = match &imp.for_ {
                Type::ResolvedPath(path) => self
                    .canonical
                    .get(&path.id)
                    .map(|p| p.join("::"))
                    .unwrap_or_default(),
                _ => String::new(),
            };
            // Two inherent impl blocks of one type print the same header; they are one entry.
            if headers.insert(header.clone()) {
                self.push("impl", &parent, &header, header.clone());
            }
            for member_id in &imp.items {
                let member = self.item(member_id)?;
                let name = member.name.clone().unwrap_or_default();
                let signature = self.member_signature(member, &name, false)?;
                self.push(
                    kind_name(member.inner.item_kind()),
                    &header,
                    &name,
                    signature,
                );
            }
        }
        Ok(())
    }

    fn impl_header(&self, imp: &rustdoc_types::Impl) -> String {
        let p = &self.printer;
        let head = format!(
            "{}impl{}",
            if imp.is_unsafe { "unsafe " } else { "" },
            p.params(&imp.generics)
        );
        let self_type = p.ty(&imp.for_);
        // A synthetic auto-trait impl's where clause names the types of private fields, so it is left out.
        let clause = if imp.is_synthetic {
            String::new()
        } else {
            p.where_clause(&imp.generics)
        };
        match &imp.trait_ {
            Some(trait_) => {
                let negative = if imp.is_negative { "!" } else { "" };
                format!(
                    "{head} {negative}{} for {self_type}{clause}",
                    p.path(trait_)
                )
            }
            None => format!("{head} {self_type}{clause}"),
        }
    }

    fn signature(&self, item: &Item, name: &str) -> Result<String, String> {
        let p = &self.printer;
        let vis = vis(item);
        Ok(match &item.inner {
            ItemEnum::Module(_) => format!("{vis}mod {name}"),
            ItemEnum::ExternCrate {
                name: krate,
                rename,
            } => {
                format!(
                    "{vis}extern crate {krate}{}",
                    rename
                        .as_ref()
                        .map(|r| format!(" as {r}"))
                        .unwrap_or_default()
                )
            }
            ItemEnum::Use(_)
            | ItemEnum::Impl(_)
            | ItemEnum::Primitive(_)
            | ItemEnum::StructField(_)
            | ItemEnum::Variant(_) => {
                return Err(format!(
                    "item {} ({name}) has no signature of its own",
                    item.id.0
                ));
            }
            ItemEnum::Function(f) => {
                format!("{vis}{}", p.function(name, &f.header, &f.generics, &f.sig))
            }
            ItemEnum::Struct(s) => {
                let head = format!("{vis}struct {name}{}", p.params(&s.generics));
                let clause = p.where_clause(&s.generics);
                match &s.kind {
                    StructKind::Unit => format!("{head}{clause}"),
                    StructKind::Tuple(fields) => {
                        format!("{head}({}){clause}", self.tuple_fields(fields)?)
                    }
                    StructKind::Plain {
                        fields,
                        has_stripped_fields,
                    } => {
                        format!(
                            "{head}{clause} {}",
                            self.named_fields(fields, *has_stripped_fields)?
                        )
                    }
                }
            }
            ItemEnum::Union(u) => format!(
                "{vis}union {name}{}{} {}",
                p.params(&u.generics),
                p.where_clause(&u.generics),
                self.named_fields(&u.fields, u.has_stripped_fields)?
            ),
            ItemEnum::Enum(e) => {
                let mut variants = Vec::new();
                for id in &e.variants {
                    let variant = self.item(id)?;
                    let ItemEnum::Variant(v) = &variant.inner else {
                        return Err(format!("item {} is not a variant", id.0));
                    };
                    variants.push(self.variant(variant.name.as_deref().unwrap_or("_"), v)?);
                }
                if e.has_stripped_variants {
                    variants.push("/* private variants */".to_owned());
                }
                let body = if variants.is_empty() {
                    "{}".to_owned()
                } else {
                    format!("{{ {} }}", variants.join(", "))
                };
                format!(
                    "{vis}enum {name}{}{} {body}",
                    p.params(&e.generics),
                    p.where_clause(&e.generics)
                )
            }
            ItemEnum::Trait(t) => {
                let supertraits = if t.bounds.is_empty() {
                    String::new()
                } else {
                    format!(": {}", p.bounds(&t.bounds))
                };
                format!(
                    "{vis}{}{}trait {name}{}{supertraits}{}",
                    if t.is_unsafe { "unsafe " } else { "" },
                    if t.is_auto { "auto " } else { "" },
                    p.params(&t.generics),
                    p.where_clause(&t.generics)
                )
            }
            ItemEnum::TraitAlias(t) => {
                format!(
                    "{vis}trait {name}{} = {}{}",
                    p.params(&t.generics),
                    p.bounds(&t.params),
                    p.where_clause(&t.generics)
                )
            }
            ItemEnum::TypeAlias(t) => {
                format!(
                    "{vis}type {name}{} = {}{}",
                    p.params(&t.generics),
                    p.ty(&t.type_),
                    p.where_clause(&t.generics)
                )
            }
            ItemEnum::Constant { type_, .. } => format!("{vis}const {name}: {}", p.ty(type_)),
            ItemEnum::Static(s) => format!(
                "{vis}{}static {}{name}: {}",
                if s.is_unsafe { "unsafe " } else { "" },
                if s.is_mutable { "mut " } else { "" },
                p.ty(&s.type_)
            ),
            ItemEnum::ExternType => format!("{vis}type {name}"),
            ItemEnum::Macro(text) => text.split_whitespace().collect::<Vec<_>>().join(" "),
            ItemEnum::ProcMacro(m) => match m.kind {
                MacroKind::Bang => format!("#[proc_macro] {name}"),
                MacroKind::Attr => format!("#[proc_macro_attribute] {name}"),
                MacroKind::Derive if m.helpers.is_empty() => {
                    format!("#[proc_macro_derive({name})]")
                }
                MacroKind::Derive => format!(
                    "#[proc_macro_derive({name}, attributes({}))]",
                    m.helpers.join(", ")
                ),
            },
            ItemEnum::AssocConst { type_, value, .. } => {
                format!(
                    "const {name}: {}{}",
                    p.ty(type_),
                    value
                        .as_ref()
                        .map(|v| format!(" = {v}"))
                        .unwrap_or_default()
                )
            }
            ItemEnum::AssocType {
                generics,
                bounds,
                type_,
                ..
            } => format!(
                "type {name}{}{}{}{}",
                p.params(generics),
                if bounds.is_empty() {
                    String::new()
                } else {
                    format!(": {}", p.bounds(bounds))
                },
                type_
                    .as_ref()
                    .map(|t| format!(" = {}", p.ty(t)))
                    .unwrap_or_default(),
                p.where_clause(generics)
            ),
        })
    }

    fn field(&self, id: &Id) -> Result<(String, String), String> {
        let field = self.item(id)?;
        let ItemEnum::StructField(ty) = &field.inner else {
            return Err(format!("item {} is not a field", id.0));
        };
        Ok((
            field.name.clone().unwrap_or_default(),
            format!("{}{}", vis(field), self.printer.ty(ty)),
        ))
    }

    /// `_` stands for a field rustdoc stripped as private.
    fn tuple_fields(&self, fields: &[Option<Id>]) -> Result<String, String> {
        let mut parts = Vec::new();
        for field in fields {
            parts.push(match field {
                Some(id) => self.field(id)?.1,
                None => "_".to_owned(),
            });
        }
        Ok(parts.join(", "))
    }

    fn named_fields(&self, fields: &[Id], stripped: bool) -> Result<String, String> {
        let mut parts = Vec::new();
        for id in fields {
            let (name, ty) = self.field(id)?;
            let (vis, ty) = ty.split_at(if ty.starts_with("pub ") { 4 } else { 0 });
            parts.push(format!("{vis}{name}: {ty}"));
        }
        if stripped {
            parts.push("/* private fields */".to_owned());
        }
        Ok(if parts.is_empty() {
            "{}".to_owned()
        } else {
            format!("{{ {} }}", parts.join(", "))
        })
    }

    fn variant(&self, name: &str, variant: &Variant) -> Result<String, String> {
        let body = match &variant.kind {
            VariantKind::Plain => String::new(),
            VariantKind::Tuple(fields) => format!("({})", self.tuple_fields(fields)?),
            VariantKind::Struct {
                fields,
                has_stripped_fields,
            } => format!(" {}", self.named_fields(fields, *has_stripped_fields)?),
        };
        // The evaluated value, not the expression's text, so reformatting it changes nothing.
        let discriminant = variant
            .discriminant
            .as_ref()
            .map(|d| format!(" = {}", d.value))
            .unwrap_or_default();
        Ok(format!("{name}{body}{discriminant}"))
    }
}
