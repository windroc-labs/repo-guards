//! Turns one crate's rustdoc JSON into entries: one per item at each public path.

use crate::signature::Printer;
use rustdoc_types::{
    Crate, Id, Item, ItemEnum, ItemKind, MacroKind, StructKind, Variant, VariantKind, Visibility,
};
use serde::Serialize;
use std::collections::HashMap;

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
    };
    renderer.module(&krate.root, &[])?;
    Ok(renderer.entries)
}

impl<'a> Renderer<'a> {
    fn item(&self, id: &Id) -> Result<&'a Item, String> {
        self.krate
            .index
            .get(id)
            .ok_or_else(|| format!("rustdoc JSON has no item {}", id.0))
    }

    fn push(&mut self, kind: &str, parent: &[String], name: &str, signature: String) {
        self.entries.push(Entry {
            unit: self.unit.to_owned(),
            kind: kind.to_owned(),
            parent: parent.join("::"),
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
            self.push(kind_name(item.inner.item_kind()), path, &name, signature);
            if matches!(item.inner, ItemEnum::Module(_)) {
                let mut inner = path.to_vec();
                inner.push(name);
                self.module(child, &inner)?;
            }
        }
        Ok(())
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
