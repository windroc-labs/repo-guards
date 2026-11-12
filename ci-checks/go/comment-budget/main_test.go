package main

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

const defaultConfig = `source_percent = 10
test_percent = 5
allowance_chars = 300
test_paths = ["**/*_test.go", "**/testdata/**"]
`

func TestMain(m *testing.M) {
	home, err := os.MkdirTemp("", "comment-budget-home-")
	if err != nil {
		panic(err)
	}
	for _, name := range []string{
		"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
		"GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CEILING_DIRECTORIES", "GIT_COMMON_DIR",
		"GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
		"XDG_CONFIG_HOME",
	} {
		os.Unsetenv(name)
	}
	for name, value := range map[string]string{
		"HOME":                home,
		"GIT_CONFIG_GLOBAL":   os.DevNull,
		"GIT_CONFIG_SYSTEM":   os.DevNull,
		"GIT_CONFIG_NOSYSTEM": "1",
		"GIT_AUTHOR_NAME":     "t",
		"GIT_AUTHOR_EMAIL":    "t@example.com",
		"GIT_COMMITTER_NAME":  "t",
		"GIT_COMMITTER_EMAIL": "t@example.com",
		"GIT_TERMINAL_PROMPT": "0",
		"LC_ALL":              "C",
	} {
		os.Setenv(name, value)
	}
	code := m.Run()
	os.RemoveAll(home)
	os.Exit(code)
}

func visible(text string) int64 {
	return int64(len(strings.Join(strings.Fields(text), "")))
}

func counts(comment, code string) Counts {
	return Counts{comment: visible(comment), code: visible(code)}
}

func source(t *testing.T, text string) Counts {
	t.Helper()
	m, err := measure([]byte(text), false)
	if err != nil {
		t.Fatalf("measure(%q): %v", text, err)
	}
	if m.tests != (Counts{}) {
		t.Fatalf("measure(%q) tests = %+v, want none", text, m.tests)
	}
	return m.source
}

func TestLineAndBlockCommentsCount(t *testing.T) {
	for _, c := range []struct{ text, comment, code string }{
		{"package a // why\n", "// why", "package a"},
		{"// Package a does x.\npackage a\n", "// Package a does x.", "package a"},
		{"package a\n/* one\n   two */\nvar x = 1\n", "/* one two */", "package a var x = 1"},
		{"package a\nvar x = /* inline */ 1\n", "/* inline */", "package a var x = 1"},
		{"package a\r\n// crlf\r\nvar x = 1\r\n", "// crlf", "package a var x = 1"},
	} {
		if got, want := source(t, c.text), counts(c.comment, c.code); got != want {
			t.Errorf("%q: got %+v, want %+v", c.text, got, want)
		}
	}
}

func TestStringsAndRunesAreNeverComments(t *testing.T) {
	text := "package a\nvar s = \"// not\" + `/* raw */` + string('/')\n"
	if got, want := source(t, text), counts("", text); got != want {
		t.Errorf("got %+v, want %+v", got, want)
	}
}

func TestDirectivesCountAsCode(t *testing.T) {
	directives := "//go:build linux\n//go:generate stringer -type=X\n//line a.go:1\n//export F\n//extern f\n"
	text := directives + "package a\n"
	if got, want := source(t, text), counts("", text); got != want {
		t.Errorf("got %+v, want %+v", got, want)
	}
	for _, comment := range []string{"// go:build x", "//lint:ignore x", "//linear", "//exported", "/*go:build x*/"} {
		text := comment + "\npackage a\n"
		if got, want := source(t, text), counts(comment, "package a"); got != want {
			t.Errorf("%q: got %+v, want %+v", comment, got, want)
		}
	}
}

func TestUnparsableFilesAreErrors(t *testing.T) {
	for _, text := range []string{
		"package a\n/* open\n",
		"package a\nvar s = `open\n",
		"package a\nvar s = \"open\n",
		"package a\nvar c = 'ab'\n",
		"package a\nvar x = 1 # 2\n",
		"package a\nvar s = \"\xff\"\n",
	} {
		if _, err := measure([]byte(text), false); err == nil {
			t.Errorf("%q: no error", text)
		}
	}
}

func TestATestPathCountsTheWholeFileAsTests(t *testing.T) {
	m, err := measure([]byte("package a // a\n"), true)
	if err != nil {
		t.Fatal(err)
	}
	if m.source != (Counts{}) || m.tests != counts("// a", "package a") {
		t.Errorf("got %+v", m)
	}
}

func TestAConfigWithTheDefaultValuesParses(t *testing.T) {
	got, err := parseConfig(defaultConfig)
	want := Config{
		sourcePercent:  10,
		testPercent:    5,
		allowanceChars: 300,
		testPaths:      []string{"**/*_test.go", "**/testdata/**"},
	}
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Errorf("got %+v, %v; want %+v", got, err, want)
	}
	got, err = parseConfig("# top\nsource_percent=0\ntest_percent = 0\nallowance_chars = 0 # a\ntest_paths = []\n")
	if err != nil || got.testPaths == nil || len(got.testPaths) != 0 || got.sourcePercent != 0 {
		t.Errorf("zeros and empty list: got %+v, %v", got, err)
	}
}

func TestTheTemplateConfigHasTheDefaultValues(t *testing.T) {
	text, err := os.ReadFile(filepath.Join("..", "comment-budget.toml"))
	if err != nil {
		t.Fatal(err)
	}
	if string(text) != defaultConfig {
		t.Errorf("template:\n%s\nwant:\n%s", text, defaultConfig)
	}
}

func TestConfigErrorsNameTheProblem(t *testing.T) {
	with := func(from, to string) string { return strings.Replace(defaultConfig, from, to, 1) }
	for _, c := range []struct{ text, error string }{
		{with("test_percent = 5\n", ""), "missing key `test_percent`"},
		{with("source_percent = 10\n", ""), "missing key `source_percent`"},
		{with("allowance_chars = 300\n", ""), "missing key `allowance_chars`"},
		{with("test_paths = [\"**/*_test.go\", \"**/testdata/**\"]\n", ""), "missing key `test_paths`"},
		{with("test_paths", "test_globs"), "unknown key `test_globs`"},
		{defaultConfig + "[extra]\nx = 1\n", "unknown key `extra`"},
		{with("= 10", "= \"10\""), "`source_percent` must be an integer"},
		{with("= 10", "= -1"), "`source_percent` must be an integer"},
		{with("= 300", "= 1.5"), "`allowance_chars` must be an integer"},
		{with("= 300", "= true"), "`allowance_chars` must be an integer"},
		{with("= 5", "= [\"5\"]"), "`test_percent` must be an integer"},
		{with("= [\"**/*_test.go\", ", "= [1, "), "`test_paths` must be a list of strings"},
		{with("= [\"**/*_test.go\", \"**/testdata/**\"]", "= \"**/*_test.go\""), "`test_paths` must be a list of strings"},
		{defaultConfig + "test_percent = 1\n", "test_percent"},
		{with("= 10", "= 10 11"), ""},
		{"source_percent", ""},
	} {
		_, err := parseConfig(c.text)
		if err == nil || !strings.Contains(err.Error(), c.error) {
			t.Errorf("%q: got %v, want an error containing %q", c.text, err, c.error)
		}
	}
}

func TestTestPathGlobsCrossDirectoriesOnlyWithDoubleStar(t *testing.T) {
	config, err := parseConfig(defaultConfig)
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"a_test.go", "pkg/a_test.go", "pkg/sub/b_test.go", "testdata/x.go", "pkg/testdata/sub/y.go"} {
		if !config.isTestPath(path) {
			t.Errorf("%s: not a test path", path)
		}
	}
	for _, path := range []string{"a.go", "pkg/test.go", "pkg/a_test.go.orig/b.go", "testdatax/a.go", "pkg/mytestdata/a.go"} {
		if config.isTestPath(path) {
			t.Errorf("%s: a test path", path)
		}
	}
	for _, c := range []struct {
		pattern, path string
		want          bool
	}{
		{"src/*.go", "src/a.go", true},
		{"src/*.go", "src/a/b.go", false},
		{"*.go", "a.go", true},
		{"*.go", "pkg/a.go", false},
		{"a/**/b.go", "a/b.go", true},
		{"a/**/b.go", "a/x/y/b.go", true},
		{"**", "a/b/c.go", true},
		{"*é*", "xéy", true},
		{"a*b*c", "abxbc", true},
		{"a*b*c", "abxbd", false},
		{"a.go", "a.go", true},
		{"a.go", "b/a.go", false},
	} {
		if got := glob(c.pattern, c.path); got != c.want {
			t.Errorf("glob(%q, %q) = %v, want %v", c.pattern, c.path, got, c.want)
		}
	}
}

func TestArgumentsHaveDefaultsAndOverrides(t *testing.T) {
	got, err := parseArgs(nil)
	if err != nil || got != (Options{base: "origin/main"}) {
		t.Errorf("defaults: got %+v, %v", got, err)
	}
	got, err = parseArgs(strings.Fields("--base HEAD --config c.toml --all"))
	if err != nil || got != (Options{base: "HEAD", config: "c.toml", all: true}) {
		t.Errorf("overrides: got %+v, %v", got, err)
	}
	for _, bad := range []string{"--base", "--config", "--source-limit 1", "--nope 1", "-all", "--base=x"} {
		if _, err := parseArgs(strings.Fields(bad)); err == nil {
			t.Errorf("%q: no error", bad)
		}
	}
}

type repo struct {
	t    *testing.T
	root string
}

func newRepo(t *testing.T) *repo {
	t.Helper()
	r := &repo{t, t.TempDir()}
	r.git("init", "-q", "-b", "main")
	r.write(configPath, defaultConfig)
	return r
}

func (r *repo) git(args ...string) {
	r.t.Helper()
	cmd := exec.Command("git", append([]string{"-c", "commit.gpgsign=false", "-C", r.root}, args...)...)
	if out, err := cmd.CombinedOutput(); err != nil {
		r.t.Fatalf("git %v: %v\n%s", args, err, out)
	}
}

func (r *repo) write(path, text string) {
	r.t.Helper()
	full := filepath.Join(r.root, path)
	if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
		r.t.Fatal(err)
	}
	if err := os.WriteFile(full, []byte(text), 0o644); err != nil {
		r.t.Fatal(err)
	}
}

func (r *repo) commit() {
	r.t.Helper()
	r.git("add", "-A")
	r.git("commit", "-q", "-m", "c")
}

func (r *repo) run(args string) (int, string, string) {
	var stdout, stderr bytes.Buffer
	code := exitCode(strings.Fields(args), r.root, &stdout, &stderr)
	return code, stdout.String(), stderr.String()
}

func heavy(n int) string {
	return "package a\n// " + strings.Repeat("y", n) + "\n"
}

func TestOnlyFilesChangedSinceTheMergeBaseAreChecked(t *testing.T) {
	r := newRepo(t)
	r.write("src/old.go", heavy(400))
	r.write("src/gone.go", heavy(400))
	r.commit()
	r.git("checkout", "-q", "-b", "pr")
	r.write("src/new.go", heavy(298))
	r.write("pkg/new_test.go", "package a\n")
	r.write("notes.md", "// "+strings.Repeat("z", 1000)+"\n")
	r.write("gen.go.txt", heavy(1000))
	r.git("rm", "-q", "src/gone.go")
	r.commit()
	r.git("checkout", "-q", "main")
	r.write("src/later.go", heavy(1000))
	r.commit()
	r.git("checkout", "-q", "pr")

	code, out, errs := r.run("--base main")
	if code != 0 || errs != "" {
		t.Fatalf("exit %d\n%s%s", code, out, errs)
	}
	want := "ok comment budget (source 10%, tests 5% of code + 300 characters per changed file): 2 checked\n" +
		"total source: 300 comment / 8 code characters (3750.00%)\n" +
		"total tests: 0 comment / 8 code characters (0.00%)\n"
	if out != want {
		t.Errorf("got:\n%s\nwant:\n%s", out, want)
	}

	r.write("src/new.go", heavy(301))
	code, out, _ = r.run("--base main")
	if code != 1 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	want = "FAIL comment budget (source 10%, tests 5% of code + 300 characters per changed file): 1 of 2 over budget\n" +
		"  src/new.go: 3787.50% (303 comment / 8 code characters, 0 in tests; 300 allowed, 3 over)\n" +
		"total source: 303 comment / 8 code characters (3787.50%)\n" +
		"total tests: 0 comment / 8 code characters (0.00%)\n" +
		purpose + "\n"
	if out != want {
		t.Errorf("got:\n%s\nwant:\n%s", out, want)
	}
}

func TestTestCodeRaisesTheBudgetByTestPercent(t *testing.T) {
	r := newRepo(t)
	r.commit()
	body := "package a\nvar x = \"" + strings.Repeat("c", 2000) + "\"\n"
	r.write("a_test.go", body+"// "+strings.Repeat("t", 398)+"\n")
	r.write("b.go", body+"// "+strings.Repeat("s", 499)+"\n")
	r.git("add", "-A")
	code, out, _ := r.run("--base main")
	if code != 0 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	for _, s := range []string{
		"total source: 501 comment / 2015 code characters (24.86%)\n",
		"total tests: 400 comment / 2015 code characters (19.85%)\n",
	} {
		if !strings.Contains(out, s) {
			t.Errorf("missing %q in:\n%s", s, out)
		}
	}
	r.write("a_test.go", body+"// "+strings.Repeat("t", 399)+"\n")
	code, out, _ = r.run("--base main")
	if code != 1 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	want := "1 of 2 over budget\n" +
		"  a_test.go: 19.90% (401 comment / 2015 code characters, 2015 in tests; 400 allowed, 1 over)\n" +
		"total source:"
	if !strings.Contains(out, want) {
		t.Errorf("missing %q in:\n%s", want, out)
	}
}

func TestDirectivesDoNotUseTheBudget(t *testing.T) {
	r := newRepo(t)
	r.commit()
	r.write("gen.go", "//go:generate "+strings.Repeat("g", 1000)+"\npackage a\n")
	r.git("add", "-A")
	code, out, _ := r.run("--base main")
	if code != 0 || !strings.Contains(out, "total source: 0 comment / 1021 code characters") {
		t.Errorf("exit %d\n%s", code, out)
	}
}

func TestAllChecksEveryTrackedFileAndReportsTotals(t *testing.T) {
	r := newRepo(t)
	r.write("src/old.go", heavy(400))
	r.write("pkg/a_test.go", "package a // ab\n")
	r.write("untracked/skip.go", "package a /* open\n")
	r.git("add", "src", "pkg", configPath)
	r.git("commit", "-q", "-m", "base")
	code, out, _ := r.run("--all")
	if code != 1 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	for _, s := range []string{
		"FAIL comment budget (source 10%, tests 5% of code + 300 characters per file): 1 of 2 over budget\n",
		"  src/old.go: ",
		"total source: 402 comment / 8 code characters (5025.00%)\n",
		"total tests: 4 comment / 8 code characters (50.00%)\n",
	} {
		if !strings.Contains(out, s) {
			t.Errorf("missing %q in:\n%s", s, out)
		}
	}
	code, out, _ = r.run("--base main")
	if code != 0 || !strings.Contains(out, ": 0 checked\n") {
		t.Errorf("exit %d\n%s", code, out)
	}
}

func TestTheConfigPathCanBeOverriddenAndMustExist(t *testing.T) {
	r := newRepo(t)
	r.write("a.go", "package a\n")
	r.write("sub/strict.toml", strings.Replace(defaultConfig, "= 300", "= 0", 1))
	r.git("add", "a.go")
	r.git("commit", "-q", "-m", "base")
	code, out, _ := r.run("--all --config sub/strict.toml")
	if code != 0 || !strings.Contains(out, "+ 0 characters per file") {
		t.Errorf("exit %d\n%s", code, out)
	}
	code, _, errs := r.run("--all --config missing.toml")
	if code != 2 || !strings.HasPrefix(errs, "comment-budget: config ") || !strings.Contains(errs, "missing.toml") {
		t.Errorf("exit %d: %s", code, errs)
	}
	os.Remove(filepath.Join(r.root, configPath))
	code, _, errs = r.run("--all")
	if code != 2 || !strings.Contains(errs, configPath) {
		t.Errorf("exit %d: %s", code, errs)
	}
}

func TestEachConfigErrorExitsTwo(t *testing.T) {
	r := newRepo(t)
	r.write("a.go", "package a\n")
	r.commit()
	with := func(from, to string) string { return strings.Replace(defaultConfig, from, to, 1) }
	for _, text := range []string{
		with("test_percent = 5\n", ""),
		with("test_paths", "test_globs"),
		with("= 10", "= \"10\""),
		with("= 10", "= -1"),
		with("= [\"**/*_test.go\", ", "= [1, "),
		"not toml",
	} {
		r.write(configPath, text)
		code, out, errs := r.run("--all")
		if code != 2 || out != "" || !strings.HasPrefix(errs, "comment-budget: config ") {
			t.Errorf("%q: exit %d\n%s%s", text, code, out, errs)
		}
	}
}

func TestABadBaseBadArgumentOrUnparsableFileExitsTwo(t *testing.T) {
	r := newRepo(t)
	r.write("a.go", "package a\n")
	r.commit()
	if code, _, errs := r.run("--base nope"); code != 2 || !strings.HasPrefix(errs, "comment-budget: git merge-base failed") {
		t.Errorf("bad base: exit %d: %s", code, errs)
	}
	if code, _, errs := r.run("--nope"); code != 2 || !strings.Contains(errs, "unknown argument --nope\n"+usage) {
		t.Errorf("bad argument: exit %d: %s", code, errs)
	}
	r.write("a.go", "package a\n/* open\n")
	code, out, errs := r.run("--all")
	if code != 2 || out != "" || !strings.HasPrefix(errs, "comment-budget: a.go: ") {
		t.Errorf("unparsable: exit %d\n%s%s", code, out, errs)
	}
}

func TestOutsideARepositoryIsAnError(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if code := exitCode([]string{"--all"}, t.TempDir(), &stdout, &stderr); code != 2 {
		t.Errorf("exit %d\n%s%s", code, stdout.String(), stderr.String())
	}
}
