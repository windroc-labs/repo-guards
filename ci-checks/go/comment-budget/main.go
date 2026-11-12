package main

import (
	"bytes"
	"errors"
	"fmt"
	"go/scanner"
	"go/token"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"unicode"

	"github.com/BurntSushi/toml"
)

const (
	configPath = ".github/checks/go/comment-budget.toml"
	purpose    = "The comment budget keeps comments short: meet it by cutting comments, " +
		"never by adding code to lower the ratio."
	usage = "usage: comment-budget [--base REF] [--config PATH] [--all]"
)

var directives = []string{"//go:", "//line ", "//export ", "//extern "}

type Counts struct {
	comment, code int64
}

func (c Counts) add(o Counts) Counts {
	return Counts{c.comment + o.comment, c.code + o.code}
}

type Measure struct {
	source, tests Counts
}

func chars(text string) int64 {
	var n int64
	for _, r := range text {
		if !unicode.IsSpace(r) {
			n++
		}
	}
	return n
}

func isDirective(comment string) bool {
	for _, d := range directives {
		if strings.HasPrefix(comment, d) {
			return true
		}
	}
	return false
}

func measure(src []byte, wholeFileIsTest bool) (Measure, error) {
	var errs scanner.ErrorList
	var s scanner.Scanner
	file := token.NewFileSet().AddFile("", -1, len(src))
	s.Init(file, src, errs.Add, scanner.ScanComments)
	var comment int64
	for {
		_, tok, lit := s.Scan()
		if tok == token.EOF {
			break
		}
		if tok == token.COMMENT && !isDirective(lit) {
			comment += chars(lit)
		}
	}
	if err := errs.Err(); err != nil {
		return Measure{}, err
	}
	counts := Counts{comment, chars(string(src)) - comment}
	if wholeFileIsTest {
		return Measure{tests: counts}, nil
	}
	return Measure{source: counts}, nil
}

func glob(pattern, path string) bool {
	return segments(strings.Split(pattern, "/"), strings.Split(path, "/"))
}

func segments(pattern, path []string) bool {
	if len(pattern) == 0 {
		return len(path) == 0
	}
	if pattern[0] == "**" {
		for k := 0; k <= len(path); k++ {
			if segments(pattern[1:], path[k:]) {
				return true
			}
		}
		return false
	}
	return len(path) > 0 && segment(pattern[0], path[0]) && segments(pattern[1:], path[1:])
}

func segment(pattern, s string) bool {
	head, tail, star := strings.Cut(pattern, "*")
	if !star {
		return pattern == s
	}
	rest, ok := strings.CutPrefix(s, head)
	if !ok {
		return false
	}
	for k := range rest {
		if segment(tail, rest[k:]) {
			return true
		}
	}
	return segment(tail, "")
}

type Config struct {
	sourcePercent, testPercent, allowanceChars int64
	testPaths                                  []string
}

func (c Config) isTestPath(path string) bool {
	for _, p := range c.testPaths {
		if glob(p, path) {
			return true
		}
	}
	return false
}

func parseConfig(text string) (Config, error) {
	var raw struct {
		SourcePercent  any `toml:"source_percent"`
		TestPercent    any `toml:"test_percent"`
		AllowanceChars any `toml:"allowance_chars"`
		TestPaths      any `toml:"test_paths"`
	}
	meta, err := toml.Decode(text, &raw)
	if err != nil {
		return Config{}, err
	}
	if unknown := meta.Undecoded(); len(unknown) > 0 {
		return Config{}, fmt.Errorf("unknown key `%s`", unknown[0])
	}
	values := []struct {
		key   string
		value any
	}{
		{"source_percent", raw.SourcePercent},
		{"test_percent", raw.TestPercent},
		{"allowance_chars", raw.AllowanceChars},
		{"test_paths", raw.TestPaths},
	}
	for _, v := range values {
		if v.value == nil {
			return Config{}, fmt.Errorf("missing key `%s`", v.key)
		}
	}
	var numbers [3]int64
	for i, v := range values[:3] {
		n, ok := v.value.(int64)
		if !ok || n < 0 {
			return Config{}, fmt.Errorf("`%s` must be an integer", v.key)
		}
		numbers[i] = n
	}
	list, ok := raw.TestPaths.([]any)
	paths := make([]string, len(list))
	for i, p := range list {
		paths[i], ok = p.(string)
		if !ok {
			break
		}
	}
	if !ok {
		return Config{}, errors.New("`test_paths` must be a list of strings")
	}
	return Config{numbers[0], numbers[1], numbers[2], paths}, nil
}

type Options struct {
	base, config string
	all          bool
}

func parseArgs(args []string) (Options, error) {
	options := Options{base: "origin/main"}
	for i := 0; i < len(args); i++ {
		flag := args[i]
		if flag == "--all" {
			options.all = true
			continue
		}
		if flag != "--base" && flag != "--config" {
			return Options{}, fmt.Errorf("unknown argument %s\n%s", flag, usage)
		}
		if i+1 == len(args) {
			return Options{}, fmt.Errorf("%s needs a value", flag)
		}
		i++
		if flag == "--base" {
			options.base = args[i]
		} else {
			options.config = args[i]
		}
	}
	return options, nil
}

func git(root string, args ...string) (string, error) {
	cmd := exec.Command("git", append([]string{
		"-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", root,
	}, args...)...)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		reason := strings.TrimSpace(stderr.String())
		if exit := (*exec.ExitError)(nil); !errors.As(err, &exit) {
			reason = err.Error()
		}
		return "", fmt.Errorf("git %s failed: %s", args[0], reason)
	}
	return string(out), nil
}

func files(root string, options Options) ([]string, error) {
	var listed string
	var err error
	if options.all {
		listed, err = git(root, "ls-files", "-z", "--", "*.go")
	} else {
		var fork string
		if fork, err = git(root, "merge-base", options.base, "HEAD"); err != nil {
			return nil, err
		}
		listed, err = git(root, "diff", "--name-only", "--no-renames", "--diff-filter=d", "-z",
			strings.TrimSpace(fork), "--")
	}
	if err != nil {
		return nil, err
	}
	var paths []string
	for _, p := range strings.Split(listed, "\x00") {
		if !strings.HasSuffix(p, ".go") {
			continue
		}
		if info, err := os.Stat(filepath.Join(root, p)); err == nil && info.Mode().IsRegular() {
			paths = append(paths, p)
		}
	}
	sort.Strings(paths)
	return paths, nil
}

func percent(n, code int64) string {
	switch {
	case code > 0:
		return fmt.Sprintf("%.2f%%", float64(n)*100/float64(code))
	case n > 0:
		return "inf"
	default:
		return "0.00%"
	}
}

type measured struct {
	path string
	Measure
}

func run(args []string, cwd string, out io.Writer) (bool, error) {
	options, err := parseArgs(args)
	if err != nil {
		return false, err
	}
	root, err := git(cwd, "rev-parse", "--show-toplevel")
	if err != nil {
		return false, err
	}
	root = strings.TrimSpace(root)
	path := filepath.Join(root, configPath)
	if filepath.IsAbs(options.config) {
		path = options.config
	} else if options.config != "" {
		path = filepath.Join(cwd, options.config)
	}
	text, err := os.ReadFile(path)
	if err != nil {
		return false, fmt.Errorf("config %s: %w", path, err)
	}
	config, err := parseConfig(string(text))
	if err != nil {
		return false, fmt.Errorf("config %s: %w", path, err)
	}
	paths, err := files(root, options)
	if err != nil {
		return false, err
	}
	var all []measured
	for _, p := range paths {
		src, err := os.ReadFile(filepath.Join(root, p))
		if err != nil {
			return false, fmt.Errorf("%s: %w", p, err)
		}
		m, err := measure(src, config.isTestPath(p))
		if err != nil {
			return false, fmt.Errorf("%s: %w", p, err)
		}
		all = append(all, measured{p, m})
	}
	allowance := config.allowanceChars
	limit := func(m Measure) int64 {
		return config.sourcePercent*m.source.code + config.testPercent*m.tests.code
	}
	var over []measured
	for _, m := range all {
		if (m.source.comment+m.tests.comment)*100 > limit(m.Measure)+allowance*100 {
			over = append(over, m)
		}
	}
	scope := "changed file"
	if options.all {
		scope = "file"
	}
	name := fmt.Sprintf("comment budget (source %d%%, tests %d%% of code + %d characters per %s)",
		config.sourcePercent, config.testPercent, allowance, scope)
	var b strings.Builder
	if len(over) == 0 {
		fmt.Fprintf(&b, "ok %s: %d checked\n", name, len(all))
	} else {
		fmt.Fprintf(&b, "FAIL %s: %d of %d over budget\n", name, len(over), len(all))
		for _, m := range over {
			n, code := m.source.comment+m.tests.comment, m.source.code+m.tests.code
			allowed := limit(m.Measure)/100 + allowance
			fmt.Fprintf(&b, "  %s: %s (%d comment / %d code characters, %d in tests; %d allowed, %d over)\n",
				m.path, percent(n, code), n, code, m.tests.code, allowed, n-allowed)
		}
	}
	var total Measure
	for _, m := range all {
		total = Measure{total.source.add(m.source), total.tests.add(m.tests)}
	}
	for _, t := range []struct {
		label string
		c     Counts
	}{{"source", total.source}, {"tests", total.tests}} {
		fmt.Fprintf(&b, "total %s: %d comment / %d code characters (%s)\n",
			t.label, t.c.comment, t.c.code, percent(t.c.comment, t.c.code))
	}
	if len(over) > 0 {
		fmt.Fprintln(&b, purpose)
	}
	if _, err := io.WriteString(out, b.String()); err != nil {
		return false, err
	}
	return len(over) == 0, nil
}

func exitCode(args []string, cwd string, stdout, stderr io.Writer) int {
	ok, err := run(args, cwd, stdout)
	switch {
	case err != nil:
		fmt.Fprintf(stderr, "comment-budget: %v\n", err)
		return 2
	case !ok:
		return 1
	default:
		return 0
	}
}

func main() {
	os.Exit(exitCode(os.Args[1:], ".", os.Stdout, os.Stderr))
}
