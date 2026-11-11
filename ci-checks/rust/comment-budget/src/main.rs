use std::fs;
use std::io::{self, Write};
use std::path::Path;
use std::process::{Command, ExitCode};

const ALLOWANCE: u64 = 300;
const PURPOSE: &str = "The comment budget keeps comments short: meet it by cutting comments or doc \
comments, never by adding code to lower the ratio.";
const USAGE: &str =
    "usage: comment-budget [--base REF] [--source-limit PERCENT] [--test-limit PERCENT] [--all]";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Lex {
    Space,
    Comment,
    Shebang,
    Literal,
    Lifetime,
    Ident,
    Punct,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Token {
    lex: Lex,
    start: usize,
    end: usize,
}

struct Lexer<'a> {
    text: &'a str,
    chars: Vec<(usize, char)>,
    tokens: Vec<Token>,
}

impl<'a> Lexer<'a> {
    fn at(&self, i: usize) -> Option<char> {
        self.chars.get(i).map(|&(_, c)| c)
    }

    fn offset(&self, i: usize) -> usize {
        self.chars.get(i).map_or(self.text.len(), |&(o, _)| o)
    }

    fn push(&mut self, lex: Lex, from: usize, to: usize) -> usize {
        self.tokens.push(Token {
            lex,
            start: self.offset(from),
            end: self.offset(to),
        });
        to
    }

    fn while_from(&self, mut i: usize, keep: impl Fn(char) -> bool) -> usize {
        while self.at(i).is_some_and(&keep) {
            i += 1;
        }
        i
    }

    fn error(&self, i: usize, what: &str) -> String {
        let line = self.text[..self.offset(i)].matches('\n').count() + 1;
        format!("line {line}: unterminated {what}")
    }

    fn block_comment(&self, start: usize) -> Result<usize, String> {
        let (mut i, mut depth) = (start + 2, 1);
        while depth > 0 {
            match (self.at(i), self.at(i + 1)) {
                (None, _) => return Err(self.error(start, "block comment")),
                (Some('/'), Some('*')) => (depth, i) = (depth + 1, i + 2),
                (Some('*'), Some('/')) => (depth, i) = (depth - 1, i + 2),
                _ => i += 1,
            }
        }
        Ok(i)
    }

    fn quoted(&self, start: usize, quote: char, what: &str) -> Result<usize, String> {
        let mut i = start + 1;
        loop {
            match self.at(i) {
                None => return Err(self.error(start, what)),
                Some('\\') => i += 2,
                Some(c) if c == quote => return Ok(i + 1),
                _ => i += 1,
            }
        }
    }

    fn raw_string(&self, start: usize, hashes: usize) -> Result<usize, String> {
        let mut i = start + hashes + 1;
        loop {
            match self.at(i) {
                None => return Err(self.error(start, "raw string")),
                Some('"') if (1..=hashes).all(|k| self.at(i + k) == Some('#')) => {
                    return Ok(i + 1 + hashes);
                }
                _ => i += 1,
            }
        }
    }

    fn quote(&self, i: usize) -> Result<(Lex, usize), String> {
        match (self.at(i + 1), self.at(i + 2)) {
            (Some('\\'), _) => Ok((Lex::Literal, self.quoted(i, '\'', "char literal")?)),
            (Some(_), Some('\'')) => Ok((Lex::Literal, i + 3)),
            (Some(c), _) if ident_start(c) => Ok((Lex::Lifetime, self.while_from(i + 1, ident))),
            _ => Ok((Lex::Punct, i + 1)),
        }
    }

    fn word(&self, i: usize) -> Result<(Lex, usize), String> {
        let end = self.while_from(i, ident);
        let word = &self.text[self.offset(i)..self.offset(end)];
        let next = self.at(end);
        if matches!(word, "r" | "br" | "cr") {
            let quote = self.while_from(end, |c| c == '#');
            if self.at(quote) == Some('"') {
                return Ok((Lex::Literal, self.raw_string(end, quote - end)?));
            }
            if word == "r" && quote == end + 1 && self.at(quote).is_some_and(ident_start) {
                return Ok((Lex::Ident, self.while_from(quote, ident)));
            }
        }
        match (word, next) {
            ("b" | "c", Some('"')) => Ok((Lex::Literal, self.quoted(end, '"', "string")?)),
            ("b", Some('\'')) => Ok((Lex::Literal, self.quoted(end, '\'', "byte literal")?)),
            _ => Ok((Lex::Ident, end)),
        }
    }

    fn number(&self, i: usize) -> usize {
        let end = self.while_from(i, ident);
        let fraction =
            self.at(end) == Some('.') && self.at(end + 1).is_some_and(|c| c.is_ascii_digit());
        if fraction { self.number(end + 1) } else { end }
    }

    fn shebang(&self) -> Option<usize> {
        let rest = self.text.strip_prefix("#!")?;
        if rest.trim_start().starts_with('[') {
            return None;
        }
        Some(self.while_from(0, |c| c != '\n'))
    }

    fn run(mut self) -> Result<Vec<Token>, String> {
        let mut i = match self.shebang() {
            Some(end) => self.push(Lex::Shebang, 0, end),
            None => 0,
        };
        while let Some(c) = self.at(i) {
            let next = self.at(i + 1);
            let (lex, end) = match (c, next) {
                _ if c.is_whitespace() => (Lex::Space, self.while_from(i, char::is_whitespace)),
                ('/', Some('/')) => (Lex::Comment, self.while_from(i, |c| c != '\n')),
                ('/', Some('*')) => (Lex::Comment, self.block_comment(i)?),
                ('"', _) => (Lex::Literal, self.quoted(i, '"', "string")?),
                ('\'', _) => self.quote(i)?,
                _ if c.is_ascii_digit() => (Lex::Literal, self.number(i)),
                _ if ident_start(c) => self.word(i)?,
                _ => (Lex::Punct, i + 1),
            };
            i = self.push(lex, i, end);
        }
        Ok(self.tokens)
    }
}

fn ident_start(c: char) -> bool {
    c == '_' || c.is_alphabetic()
}

fn ident(c: char) -> bool {
    c == '_' || c.is_alphanumeric()
}

fn lex(text: &str) -> Result<Vec<Token>, String> {
    Lexer {
        text,
        chars: text.char_indices().collect(),
        tokens: Vec::new(),
    }
    .run()
}

struct Code<'a> {
    text: &'a str,
    tokens: Vec<Token>,
}

impl Code<'_> {
    fn is(&self, i: usize, word: &str) -> bool {
        self.tokens
            .get(i)
            .is_some_and(|t| &self.text[t.start..t.end] == word)
    }

    fn is_all(&self, i: usize, words: &[&str]) -> bool {
        words.iter().enumerate().all(|(k, w)| self.is(i + k, w))
    }

    fn close(&self, open: usize) -> Option<usize> {
        let pair = |o: &str| match o {
            "(" => Some(")"),
            "[" => Some("]"),
            "{" => Some("}"),
            _ => None,
        };
        let open_text = &self.text[self.tokens[open].start..self.tokens[open].end];
        let close_text = pair(open_text)?;
        let mut depth = 0usize;
        for (i, t) in self.tokens.iter().enumerate().skip(open) {
            let s = &self.text[t.start..t.end];
            if s == open_text {
                depth += 1;
            } else if s == close_text {
                depth -= 1;
                if depth == 0 {
                    return Some(i);
                }
            }
        }
        None
    }

    fn test_module_end(&self, attribute: usize) -> Option<usize> {
        let mut i = attribute + 7;
        while self.is_all(i, &["#", "["]) {
            i = self.close(i + 1)? + 1;
        }
        if self.is(i, "pub") {
            i += 1;
            if self.is(i, "(") {
                i = self.close(i)? + 1;
            }
        }
        let named = self.tokens.get(i + 1).is_some_and(|t| t.lex == Lex::Ident);
        if self.is(i, "mod") && named && self.is(i + 2, "{") {
            self.close(i + 2)
        } else {
            None
        }
    }

    fn test_ranges(&self) -> Vec<(usize, usize)> {
        let mut ranges = Vec::new();
        let mut i = 0;
        while i < self.tokens.len() {
            let attribute = self.is_all(i, &["#", "[", "cfg", "(", "test", ")", "]"]);
            match attribute.then(|| self.test_module_end(i)).flatten() {
                Some(end) => {
                    ranges.push((self.tokens[i].start, self.tokens[end].end));
                    i = end + 1;
                }
                None => i += 1,
            }
        }
        ranges
    }
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
struct Counts {
    comment: u64,
    code: u64,
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
struct Measure {
    source: Counts,
    tests: Counts,
}

fn chars(text: &str) -> u64 {
    text.chars().filter(|c| !c.is_whitespace()).count() as u64
}

fn measure(text: &str, whole_file_is_test: bool) -> Result<Measure, String> {
    let tokens = lex(text)?;
    let significant = tokens
        .iter()
        .copied()
        .filter(|t| !matches!(t.lex, Lex::Space | Lex::Comment))
        .collect();
    let ranges = Code {
        text,
        tokens: significant,
    }
    .test_ranges();
    let mut result = Measure::default();
    for token in tokens {
        let in_test = ranges
            .iter()
            .any(|&(start, end)| start <= token.start && token.end <= end);
        let part = if whole_file_is_test || in_test {
            &mut result.tests
        } else {
            &mut result.source
        };
        let n = chars(&text[token.start..token.end]);
        if token.lex == Lex::Comment {
            part.comment += n;
        } else {
            part.code += n;
        }
    }
    Ok(result)
}

fn is_test_path(path: &str) -> bool {
    path.starts_with("tests/") || path.contains("/tests/") || path.ends_with("_test.rs")
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Options {
    base: String,
    source_limit: u64,
    test_limit: u64,
    all: bool,
}

fn parse(args: &[String]) -> Result<Options, String> {
    let mut options = Options {
        base: "origin/main".into(),
        source_limit: 10,
        test_limit: 5,
        all: false,
    };
    let mut args = args.iter();
    while let Some(flag) = args.next() {
        if flag == "--all" {
            options.all = true;
            continue;
        }
        let value = args.next().ok_or_else(|| format!("{flag} needs a value"))?;
        let percent = || {
            value
                .parse()
                .map_err(|_| format!("{flag}: not a number: {value}"))
        };
        match flag.as_str() {
            "--base" => options.base = value.clone(),
            "--source-limit" => options.source_limit = percent()?,
            "--test-limit" => options.test_limit = percent()?,
            _ => return Err(format!("unknown argument {flag}\n{USAGE}")),
        }
    }
    Ok(options)
}

fn git(root: &Path, args: &[&str]) -> Result<String, String> {
    let done = Command::new("git")
        .args([
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
        ])
        .arg(root)
        .args(args)
        .output()
        .map_err(|e| format!("git: {e}"))?;
    if !done.status.success() {
        let stderr = String::from_utf8_lossy(&done.stderr);
        return Err(format!("git {} failed: {}", args[0], stderr.trim()));
    }
    String::from_utf8(done.stdout).map_err(|e| format!("git {}: {e}", args[0]))
}

fn files(root: &Path, options: &Options) -> Result<Vec<String>, String> {
    let listed = if options.all {
        git(root, &["ls-files", "-z", "--", "*.rs"])?
    } else {
        let fork = git(root, &["merge-base", &options.base, "HEAD"])?;
        let diff = [
            "diff",
            "--name-only",
            "--no-renames",
            "--diff-filter=d",
            "-z",
        ];
        git(root, &[&diff[..], &[fork.trim(), "--"]].concat())?
    };
    let mut paths: Vec<String> = listed
        .split('\0')
        .filter(|p| p.ends_with(".rs") && root.join(p).is_file())
        .map(String::from)
        .collect();
    paths.sort();
    Ok(paths)
}

fn percent(n: u64, code: u64) -> String {
    match (n, code) {
        (_, 0) if n > 0 => "inf".into(),
        (_, 0) => "0.00%".into(),
        _ => format!("{:.2}%", n as f64 * 100.0 / code as f64),
    }
}

fn run(args: &[String], cwd: &Path, out: &mut impl Write) -> Result<bool, String> {
    let options = parse(args)?;
    let root = git(cwd, &["rev-parse", "--show-toplevel"])?;
    let root = Path::new(root.trim());
    let mut measured = Vec::new();
    for path in files(root, &options)? {
        let text = fs::read_to_string(root.join(&path)).map_err(|e| format!("{path}: {e}"))?;
        let m = measure(&text, is_test_path(&path)).map_err(|e| format!("{path}: {e}"))?;
        measured.push((path, m));
    }
    let limit =
        |m: &Measure| options.source_limit * m.source.code + options.test_limit * m.tests.code;
    let over: Vec<_> = measured
        .iter()
        .filter(|(_, m)| (m.source.comment + m.tests.comment) * 100 > limit(m) + ALLOWANCE * 100)
        .collect();
    let scope = if options.all { "file" } else { "changed file" };
    let name = format!(
        "comment budget (source {}%, tests {}% of code + {ALLOWANCE} characters per {scope})",
        options.source_limit, options.test_limit
    );
    let print =
        |out: &mut dyn Write, line: String| writeln!(out, "{line}").map_err(|e| e.to_string());
    if over.is_empty() {
        print(out, format!("ok {name}: {} checked", measured.len()))?;
    } else {
        print(
            out,
            format!(
                "FAIL {name}: {} of {} over budget",
                over.len(),
                measured.len()
            ),
        )?;
        for (path, m) in &over {
            let (n, code) = (
                m.source.comment + m.tests.comment,
                m.source.code + m.tests.code,
            );
            let allowed = limit(m) / 100 + ALLOWANCE;
            print(
                out,
                format!(
                    "  {path}: {} ({n} comment / {code} code characters, {} in tests; {allowed} allowed, {} over)",
                    percent(n, code),
                    m.tests.code,
                    n - allowed
                ),
            )?;
        }
    }
    let total = measured
        .iter()
        .fold(Measure::default(), |a, (_, m)| Measure {
            source: Counts {
                comment: a.source.comment + m.source.comment,
                code: a.source.code + m.source.code,
            },
            tests: Counts {
                comment: a.tests.comment + m.tests.comment,
                code: a.tests.code + m.tests.code,
            },
        });
    for (label, c) in [("source", total.source), ("tests", total.tests)] {
        print(
            out,
            format!(
                "total {label}: {} comment / {} code characters ({})",
                c.comment,
                c.code,
                percent(c.comment, c.code)
            ),
        )?;
    }
    if !over.is_empty() {
        print(out, PURPOSE.into())?;
    }
    Ok(over.is_empty())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args, Path::new("."), &mut io::stdout()) {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::from(1),
        Err(error) => {
            eprintln!("comment-budget: {error}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn counts(comment: &str, code: &str) -> Counts {
        Counts {
            comment: chars(comment),
            code: chars(code),
        }
    }

    fn source(text: &str) -> Counts {
        let m = measure(text, false).unwrap();
        assert_eq!(m.tests, Counts::default());
        m.source
    }

    #[test]
    fn line_block_and_doc_comments_count() {
        for (text, comment, code) in [
            ("let x = 1; // note\n", "// note", "let x = 1;"),
            ("/// Doc.\nfn f() {}\n", "/// Doc.", "fn f() {}"),
            ("//! Crate.\nfn f() {}\n", "//! Crate.", "fn f() {}"),
            ("/** Doc. */ fn f() {}", "/** Doc. */", "fn f() {}"),
            ("/*! Crate. */ fn f() {}", "/*! Crate. */", "fn f() {}"),
            (
                "a /* x /* nested */ y */ b",
                "/* x /* nested */ y */",
                "a b",
            ),
        ] {
            assert_eq!(source(text), counts(comment, code), "{text}");
        }
    }

    #[test]
    fn literals_and_lifetimes_are_never_comments() {
        for text in [
            r#"let s = "// not a comment /* either */";"#,
            r#"let s = "escaped \" // still a string";"#,
            r###"let s = r#"raw " // still raw"#;"###,
            r#"let s = r"raw // plain";"#,
            r#"let s = b"bytes // here"; let t = br"raw /*";"#,
            r#"let s = c"c string //";"#,
            r#"let c = '"'; let d = '/'; let e = '\''; let f = b'/';"#,
            "fn f<'a>(x: &'a str) -> &'a str { x } let q = '\"';",
            "'outer: loop { break 'outer; }",
            "let r#type = 1; let x = 1.5e3;",
        ] {
            assert_eq!(source(text), counts("", text), "{text}");
        }
    }

    #[test]
    fn a_lifetime_followed_by_a_comment_still_finds_the_comment() {
        let text = "fn f<'a>() {} // note\n";
        assert_eq!(source(text), counts("// note", "fn f<'a>() {}"));
    }

    #[test]
    fn a_first_line_shebang_is_code_but_an_inner_attribute_is_not_a_shebang() {
        assert_eq!(
            source("#!/usr/bin/env run-cargo-script\nfn main() {}\n"),
            counts("", "#!/usr/bin/env run-cargo-script fn main() {}")
        );
        let attribute = "#![allow(dead_code)] // why\nfn main() {}\n";
        assert_eq!(
            source(attribute),
            counts("// why", "#![allow(dead_code)] fn main() {}")
        );
    }

    #[test]
    fn unterminated_literals_and_comments_are_errors() {
        for text in ["/* open", "\"open", "r#\"open\"", "/* a /* b */"] {
            assert!(measure(text, false).is_err(), "{text}");
        }
    }

    #[test]
    fn inline_test_modules_count_as_tests() {
        let module = "#[cfg(test)]\n#[allow(unused)]\npub(crate) mod tests {\n    // why\n    fn t() { let s = \"}\"; }\n}";
        let text = format!("// top\nfn f() {{}}\n{module}\nfn g() {{}}\n");
        let m = measure(&text, false).unwrap();
        assert_eq!(m.source, counts("// top", "fn f() {} fn g() {}"));
        assert_eq!(m.tests.comment, chars("// why"));
        assert_eq!(m.tests.code, chars(module) - chars("// why"));
    }

    #[test]
    fn other_cfg_test_items_stay_source() {
        for text in [
            "#[cfg(test)]\nmod tests;\n// a\n",
            "#[cfg(test)]\nfn helper() {}\n// a\n",
            "#[cfg(not(test))]\nmod real { }\n// a\n",
        ] {
            assert_eq!(
                measure(text, false).unwrap().tests,
                Counts::default(),
                "{text}"
            );
        }
    }

    #[test]
    fn test_paths_make_the_whole_file_tests() {
        for path in [
            "tests/a.rs",
            "crate/tests/b.rs",
            "tests/unit/c.rs",
            "src/d_test.rs",
        ] {
            assert!(is_test_path(path), "{path}");
        }
        for path in ["src/e.rs", "contests/f.rs", "tests.rs", "src/test.rs"] {
            assert!(!is_test_path(path), "{path}");
        }
        let m = measure("fn f() {} // a\n", true).unwrap();
        assert_eq!(
            (m.source, m.tests),
            (Counts::default(), counts("// a", "fn f() {}"))
        );
    }

    #[test]
    fn arguments_have_defaults_and_overrides() {
        let args = |s: &str| s.split_whitespace().map(String::from).collect::<Vec<_>>();
        let default = parse(&[]).unwrap();
        assert_eq!(
            (
                default.base.as_str(),
                default.source_limit,
                default.test_limit,
                default.all
            ),
            ("origin/main", 10, 5, false)
        );
        let set = parse(&args("--base HEAD --source-limit 20 --test-limit 3 --all")).unwrap();
        assert_eq!(
            (set.base.as_str(), set.source_limit, set.test_limit, set.all),
            ("HEAD", 20, 3, true)
        );
        for bad in ["--base", "--source-limit x", "--nope 1"] {
            assert!(parse(&args(bad)).is_err(), "{bad}");
        }
    }

    struct Repo(PathBuf);

    impl Repo {
        fn new(name: &str) -> Repo {
            let dir =
                std::env::temp_dir().join(format!("comment-budget-{}-{name}", std::process::id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).unwrap();
            let repo = Repo(dir);
            repo.git(&["init", "-q", "-b", "main"]);
            repo
        }

        fn git(&self, args: &[&str]) {
            let ok = Command::new("git")
                .args(["-c", "user.name=t", "-c", "user.email=t@example.com"])
                .args(["-c", "commit.gpgsign=false", "-C"])
                .arg(&self.0)
                .args(args)
                .status()
                .unwrap()
                .success();
            assert!(ok, "git {args:?}");
        }

        fn write(&self, path: &str, text: &str) {
            let full = self.0.join(path);
            fs::create_dir_all(full.parent().unwrap()).unwrap();
            fs::write(full, text).unwrap();
        }

        fn run(&self, args: &str) -> (Result<bool, String>, String) {
            let args: Vec<String> = args.split_whitespace().map(String::from).collect();
            let mut out = Vec::new();
            let result = run(&args, &self.0, &mut out);
            (result, String::from_utf8(out).unwrap())
        }
    }

    impl Drop for Repo {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn only_files_changed_since_the_merge_base_are_checked() {
        let repo = Repo::new("diff");
        let heavy = format!("// {}\nfn f() {{}}\n", "x".repeat(400));
        repo.write("src/old.rs", &heavy);
        repo.git(&["add", "."]);
        repo.git(&["commit", "-q", "-m", "base"]);
        repo.write(
            "src/new.rs",
            &format!("// {}\nfn g() {{}}\n", "y".repeat(298)),
        );
        repo.write("tests/new.rs", "fn t() {}\n");
        repo.git(&["add", "."]);
        let (result, out) = repo.run("--base main");
        assert_eq!(result, Ok(true), "{out}");
        assert!(out.starts_with("ok comment budget (source 10%, tests 5% of code + 300 characters per changed file): 2 checked\n"), "{out}");

        repo.write(
            "src/new.rs",
            &format!("// {}\nfn g() {{}}\n", "y".repeat(300)),
        );
        let (result, out) = repo.run("--base main");
        assert_eq!(result, Ok(false), "{out}");
        assert!(out.contains("FAIL comment budget"), "{out}");
        assert!(
            out.contains(
                "src/new.rs: 4314.29% (302 comment / 7 code characters, 0 in tests; 300 allowed, 2 over)"
            ),
            "{out}"
        );
        assert!(out.trim_end().ends_with(PURPOSE), "{out}");
        assert!(!out.contains("src/old.rs:"), "{out}");
    }

    #[test]
    fn all_checks_every_tracked_file_and_reports_totals() {
        let repo = Repo::new("all");
        repo.write(
            "src/old.rs",
            &format!("// {}\nfn f() {{}}\n", "x".repeat(400)),
        );
        repo.write("tests/t.rs", "// ab\nfn t() {}\n");
        repo.git(&["add", "."]);
        repo.git(&["commit", "-q", "-m", "base"]);
        let (result, out) = repo.run("--all");
        assert_eq!(result, Ok(false), "{out}");
        assert!(out.contains("1 of 2 over budget"), "{out}");
        assert!(
            out.contains("total source: 402 comment / 7 code characters (5742.86%)"),
            "{out}"
        );
        assert!(
            out.contains("total tests: 4 comment / 7 code characters (57.14%)"),
            "{out}"
        );
        let (result, out) = repo.run("--base main");
        assert_eq!(result, Ok(true), "{out}");
        assert!(out.contains(": 0 checked"), "{out}");
    }

    #[test]
    fn a_bad_base_is_an_error() {
        let repo = Repo::new("bad");
        repo.write("a.rs", "fn f() {}\n");
        repo.git(&["add", "."]);
        repo.git(&["commit", "-q", "-m", "base"]);
        assert!(repo.run("--base nope").0.is_err());
    }
}
