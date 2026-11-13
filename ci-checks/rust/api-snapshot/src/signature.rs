//! Renders rustdoc types, generics and items as Rust-like text. Paths are canonical, never as written,
//! so rewriting a `use` or moving code between files leaves signatures unchanged.

use rustdoc_types::{
    Abi, AssocItemConstraint, AssocItemConstraintKind, Crate, FunctionHeader, FunctionPointer,
    FunctionSignature, GenericArg, GenericArgs, GenericBound, GenericParamDef, GenericParamDefKind,
    Generics, Id, Path, PolyTrait, PreciseCapturingArg, Term, TraitBoundModifier, Type,
    WherePredicate,
};
use std::collections::HashMap;

pub struct Printer<'a> {
    pub krate: &'a Crate,
    /// Full paths (crate name first) of the crate's items reachable through public paths.
    pub public: HashMap<Id, String>,
}

fn join<T>(items: &[T], separator: &str, mut render: impl FnMut(&T) -> String) -> String {
    items
        .iter()
        .map(&mut render)
        .collect::<Vec<_>>()
        .join(separator)
}

impl Printer<'_> {
    pub fn item_path(&self, id: &Id, written: &str) -> String {
        if let Some(path) = self.public.get(id) {
            return path.clone();
        }
        match self.krate.paths.get(id) {
            Some(summary) => summary.path.join("::"),
            None => written.to_owned(),
        }
    }

    pub fn path(&self, path: &Path) -> String {
        let args = path
            .args
            .as_deref()
            .map(|a| self.args(a))
            .unwrap_or_default();
        format!("{}{args}", self.item_path(&path.id, &path.path))
    }

    fn args(&self, args: &GenericArgs) -> String {
        match args {
            GenericArgs::AngleBracketed { args, constraints } => {
                let mut parts: Vec<String> = args.iter().map(|a| self.arg(a)).collect();
                parts.extend(constraints.iter().map(|c| self.constraint(c)));
                if parts.is_empty() {
                    String::new()
                } else {
                    format!("<{}>", parts.join(", "))
                }
            }
            GenericArgs::Parenthesized { inputs, output } => {
                format!(
                    "({}){}",
                    join(inputs, ", ", |t| self.ty(t)),
                    self.ret(output.as_ref())
                )
            }
            GenericArgs::ReturnTypeNotation => "(..)".to_owned(),
        }
    }

    fn arg(&self, arg: &GenericArg) -> String {
        match arg {
            GenericArg::Lifetime(lifetime) => lifetime.clone(),
            GenericArg::Type(ty) => self.ty(ty),
            GenericArg::Const(constant) => constant.expr.clone(),
            GenericArg::Infer => "_".to_owned(),
        }
    }

    fn constraint(&self, constraint: &AssocItemConstraint) -> String {
        let args = constraint
            .args
            .as_deref()
            .map(|a| self.args(a))
            .unwrap_or_default();
        match &constraint.binding {
            AssocItemConstraintKind::Equality(term) => {
                format!("{}{args} = {}", constraint.name, self.term(term))
            }
            AssocItemConstraintKind::Constraint(bounds) => {
                format!("{}{args}: {}", constraint.name, self.bounds(bounds))
            }
        }
    }

    fn term(&self, term: &Term) -> String {
        match term {
            Term::Type(ty) => self.ty(ty),
            Term::Constant(constant) => constant.expr.clone(),
        }
    }

    fn ret(&self, output: Option<&Type>) -> String {
        output
            .map(|t| format!(" -> {}", self.ty(t)))
            .unwrap_or_default()
    }

    pub fn ty(&self, ty: &Type) -> String {
        match ty {
            Type::ResolvedPath(path) => self.path(path),
            Type::DynTrait(dyn_trait) => {
                let mut parts: Vec<String> = dyn_trait
                    .traits
                    .iter()
                    .map(|t| self.poly_trait(t))
                    .collect();
                parts.extend(dyn_trait.lifetime.clone());
                format!("dyn {}", parts.join(" + "))
            }
            Type::Generic(name) | Type::Primitive(name) => name.clone(),
            Type::FunctionPointer(pointer) => self.function_pointer(pointer),
            Type::Tuple(types) if types.len() == 1 => format!("({},)", self.ty(&types[0])),
            Type::Tuple(types) => format!("({})", join(types, ", ", |t| self.ty(t))),
            Type::Slice(ty) => format!("[{}]", self.ty(ty)),
            Type::Array { type_, len } => format!("[{}; {len}]", self.ty(type_)),
            Type::Pat { type_, .. } => self.ty(type_),
            Type::ImplTrait(bounds) => format!("impl {}", self.bounds(bounds)),
            Type::Infer => "_".to_owned(),
            Type::RawPointer { is_mutable, type_ } => {
                format!(
                    "*{} {}",
                    if *is_mutable { "mut" } else { "const" },
                    self.ty(type_)
                )
            }
            Type::BorrowedRef {
                lifetime,
                is_mutable,
                type_,
            } => {
                format!(
                    "&{}{}{}",
                    lifetime
                        .as_ref()
                        .map(|l| format!("{l} "))
                        .unwrap_or_default(),
                    if *is_mutable { "mut " } else { "" },
                    self.ty(type_)
                )
            }
            Type::QualifiedPath {
                name,
                args,
                self_type,
                trait_,
            } => {
                let args = args.as_deref().map(|a| self.args(a)).unwrap_or_default();
                match trait_ {
                    Some(trait_) => format!(
                        "<{} as {}>::{name}{args}",
                        self.ty(self_type),
                        self.path(trait_)
                    ),
                    None => format!("{}::{name}{args}", self.ty(self_type)),
                }
            }
        }
    }

    fn poly_trait(&self, poly: &PolyTrait) -> String {
        format!(
            "{}{}",
            self.for_params(&poly.generic_params),
            self.path(&poly.trait_)
        )
    }

    fn for_params(&self, params: &[GenericParamDef]) -> String {
        if params.is_empty() {
            String::new()
        } else {
            format!("for<{}> ", join(params, ", ", |p| self.param(p)))
        }
    }

    fn function_pointer(&self, pointer: &FunctionPointer) -> String {
        format!(
            "{}{}fn({}){}",
            self.for_params(&pointer.generic_params),
            header(&pointer.header),
            self.inputs(&pointer.sig),
            self.ret(pointer.sig.output.as_ref())
        )
    }

    pub fn bounds(&self, bounds: &[GenericBound]) -> String {
        join(bounds, " + ", |b| self.bound(b))
    }

    fn bound(&self, bound: &GenericBound) -> String {
        match bound {
            GenericBound::TraitBound {
                trait_,
                generic_params,
                modifier,
            } => {
                let modifier = match modifier {
                    TraitBoundModifier::None => "",
                    TraitBoundModifier::Maybe => "?",
                    TraitBoundModifier::MaybeConst => "~const ",
                };
                format!(
                    "{}{modifier}{}",
                    self.for_params(generic_params),
                    self.path(trait_)
                )
            }
            GenericBound::Outlives(lifetime) => lifetime.clone(),
            GenericBound::Use(args) => format!(
                "use<{}>",
                join(args, ", ", |a| match a {
                    PreciseCapturingArg::Lifetime(name) | PreciseCapturingArg::Param(name) =>
                        name.clone(),
                })
            ),
        }
    }

    fn param(&self, param: &GenericParamDef) -> String {
        match &param.kind {
            GenericParamDefKind::Lifetime { outlives } if outlives.is_empty() => param.name.clone(),
            GenericParamDefKind::Lifetime { outlives } => {
                format!("{}: {}", param.name, outlives.join(" + "))
            }
            GenericParamDefKind::Type {
                bounds, default, ..
            } => {
                let bounds = if bounds.is_empty() {
                    String::new()
                } else {
                    format!(": {}", self.bounds(bounds))
                };
                let default = default
                    .as_ref()
                    .map(|d| format!(" = {}", self.ty(d)))
                    .unwrap_or_default();
                format!("{}{bounds}{default}", param.name)
            }
            GenericParamDefKind::Const { type_, default } => {
                let default = default
                    .as_ref()
                    .map(|d| format!(" = {d}"))
                    .unwrap_or_default();
                format!("const {}: {}{default}", param.name, self.ty(type_))
            }
        }
    }

    /// `<…>` for the declared parameters; an `impl Trait` argument's synthetic parameter is not declared.
    pub fn params(&self, generics: &Generics) -> String {
        let declared: Vec<&GenericParamDef> = generics
            .params
            .iter()
            .filter(|p| {
                !matches!(
                    p.kind,
                    GenericParamDefKind::Type {
                        is_synthetic: true,
                        ..
                    }
                )
            })
            .collect();
        if declared.is_empty() {
            String::new()
        } else {
            format!("<{}>", join(&declared, ", ", |p| self.param(p)))
        }
    }

    pub fn where_clause(&self, generics: &Generics) -> String {
        if generics.where_predicates.is_empty() {
            return String::new();
        }
        format!(
            " where {}",
            join(&generics.where_predicates, ", ", |p| self.predicate(p))
        )
    }

    fn predicate(&self, predicate: &WherePredicate) -> String {
        match predicate {
            WherePredicate::BoundPredicate {
                type_,
                bounds,
                generic_params,
            } => {
                format!(
                    "{}{}: {}",
                    self.for_params(generic_params),
                    self.ty(type_),
                    self.bounds(bounds)
                )
            }
            WherePredicate::LifetimePredicate { lifetime, outlives } => {
                format!("{lifetime}: {}", outlives.join(" + "))
            }
            WherePredicate::EqPredicate { lhs, rhs } => {
                format!("{} = {}", self.ty(lhs), self.term(rhs))
            }
        }
    }

    /// Parameter types only: a caller cannot depend on parameter names, so renaming one is no API change.
    fn inputs(&self, sig: &FunctionSignature) -> String {
        let mut inputs: Vec<String> = sig
            .inputs
            .iter()
            .map(|(name, ty)| self.input(name, ty))
            .collect();
        if sig.is_c_variadic {
            inputs.push("...".to_owned());
        }
        inputs.join(", ")
    }

    fn input(&self, name: &str, ty: &Type) -> String {
        if name != "self" {
            return self.ty(ty);
        }
        match ty {
            Type::Generic(s) if s == "Self" => "self".to_owned(),
            Type::BorrowedRef {
                lifetime,
                is_mutable,
                type_,
            } if matches!(&**type_, Type::Generic(s) if s == "Self") => {
                format!(
                    "&{}{}self",
                    lifetime
                        .as_ref()
                        .map(|l| format!("{l} "))
                        .unwrap_or_default(),
                    if *is_mutable { "mut " } else { "" }
                )
            }
            _ => format!("self: {}", self.ty(ty)),
        }
    }

    pub fn function(
        &self,
        name: &str,
        header_: &FunctionHeader,
        generics: &Generics,
        sig: &FunctionSignature,
    ) -> String {
        format!(
            "{}fn {name}{}({}){}{}",
            header(header_),
            self.params(generics),
            self.inputs(sig),
            self.ret(sig.output.as_ref()),
            self.where_clause(generics)
        )
    }
}

fn header(header: &FunctionHeader) -> String {
    let mut text = String::new();
    for (on, word) in [
        (header.is_const, "const "),
        (header.is_async, "async "),
        (header.is_unsafe, "unsafe "),
    ] {
        if on {
            text.push_str(word);
        }
    }
    if let Some(abi) = abi(&header.abi) {
        text.push_str(&format!("extern \"{abi}\" "));
    }
    text
}

fn abi(abi: &Abi) -> Option<String> {
    let (name, unwind) = match abi {
        Abi::Rust => return None,
        Abi::C { unwind } => ("C", unwind),
        Abi::Cdecl { unwind } => ("cdecl", unwind),
        Abi::Stdcall { unwind } => ("stdcall", unwind),
        Abi::Fastcall { unwind } => ("fastcall", unwind),
        Abi::Aapcs { unwind } => ("aapcs", unwind),
        Abi::Win64 { unwind } => ("win64", unwind),
        Abi::SysV64 { unwind } => ("sysv64", unwind),
        Abi::System { unwind } => ("system", unwind),
        Abi::Other(other) => return Some(other.trim_matches('"').to_owned()),
    };
    Some(if *unwind {
        format!("{name}-unwind")
    } else {
        name.to_owned()
    })
}
