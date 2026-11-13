use std::fs;
use std::io::{self, Read, Write};
use std::ops::Range;
use std::path::Path;
use std::process::ExitCode;

use proc_macro2::{Delimiter, LexError, Span, TokenStream, TokenTree};
use syn::spanned::Spanned;
use syn::visit::{self, Visit};
use syn::{Expr, Lit, Meta, Type};

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct Occurrence {
    tool: String,
    rule: String,
    function: String,
}

fn occurrence(tool: &str, rule: impl Into<String>, function: &str) -> Occurrence {
    Occurrence {
        tool: tool.into(),
        rule: rule.into(),
        function: function.into(),
    }
}

fn line_of(bytes: &[u8], offset: usize) -> usize {
    bytes[..offset].iter().filter(|&&b| b == b'\n').count() + 1
}

fn block_comment_len(s: &str) -> Option<usize> {
    let bytes = s.as_bytes();
    let (mut i, mut depth) = (0, 0);
    while i + 1 < bytes.len() {
        match (bytes[i], bytes[i + 1]) {
            (b'/', b'*') => (depth, i) = (depth + 1, i + 2),
            (b'*', b'/') if depth == 1 => return Some(i + 2),
            (b'*', b'/') => (depth, i) = (depth - 1, i + 2),
            _ => i += 1,
        }
    }
    None
}

fn is_doc_comment(s: &str) -> bool {
    (s.starts_with("///") && !s.starts_with("////"))
        || s.starts_with("//!")
        || (s.starts_with("/**") && !s.starts_with("/***") && !s.starts_with("/**/"))
        || s.starts_with("/*!")
}

fn skip_trivia(mut s: &str) -> &str {
    loop {
        s = s.trim_start_matches(|c: char| c.is_whitespace() || c == '\u{200e}' || c == '\u{200f}');
        if is_doc_comment(s) {
            return s;
        } else if s.starts_with("//") {
            s = s.find('\n').map_or("", |i| &s[i + 1..]);
        } else if s.starts_with("/*") {
            match block_comment_len(s) {
                Some(len) => s = &s[len..],
                None => return s,
            }
        } else {
            return s;
        }
    }
}

fn prepare(text: &str) -> String {
    let mut text = text.strip_prefix('\u{feff}').unwrap_or(text).to_string();
    if text.starts_with("#!") && !skip_trivia(&text[2..]).starts_with('[') {
        let end = text.find('\n').unwrap_or(text.len());
        text.replace_range(..end, &" ".repeat(end));
    }
    text
}

fn lex_error(error: &LexError) -> String {
    format!("line {}: {error}", error.span().start().line)
}

fn parse_error(text: &str, error: &syn::Error) -> String {
    let span = error.span();
    let line = if span.byte_range().is_empty() {
        line_of(text.as_bytes(), text.len())
    } else {
        span.start().line
    };
    format!("line {line}: {error}")
}

fn path_string(path: &syn::Path) -> String {
    let segments: Vec<String> = path.segments.iter().map(|s| s.ident.to_string()).collect();
    segments.join("::")
}

fn split_commas(stream: TokenStream) -> Vec<TokenStream> {
    let mut parts = vec![TokenStream::new()];
    for token in stream {
        match &token {
            TokenTree::Punct(p) if p.as_char() == ',' => parts.push(TokenStream::new()),
            _ => parts.last_mut().unwrap().extend([token]),
        }
    }
    parts
}

fn list_metas(meta: &Meta) -> Vec<Meta> {
    match meta {
        Meta::List(list) => split_commas(list.tokens.clone())
            .into_iter()
            .filter_map(|part| syn::parse2::<Meta>(part).ok())
            .collect(),
        _ => Vec::new(),
    }
}

fn single_ident(meta: &Meta, word: &str) -> bool {
    let Meta::List(list) = meta else {
        return false;
    };
    let mut tokens = list.tokens.clone().into_iter();
    matches!((tokens.next(), tokens.next()), (Some(TokenTree::Ident(i)), None) if i == word)
}

fn is_cfg_test(attribute: &syn::Attribute) -> bool {
    attribute.meta.path().is_ident("cfg") && single_ident(&attribute.meta, "test")
}

fn type_name(ty: &Type) -> Option<String> {
    match ty {
        Type::Reference(r) => type_name(&r.elem),
        Type::Paren(p) => type_name(&p.elem),
        Type::Group(g) => type_name(&g.elem),
        Type::Path(p) => p.path.segments.last().map(|s| s.ident.to_string()),
        _ => None,
    }
}

#[derive(Default)]
struct Visitor {
    found: Vec<Occurrence>,
    functions: Vec<(String, Range<usize>)>,
    context: Vec<String>,
    owners: Vec<Option<String>>,
}

impl Visitor {
    fn add(&mut self, tool: &str, rule: impl Into<String>) {
        let function = self.context.last().map_or("", String::as_str);
        self.found.push(occurrence(tool, rule, function));
    }

    fn method_name(&self, ident: &syn::Ident) -> String {
        match self.owners.last() {
            Some(Some(owner)) => format!("{owner}::{ident}"),
            _ => ident.to_string(),
        }
    }

    fn enter(&mut self, name: String, span: Span, visit: impl FnOnce(&mut Self)) {
        self.functions.push((name.clone(), span.byte_range()));
        self.context.push(name);
        visit(self);
        self.context.pop();
    }

    fn meta(&mut self, meta: &Meta, test_module: bool) {
        let path = path_string(meta.path());
        match (path.as_str(), meta) {
            ("cfg_attr", Meta::List(list)) => {
                for part in split_commas(list.tokens.clone()).into_iter().skip(1) {
                    if let Ok(inner) = syn::parse2::<Meta>(part) {
                        self.meta(&inner, test_module);
                    }
                }
            }
            ("allow" | "expect", _) => {
                for lint in list_metas(meta) {
                    if let Meta::Path(lint) = lint {
                        let lint = path_string(&lint);
                        let tool = if lint.starts_with("clippy::") {
                            "clippy"
                        } else {
                            "rustc"
                        };
                        self.add(tool, lint);
                    }
                }
            }
            ("rustfmt::skip", Meta::Path(_)) => self.add("rustfmt", "skip"),
            ("coverage", _) if !test_module && single_ident(meta, "off") => {
                self.add("cargo-llvm-cov", "off");
            }
            ("ignore", Meta::Path(_) | Meta::NameValue(_)) => self.add("cargo-test", "ignore"),
            ("no_sanitize", _) => {
                for name in list_metas(meta) {
                    if let Meta::Path(name) = name {
                        self.add("sanitizer", path_string(&name));
                    }
                }
            }
            ("sanitize", _) => {
                for setting in list_metas(meta) {
                    if let Meta::NameValue(setting) = setting
                        && let Expr::Lit(value) = &setting.value
                        && let Lit::Str(value) = &value.lit
                        && value.value() == "off"
                    {
                        self.add("sanitizer", path_string(&setting.path));
                    }
                }
            }
            _ => {}
        }
    }

    fn macro_tokens(&mut self, stream: TokenStream) {
        let tokens: Vec<TokenTree> = stream.into_iter().collect();
        let mut i = 0;
        while i < tokens.len() {
            if let TokenTree::Punct(pound) = &tokens[i]
                && pound.as_char() == '#'
            {
                let bang =
                    matches!(&tokens.get(i + 1), Some(TokenTree::Punct(p)) if p.as_char() == '!');
                let at = i + 1 + usize::from(bang);
                if let Some(TokenTree::Group(group)) = tokens.get(at)
                    && group.delimiter() == Delimiter::Bracket
                    && let Ok(meta) = syn::parse2::<Meta>(group.stream())
                {
                    self.meta(&meta, false);
                    i = at + 1;
                    continue;
                }
            }
            if let TokenTree::Group(group) = &tokens[i] {
                self.macro_tokens(group.stream());
            }
            i += 1;
        }
    }
}

impl<'ast> Visit<'ast> for Visitor {
    fn visit_attribute(&mut self, attribute: &'ast syn::Attribute) {
        self.meta(&attribute.meta, false);
    }

    fn visit_macro(&mut self, mac: &'ast syn::Macro) {
        self.macro_tokens(mac.tokens.clone());
    }

    fn visit_item_fn(&mut self, node: &'ast syn::ItemFn) {
        let name = node.sig.ident.to_string();
        self.enter(name, node.span(), |v| visit::visit_item_fn(v, node));
    }

    fn visit_foreign_item_fn(&mut self, node: &'ast syn::ForeignItemFn) {
        let name = node.sig.ident.to_string();
        self.enter(name, node.span(), |v| visit::visit_foreign_item_fn(v, node));
    }

    fn visit_impl_item_fn(&mut self, node: &'ast syn::ImplItemFn) {
        let name = self.method_name(&node.sig.ident);
        self.enter(name, node.span(), |v| visit::visit_impl_item_fn(v, node));
    }

    fn visit_trait_item_fn(&mut self, node: &'ast syn::TraitItemFn) {
        let name = self.method_name(&node.sig.ident);
        self.enter(name, node.span(), |v| visit::visit_trait_item_fn(v, node));
    }

    fn visit_item_impl(&mut self, node: &'ast syn::ItemImpl) {
        self.owners.push(type_name(&node.self_ty));
        visit::visit_item_impl(self, node);
        self.owners.pop();
    }

    fn visit_item_trait(&mut self, node: &'ast syn::ItemTrait) {
        self.owners.push(Some(node.ident.to_string()));
        visit::visit_item_trait(self, node);
        self.owners.pop();
    }

    fn visit_item_mod(&mut self, node: &'ast syn::ItemMod) {
        let test_module = node.attrs.iter().any(is_cfg_test);
        for attribute in &node.attrs {
            self.meta(&attribute.meta, test_module);
        }
        for item in node.content.iter().flat_map(|(_, items)| items) {
            self.visit_item(item);
        }
    }
}

fn token_ranges(stream: TokenStream, out: &mut Vec<Range<usize>>) {
    for token in stream {
        match token {
            TokenTree::Group(group) => {
                out.push(group.span_open().byte_range());
                out.push(group.span_close().byte_range());
                token_ranges(group.stream(), out);
            }
            other => out.push(other.span().byte_range()),
        }
    }
}

struct Gap {
    range: Range<usize>,
    comments: Vec<Range<usize>>,
}

fn gaps(text: &str, mut tokens: Vec<Range<usize>>) -> Vec<Gap> {
    tokens.sort_by_key(|r| r.start);
    let mut cursor = 0;
    let mut ranges = Vec::new();
    for token in tokens
        .into_iter()
        .chain(std::iter::once(text.len()..text.len()))
    {
        if token.start > cursor {
            ranges.push(cursor..token.start);
        }
        cursor = cursor.max(token.end);
    }
    ranges
        .into_iter()
        .map(|range| {
            let mut comments = Vec::new();
            let mut i = range.start;
            while i < range.end {
                let rest = &text[i..range.end];
                let len = if rest.starts_with("//") {
                    rest.find('\n').unwrap_or(rest.len())
                } else if rest.starts_with("/*") {
                    block_comment_len(rest).unwrap_or(rest.len())
                } else {
                    i += rest.chars().next().map_or(1, char::len_utf8);
                    continue;
                };
                comments.push(i..i + len);
                i += len;
            }
            Gap { range, comments }
        })
        .collect()
}

fn leading_comments_start(text: &str, gap: &Gap, start: usize) -> usize {
    let mut start = start;
    for comment in gap.comments.iter().rev() {
        let own_line = gap.range.start == 0 || text[gap.range.start..comment.start].contains('\n');
        if !own_line || text[comment.end..start].matches('\n').count() > 1 {
            break;
        }
        start = comment.start;
    }
    start
}

fn is_generated(comment: &str) -> bool {
    const CODE: &str = "Code generated ";
    comment.contains("@generated")
        || comment.contains("Generated by the protocol buffer compiler.  DO NOT EDIT!")
        || comment.lines().any(|line| {
            line.find(CODE)
                .is_some_and(|i| line[i + CODE.len()..].contains(" DO NOT EDIT."))
        })
}

fn scan_tokens(text: &str) -> Result<Vec<Occurrence>, String> {
    let stream: TokenStream = text.parse().map_err(|e: LexError| lex_error(&e))?;
    let mut tokens = Vec::new();
    token_ranges(stream.clone(), &mut tokens);
    let file: syn::File = syn::parse2(stream).map_err(|e| parse_error(text, &e))?;
    let mut visitor = Visitor::default();
    visitor.visit_file(&file);
    let first_token = tokens.iter().map(|r| r.start).min().unwrap_or(text.len());
    let gaps = gaps(text, tokens);
    let functions: Vec<(String, Range<usize>)> = visitor
        .functions
        .iter()
        .map(|(name, range)| {
            let start = gaps
                .binary_search_by_key(&range.start, |gap| gap.range.end)
                .map_or(range.start, |i| {
                    leading_comments_start(text, &gaps[i], range.start)
                });
            (name.clone(), start..range.end)
        })
        .collect();
    let mut found = visitor.found;
    let comments = || gaps.iter().flat_map(|gap| gap.comments.iter().cloned());
    if comments().any(|c| c.end <= first_token && is_generated(&text[c])) {
        found.push(occurrence("generated", "generated", ""));
    }
    for comment in comments().filter(|c| text[c.clone()].contains("jscpd:ignore-start")) {
        let function = functions
            .iter()
            .filter(|(_, range)| range.start <= comment.start && comment.end <= range.end)
            .max_by_key(|(_, range)| range.start)
            .map_or("", |(name, _)| name.as_str());
        found.push(occurrence("jscpd", "ignore", function));
    }
    Ok(found)
}

fn scan_rust(text: &str) -> Result<Vec<Occurrence>, String> {
    let found = scan_tokens(&prepare(text));
    proc_macro2::extra::invalidate_current_thread_spans();
    found
}

fn scan_cargo(text: &str) -> Result<Vec<Occurrence>, String> {
    let table: toml::Table = text.parse().map_err(|e: toml::de::Error| {
        let line = e
            .span()
            .map_or(1, |span| line_of(text.as_bytes(), span.start));
        format!("line {line}: {}", e.message().trim())
    })?;
    let mut found = Vec::new();
    let workspace = table.get("workspace");
    let lints = [table.get("lints"), workspace.and_then(|w| w.get("lints"))];
    for groups in lints
        .into_iter()
        .flatten()
        .filter_map(toml::Value::as_table)
    {
        for (group, entries) in groups {
            for (name, value) in entries.as_table().into_iter().flatten() {
                let level = value.as_str().or_else(|| value.get("level")?.as_str());
                if level != Some("allow") {
                    continue;
                }
                match group.as_str() {
                    "rust" => found.push(occurrence("rustc", name, "")),
                    "clippy" => found.push(occurrence("clippy", format!("clippy::{name}"), "")),
                    _ => found.push(occurrence("rustc", format!("{group}::{name}"), "")),
                }
            }
        }
    }
    let ignored = table
        .get("package")
        .and_then(|p| p.get("metadata"))
        .and_then(|m| m.get("cargo-machete"))
        .and_then(|m| m.get("ignored"))
        .and_then(toml::Value::as_array);
    for name in ignored
        .into_iter()
        .flatten()
        .filter_map(toml::Value::as_str)
    {
        found.push(occurrence("cargo-machete", name, ""));
    }
    Ok(found)
}

fn json_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn scan_file(root: &Path, path: &str) -> Result<Vec<Occurrence>, String> {
    let bytes = fs::read(root.join(path)).map_err(|e| e.to_string())?;
    let text = std::str::from_utf8(&bytes)
        .map_err(|e| format!("line {}: not UTF-8", line_of(&bytes, e.valid_up_to())))?;
    if path.rsplit('/').next() == Some("Cargo.toml") {
        scan_cargo(text)
    } else {
        scan_rust(text)
    }
}

fn run(root: &Path, input: &[u8], out: &mut impl Write) -> io::Result<()> {
    for path in input.split(|&b| b == 0).filter(|p| !p.is_empty()) {
        let path = String::from_utf8_lossy(path);
        let file = json_string(&path);
        match scan_file(root, &path) {
            Ok(found) => {
                for o in found {
                    let (tool, rule, function) = (
                        json_string(&o.tool),
                        json_string(&o.rule),
                        json_string(&o.function),
                    );
                    writeln!(
                        out,
                        "{{\"tool\":{tool},\"file\":{file},\"rule\":{rule},\"function\":{function}}}"
                    )?;
                }
            }
            Err(error) => writeln!(out, "{{\"file\":{file},\"error\":{}}}", json_string(&error))?,
        }
    }
    out.flush()
}

fn main() -> ExitCode {
    let Some(root) = std::env::args_os().nth(1) else {
        eprintln!("usage: inline-exemptions <root> < NUL-separated paths");
        return ExitCode::from(2);
    };
    let mut input = Vec::new();
    let done = io::stdin()
        .read_to_end(&mut input)
        .and_then(|_| run(Path::new(&root), &input, &mut io::stdout().lock()));
    match done {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("inline-exemptions: {error}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    type Found = Vec<(String, String, String)>;

    fn found(items: &[(&str, &str, &str)]) -> Found {
        let mut items: Found = items
            .iter()
            .map(|&(tool, rule, function)| (tool.into(), rule.into(), function.into()))
            .collect();
        items.sort();
        items
    }

    fn sorted(occurrences: Vec<Occurrence>) -> Found {
        let mut items: Found = occurrences
            .into_iter()
            .map(|o| (o.tool, o.rule, o.function))
            .collect();
        items.sort();
        items
    }

    fn rs(text: &str) -> Found {
        sorted(scan_rust(text).unwrap_or_else(|e| panic!("{e}\n{text}")))
    }

    fn cargo(text: &str) -> Found {
        sorted(scan_cargo(text).unwrap_or_else(|e| panic!("{e}\n{text}")))
    }

    fn temp(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("inline-exemptions-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn put(root: &Path, path: &str, bytes: &[u8]) {
        let file = root.join(path);
        fs::create_dir_all(file.parent().unwrap()).unwrap();
        fs::write(file, bytes).unwrap();
    }

    fn scan_dir(root: &Path, paths: &[&str]) -> Vec<String> {
        let mut out = Vec::new();
        run(root, paths.join("\0").as_bytes(), &mut out).unwrap();
        String::from_utf8(out)
            .unwrap()
            .lines()
            .map(String::from)
            .collect()
    }

    #[test]
    fn s6_allow_counts_per_rule_and_function() {
        let text = "struct Foo;\nimpl Foo {\n    #[allow(clippy::unwrap_used, dead_code)]\n    fn bar() {}\n}\n";
        assert_eq!(
            rs(text),
            found(&[
                ("clippy", "clippy::unwrap_used", "Foo::bar"),
                ("rustc", "dead_code", "Foo::bar"),
            ])
        );
    }

    #[test]
    fn s7_rust_forms() {
        let rows = [
            ("#![allow(unused)]\nfn f() {}\n", "rustc", "unused"),
            (
                "#[expect(clippy::panic)]\nfn f() {}\n",
                "clippy",
                "clippy::panic",
            ),
            ("#[rustfmt::skip]\nfn f() {}\n", "rustfmt", "skip"),
            (
                "#[cfg_attr(coverage_nightly, coverage(off))]\nfn f() {}\n",
                "cargo-llvm-cov",
                "off",
            ),
            ("#[ignore]\nfn f() {}\n", "cargo-test", "ignore"),
            (
                "#[cfg_attr(miri, ignore)]\nfn f() {}\n",
                "cargo-test",
                "ignore",
            ),
            (
                "#[no_sanitize(address)]\nfn f() {}\n",
                "sanitizer",
                "address",
            ),
            ("// @generated\nfn f() {}\n", "generated", "generated"),
        ];
        for (text, tool, rule) in rows {
            let got = rs(text);
            assert_eq!(got.len(), 1, "{text}: {got:?}");
            assert_eq!(
                (got[0].0.as_str(), got[0].1.as_str()),
                (tool, rule),
                "{text}"
            );
        }
    }

    #[test]
    fn s7_generated_header_has_no_function() {
        assert_eq!(
            rs("// @generated\nfn f() {}\n"),
            found(&[("generated", "generated", "")])
        );
    }

    #[test]
    fn s10_jscpd_ignore_start_in_function() {
        let text =
            "fn f() {\n    // jscpd:ignore-start\n    let a = 1;\n    // jscpd:ignore-end\n}\n";
        assert_eq!(rs(text), found(&[("jscpd", "ignore", "f")]));
    }

    #[test]
    fn s11_cargo_machete_ignored() {
        let text = "[package]\nname = \"x\"\n\n[package.metadata.cargo-machete]\nignored = [\"serde\", \"log\"]\n";
        assert_eq!(
            cargo(text),
            found(&[("cargo-machete", "serde", ""), ("cargo-machete", "log", "")])
        );
    }

    #[test]
    fn s36_cargo_lint_allow() {
        let text = "[lints.clippy]\nunwrap_used = \"allow\"\n\n[workspace.lints.rust]\ndead_code = \"allow\"\n";
        assert_eq!(
            cargo(text),
            found(&[
                ("clippy", "clippy::unwrap_used", ""),
                ("rustc", "dead_code", "")
            ])
        );
    }

    #[test]
    fn s13_coverage_off_on_the_test_module_does_not_count() {
        let text = "#[cfg_attr(coverage_nightly, coverage(off))]\nfn slow() {}\n\n#[cfg(test)]\n#[cfg_attr(coverage_nightly, coverage(off))]\nmod tests {\n    fn helper() {}\n}\n";
        assert_eq!(rs(text), found(&[("cargo-llvm-cov", "off", "slow")]));
    }

    #[test]
    fn s27_other_test_module_names_and_mod_declarations_do_not_count_cfg_any_does() {
        let unit_tests =
            "#[cfg(test)]\n#[cfg_attr(coverage_nightly, coverage(off))]\nmod unit_tests {}\n";
        let prod = "#[cfg(any(test, feature = \"full\"))]\n#[cfg_attr(coverage_nightly, coverage(off))]\nmod prod {}\n";
        let helpers = "#[cfg_attr(coverage_nightly, coverage(off))]\n#[cfg(test)]\nmod helpers;\n";
        let off = found(&[("cargo-llvm-cov", "off", "")]);
        assert_eq!(rs(&format!("{unit_tests}{prod}{helpers}")), off);
        assert_eq!(rs(prod), off);
        assert_eq!(rs(&format!("{unit_tests}{helpers}")), found(&[]));
    }

    #[test]
    fn coverage_off_inside_a_test_module_and_inner_attributes() {
        let inner =
            "#[cfg(test)]\nmod tests {\n    #![cfg_attr(coverage_nightly, coverage(off))]\n}\n";
        assert_eq!(rs(inner), found(&[]));
        let all = "#[cfg(all(test, unix))]\n#[coverage(off)]\nmod t {}\n";
        assert_eq!(rs(all), found(&[("cargo-llvm-cov", "off", "")]));
        let on_fn = "#[cfg(test)]\nmod tests {\n    #[coverage(off)]\n    fn t() {}\n}\n";
        assert_eq!(rs(on_fn), found(&[("cargo-llvm-cov", "off", "t")]));
        let not_test = "#[cfg(not(test))]\n#[coverage(off)]\nmod m {}\n";
        assert_eq!(rs(not_test), found(&[("cargo-llvm-cov", "off", "")]));
        assert_eq!(rs("#[coverage(on)]\nfn f() {}\n"), found(&[]));
    }

    #[test]
    fn s29_doc_comment_text_never_counts() {
        let text = "//! @generated jscpd:ignore-start\n/// #[allow(dead_code)]\n/// jscpd:ignore-start\n/** #[ignore] jscpd:ignore-start */\nfn f() {\n    //! jscpd:ignore-start\n}\n";
        assert_eq!(rs(text), found(&[]));
    }

    #[test]
    fn literals_never_hold_comments() {
        let text = "fn f() {\n    let s = \"// @generated // jscpd:ignore-start #[allow(dead_code)]\";\n    let r = r#\"\n// jscpd:ignore-start\n/* jscpd:ignore-start */\n#[ignore]\n\"#;\n    let c = '/';\n}\n";
        assert_eq!(rs(text), found(&[]));
        assert_eq!(rs("static S: &str = \"// @generated\";\n"), found(&[]));
    }

    #[test]
    fn s47_items_outside_functions_and_nested_modules() {
        let rows = [
            ("struct Foo;\n#[allow(dead_code)]\nimpl Foo {}\n", ""),
            ("struct S {\n    #[allow(dead_code)]\n    x: u8,\n}\n", ""),
            ("mod m {\n    #[allow(dead_code)]\n    fn f() {}\n}\n", "f"),
        ];
        for (text, function) in rows {
            assert_eq!(
                rs(text),
                found(&[("rustc", "dead_code", function)]),
                "{text}"
            );
        }
    }

    #[test]
    fn items_inside_a_function_belong_to_it() {
        let text = "fn outer() {\n    #[allow(dead_code)]\n    struct S;\n    let c = || {\n        #[allow(unused)]\n        let x = 1;\n    };\n}\n";
        assert_eq!(
            rs(text),
            found(&[
                ("rustc", "dead_code", "outer"),
                ("rustc", "unused", "outer")
            ])
        );
    }

    #[test]
    fn method_names_carry_only_the_innermost_type() {
        let text = "struct Foo<K>(K);\n\
            impl<K> Foo<K> {\n    #[allow(a)]\n    fn m(&self) {\n        #[allow(b)]\n        fn inner() {}\n        impl Bar {\n            #[allow(c)]\n            fn n() {}\n        }\n    }\n}\n\
            impl std::fmt::Display for crate::x::Baz {\n    #[allow(d)]\n    fn fmt(&self) {}\n}\n\
            impl T for &mut Qux {\n    #[allow(e)]\n    fn t() {}\n}\n\
            impl T for [u8] {\n    #[allow(f)]\n    fn s() {}\n}\n\
            trait Tr {\n    #[allow(g)]\n    fn d() {}\n    #[allow(h)]\n    fn r();\n}\n";
        assert_eq!(
            rs(text),
            found(&[
                ("rustc", "a", "Foo::m"),
                ("rustc", "b", "inner"),
                ("rustc", "c", "Bar::n"),
                ("rustc", "d", "Baz::fmt"),
                ("rustc", "e", "Qux::t"),
                ("rustc", "f", "s"),
                ("rustc", "g", "Tr::d"),
                ("rustc", "h", "Tr::r"),
            ])
        );
    }

    #[test]
    fn inner_attributes_at_the_crate_top_and_in_a_function() {
        let text = "#![allow(dead_code)]\nfn f() {\n    #![allow(clippy::x)]\n}\n";
        assert_eq!(
            rs(text),
            found(&[("rustc", "dead_code", ""), ("clippy", "clippy::x", "f")])
        );
    }

    #[test]
    fn nested_cfg_attr_and_reason_keys() {
        let text = "#[cfg_attr(a, cfg_attr(all(b, c), allow(dead_code, clippy::x), rustfmt::skip))]\n#[expect(unused, reason = \"kept\")]\nfn f() {}\n";
        assert_eq!(
            rs(text),
            found(&[
                ("rustc", "dead_code", "f"),
                ("clippy", "clippy::x", "f"),
                ("rustfmt", "skip", "f"),
                ("rustc", "unused", "f"),
            ])
        );
    }

    #[test]
    fn other_attribute_forms() {
        let text = "#[rustfmt::skip::macros(vec)]\n#[rustfmt::skip::attributes(derive)]\n#[ignore = \"slow\"]\n#[no_sanitize(address, memory)]\n#[sanitize(thread = \"off\", hwaddress = \"on\")]\n#[allow(rustdoc::broken_intra_doc_links)]\n#[warn(dead_code)]\n#[deny(unused)]\n#[forbid(x)]\n#[test]\nfn f() {}\n";
        assert_eq!(
            rs(text),
            found(&[
                ("cargo-test", "ignore", "f"),
                ("sanitizer", "address", "f"),
                ("sanitizer", "memory", "f"),
                ("sanitizer", "thread", "f"),
                ("rustc", "rustdoc::broken_intra_doc_links", "f"),
            ])
        );
    }

    #[test]
    fn attributes_inside_macro_token_streams() {
        let text = "macro_rules! m {\n    () => {\n        #[allow(dead_code)]\n        fn g() {}\n    };\n    ($meta:meta) => {\n        #[$meta]\n        /// #[ignore]\n        fn h() {}\n    };\n}\nfn f() {\n    my_macro! { #![allow(unused)] x = [#[cfg_attr(x, ignore)] 1] }\n}\n";
        assert_eq!(
            rs(text),
            found(&[
                ("rustc", "dead_code", ""),
                ("rustc", "unused", "f"),
                ("cargo-test", "ignore", "f"),
            ])
        );
    }

    #[test]
    fn comments_belong_to_the_function_they_are_in_or_directly_above() {
        let text = "// jscpd:ignore-start\n#[test]\nfn a() {}\n\n// jscpd:ignore-start\n\nfn b() {}\nstruct S; // jscpd:ignore-start\nfn c() {}\nstruct Foo;\nimpl Foo {\n    // jscpd:ignore-start\n    const X: u8 = 1;\n\n    /* jscpd:ignore-start */\n    // more\n    pub fn m() {\n        let f = |x: u8| {\n            /* /* nested */ jscpd:ignore-start */\n        };\n        // jscpd:ignore-start\n        fn inner() {}\n    }\n}\n";
        assert_eq!(
            rs(text),
            found(&[
                ("jscpd", "ignore", "a"),
                ("jscpd", "ignore", ""),
                ("jscpd", "ignore", ""),
                ("jscpd", "ignore", ""),
                ("jscpd", "ignore", "Foo::m"),
                ("jscpd", "ignore", "Foo::m"),
                ("jscpd", "ignore", "inner"),
            ])
        );
    }

    #[test]
    fn generated_header_forms_and_position() {
        let yes = [
            "// @generated\nfn f() {}\n",
            "\u{feff}// @generated\nfn f() {}\n",
            "#!/usr/bin/env rust-script\n\n// @generated by x\nfn main() {}\n",
            "/* Code generated by tool. DO NOT EDIT. */\nfn f() {}\n",
            "// Code generated  DO NOT EDIT.\n",
            "// Generated by the protocol buffer compiler.  DO NOT EDIT!\nfn f() {}\n",
            "// @generated\n// Code generated by x. DO NOT EDIT.\nfn f() {}\n",
        ];
        for text in yes {
            assert_eq!(
                rs(text),
                found(&[("generated", "generated", "")]),
                "{text:?}"
            );
        }
        let no = [
            "fn f() {}\n// @generated\n",
            "// Code generated DO NOT EDIT.\nfn f() {}\n",
            "// Code generated by x.\n// DO NOT EDIT.\nfn f() {}\n",
            "// Generated by the protocol buffer compiler. DO NOT EDIT!\nfn f() {}\n",
        ];
        for text in no {
            assert_eq!(rs(text), found(&[]), "{text:?}");
        }
        assert_eq!(
            rs("#![allow(x)]\n// @generated\n"),
            found(&[("rustc", "x", "")])
        );
    }

    #[test]
    fn a_shebang_is_skipped_but_an_inner_attribute_is_not_a_shebang() {
        let text = "#!/usr/bin/env rust-script\n#![allow(dead_code)]\nfn main() {}\n";
        assert_eq!(rs(text), found(&[("rustc", "dead_code", "")]));
        let text = "#! [allow(unused)]\nfn main() {}\n";
        assert_eq!(rs(text), found(&[("rustc", "unused", "")]));
    }

    #[test]
    fn lex_and_parse_errors_name_the_line() {
        let lex = scan_rust("fn f() {}\nfn g() { \"open }\n").unwrap_err();
        assert!(lex.starts_with("line 2: "), "{lex}");
        let parse = scan_rust("fn f() {}\n\nfn g( {}\n").unwrap_err();
        assert!(parse.starts_with("line 3: "), "{parse}");
        let end = scan_rust("fn f() {}\n\nstruct").unwrap_err();
        assert!(end.starts_with("line 3: unexpected end of input"), "{end}");
    }

    #[test]
    fn cargo_lint_forms() {
        let text = "[lints]\nworkspace = true\n\n[lints.rust]\nunsafe_code = { level = \"allow\", priority = -1 }\nunused = \"warn\"\nx = { level = \"deny\" }\n\n[lints.rustdoc]\nbroken_intra_doc_links = \"allow\"\n\n[workspace.lints.clippy]\npedantic = { level = \"allow\" }\n\n[workspace.metadata.cargo-machete]\nignored = [\"anyhow\"]\n";
        assert_eq!(
            cargo(text),
            found(&[
                ("rustc", "unsafe_code", ""),
                ("rustc", "rustdoc::broken_intra_doc_links", ""),
                ("clippy", "clippy::pedantic", ""),
            ])
        );
        let error = scan_cargo("[package]\nname = \n").unwrap_err();
        assert!(error.starts_with("line 2: "), "{error}");
    }

    #[test]
    fn json_strings_are_escaped() {
        assert_eq!(
            json_string("a\"b\\c\nd\t\u{1}é"),
            "\"a\\\"b\\\\c\\nd\\t\\u0001é\""
        );
    }

    #[test]
    fn s23_non_utf8_and_syntax_errors_are_error_records() {
        let root = temp("s23");
        put(&root, "src/y.rs", b"fn f() {}\n// \xff\n");
        put(&root, "src/z.rs", b"fn f( {\n");
        put(&root, "src/ok.rs", b"#[ignore]\nfn t() {}\n");
        put(
            &root,
            "crates/a/Cargo.toml",
            b"[lints.rust]\nunused = \"allow\"\n",
        );
        put(&root, "bad/Cargo.toml", b"[lints\n");
        let lines = scan_dir(
            &root,
            &[
                "src/y.rs",
                "src/z.rs",
                "src/ok.rs",
                "crates/a/Cargo.toml",
                "bad/Cargo.toml",
            ],
        );
        assert_eq!(lines.len(), 5, "{lines:?}");
        assert_eq!(
            lines[0],
            "{\"file\":\"src/y.rs\",\"error\":\"line 2: not UTF-8\"}"
        );
        assert!(
            lines[1].starts_with("{\"file\":\"src/z.rs\",\"error\":\"line 1: "),
            "{lines:?}"
        );
        assert_eq!(
            lines[2],
            "{\"tool\":\"cargo-test\",\"file\":\"src/ok.rs\",\"rule\":\"ignore\",\"function\":\"t\"}"
        );
        assert_eq!(
            lines[3],
            "{\"tool\":\"rustc\",\"file\":\"crates/a/Cargo.toml\",\"rule\":\"unused\",\"function\":\"\"}"
        );
        assert!(
            lines[4].starts_with("{\"file\":\"bad/Cargo.toml\",\"error\":\"line 1: "),
            "{lines:?}"
        );
        fs::remove_dir_all(root).unwrap();
    }
}
