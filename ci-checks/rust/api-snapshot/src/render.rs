//! Turns one crate's rustdoc JSON into entries: one per item at each public path.

use crate::signature::Printer;
use rustdoc_types::{
    Crate, Id, Item, ItemEnum, ItemKind, MacroKind, StructKind, Variant, VariantKind, Visibility,
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

/// rustdoc's own name for each kind, as its JSON spells it.
pub fn kind_name(kind: ItemKind) -> &'static str {
    match kind {
        ItemKind::Module => "module",
        ItemKind::ExternCrate => "extern_crate",
        ItemKind::Use => "use",
        ItemKind::Struct => "struct",
        ItemKind::StructField => "struct_field",
        ItemKind::Union => "union",
        ItemKind::Enum => "enum",
        ItemKind::Variant => "variant",
        ItemKind::Function => "function",
        ItemKind::TypeAlias => "type_alias",
        ItemKind::Constant => "constant",
        ItemKind::Trait => "trait",
        ItemKind::TraitAlias => "trait_alias",
        ItemKind::Impl => "impl",
        ItemKind::Static => "static",
        ItemKind::ExternType => "extern_type",
        ItemKind::Macro => "macro",
        ItemKind::ProcAttribute => "proc_attribute",
        ItemKind::ProcDerive => "proc_derive",
        ItemKind::AssocConst => "assoc_const",
        ItemKind::AssocType => "assoc_type",
        ItemKind::Primitive => "primitive",
        ItemKind::Keyword => "keyword",
        ItemKind::Attribute => "attribute",
    }
}

struct Renderer<'a> {
    unit: &'a str,
    krate: &'a Crate,
    printer: Printer<'a>,
    entries: Vec<Entry>,
    /// Where each item was first emitted, for an impl's parent.
    paths: HashMap<Id, String>,
    /// Impls of the emitted types and traits, in walk order, each once.
    impls: Vec<Id>,
    seen_impls: HashSet<Id>,
}

pub fn entries(unit: &str, krate: &Crate) -> Result<Vec<Entry>, String> {
    let mut renderer = Renderer {
        unit,
        krate,
        printer: Printer {
            krate,
            public: HashMap::new(),
        },
        entries: Vec::new(),
        paths: HashMap::new(),
        impls: Vec::new(),
        seen_impls: HashSet::new(),
    };
    renderer.module(&krate.root, &[])?;
    renderer.render_impls()?;
    Ok(renderer.entries)
}

impl<'a> Renderer<'a> {
    fn item(&self, id: &Id) -> Result<&'a Item, String> {
        self.krate
            .index
            .get(id)
            .ok_or_else(|| format!("rustdoc JSON has no item {}", id.0))
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

    fn module(&mut self, id: &Id, path: &[String]) -> Result<(), String> {
        let ItemEnum::Module(module) = &self.item(id)?.inner else {
            return Err(format!("item {} is not a module", id.0));
        };
        for child in &module.items {
            let item = self.item(child)?;
            let Some(name) = item.name.clone() else {
                continue;
            };
            if matches!(
                item.inner,
                ItemEnum::Use(_) | ItemEnum::Impl(_) | ItemEnum::Primitive(_)
            ) {
                continue;
            }
            let signature = self.signature(item, &name)?;
            self.push(
                kind_name(item.inner.item_kind()),
                &path.join("::"),
                &name,
                signature,
            );
            let mut inner = path.to_vec();
            inner.push(name);
            self.children(item, &inner)?;
        }
        Ok(())
    }

    /// What hangs under an item: a module's items, a trait's items, and the impls of types and traits.
    fn children(&mut self, item: &'a Item, path: &[String]) -> Result<(), String> {
        self.paths.entry(item.id).or_insert_with(|| path.join("::"));
        let impls = match &item.inner {
            ItemEnum::Module(_) => return self.module(&item.id, path),
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
                rustdoc_types::Type::ResolvedPath(path) => {
                    self.paths.get(&path.id).cloned().unwrap_or_default()
                }
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
        let vis = if item.visibility == Visibility::Public {
            "pub "
        } else {
            ""
        };
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
        let vis = if field.visibility == Visibility::Public {
            "pub "
        } else {
            ""
        };
        Ok((
            field.name.clone().unwrap_or_default(),
            format!("{vis}{}", self.printer.ty(ty)),
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
