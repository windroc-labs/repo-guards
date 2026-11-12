package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
)

func sorted(list []Occurrence) []Occurrence {
	out := append([]Occurrence{}, list...)
	sort.Slice(out, func(i, j int) bool {
		a, b := out[i], out[j]
		return strings.Join([]string{a.File, a.Function, a.Tool, a.Rule}, "\x00") <
			strings.Join([]string{b.File, b.Function, b.Tool, b.Rule}, "\x00")
	})
	return out
}

func occ(tool, file, rule, function string) Occurrence {
	return Occurrence{Tool: tool, File: file, Rule: rule, Function: function}
}

func checkGo(t *testing.T, file, src string, want ...Occurrence) {
	t.Helper()
	got, err := scanGo(file, []byte(src))
	if err != nil {
		t.Fatalf("scanGo(%s): %v", file, err)
	}
	if want == nil {
		want = []Occurrence{}
	}
	if got == nil {
		got = []Occurrence{}
	}
	if g, w := sorted(got), sorted(want); !reflect.DeepEqual(g, w) {
		t.Errorf("%s:\n got %+v\nwant %+v", file, g, w)
	}
}

func method(body string) string {
	return "package a\n\ntype T struct{}\n\nfunc (t *T) Run() {\n" + body + "\n}\n"
}

func TestGoForms(t *testing.T) {
	for _, c := range []struct {
		name, file, src string
		want            []Occurrence
	}{
		{"S-40 nolint with linter and explanation", "pkg/a.go",
			method("\tx := f() //nolint:errcheck // 说明\n\t_ = x"),
			[]Occurrence{occ("golangci-lint", "pkg/a.go", "errcheck", "T.Run")}},
		{"S-40 bare nolint", "pkg/a.go",
			method("\ty := g() //nolint\n\t_ = y"),
			[]Occurrence{occ("golangci-lint", "pkg/a.go", "*", "T.Run")}},
		{"S-40 t.Skip", "pkg/a.go", method("\tt.Skip(\"flaky\")"),
			[]Occurrence{occ("go-test", "pkg/a.go", "Skip", "T.Run")}},
		{"S-40 t.SkipNow", "pkg/a.go", method("\tt.SkipNow()"),
			[]Occurrence{occ("go-test", "pkg/a.go", "SkipNow", "T.Run")}},
		{"Skipf on any receiver in a non-test file", "pkg/a.go", method("\tsuite.Helper().Skipf(\"x %d\", 1)"),
			[]Occurrence{occ("go-test", "pkg/a.go", "Skipf", "T.Run")}},
		{"a bare Skip call is not a test skip", "pkg/a.go", method("\tSkip()\n\tt.Skipped()"), nil},
		{"S-41 whole-file nolint and pointer generic receiver", "pkg/b.go",
			"//nolint:all\npackage b\n\ntype Srv[K comparable] struct{}\n\n" +
				"func (s *Srv[K]) Stop() {\n\t_ = s //nolint:gosec\n}\n",
			[]Occurrence{
				occ("golangci-lint", "pkg/b.go", "*", ""),
				occ("golangci-lint", "pkg/b.go", "gosec", "Srv.Stop"),
			}},
		{"method on a non-pointer receiver", "pkg/a.go",
			"package a\n\ntype T struct{}\n\nfunc (T) Run() {\n\tx() //nolint:unused\n}\n",
			[]Occurrence{occ("golangci-lint", "pkg/a.go", "unused", "T.Run")}},
		{"receiver with two type parameters", "pkg/a.go",
			"package a\n\nfunc (m Map[K, V]) Get() {\n\tt.Skip()\n}\n",
			[]Occurrence{occ("go-test", "pkg/a.go", "Skip", "Map.Get")}},
		{"generic function", "pkg/a.go",
			"package a\n\nfunc F[T any]() {\n\tt.Skip()\n}\n",
			[]Occurrence{occ("go-test", "pkg/a.go", "Skip", "F")}},
		{"func literal inside a method counts as the method", "pkg/a.go",
			method("\tgo func() {\n\t\tdefer func() { t.SkipNow() }()\n\t\tx() //nolint:errcheck\n\t}()"),
			[]Occurrence{
				occ("go-test", "pkg/a.go", "SkipNow", "T.Run"),
				occ("golangci-lint", "pkg/a.go", "errcheck", "T.Run"),
			}},
		{"func literal at package level has no function", "pkg/a.go",
			"package a\n\nvar h = func() {\n\tt.Skip()\n\tx() //nolint:errcheck\n}\n",
			[]Occurrence{
				occ("go-test", "pkg/a.go", "Skip", ""),
				occ("golangci-lint", "pkg/a.go", "errcheck", ""),
			}},
		{"FR-14 one record per linter", "pkg/a.go",
			"package a\n\nfunc F() {\n\tx() //nolint:errcheck,gosec\n}\n",
			[]Occurrence{
				occ("golangci-lint", "pkg/a.go", "errcheck", "F"),
				occ("golangci-lint", "pkg/a.go", "gosec", "F"),
			}},
		{"FR-15 a repeated key is recorded each time", "pkg/a.go",
			"package a\n\nfunc F() {\n\tx() //nolint:errcheck\n\tx() //nolint:errcheck\n\tx() //nolint:errcheck\n}\n",
			[]Occurrence{
				occ("golangci-lint", "pkg/a.go", "errcheck", "F"),
				occ("golangci-lint", "pkg/a.go", "errcheck", "F"),
				occ("golangci-lint", "pkg/a.go", "errcheck", "F"),
			}},
		{"nolint on the line above a func belongs to it", "pkg/a.go",
			"package a\n\n//nolint:funlen\nfunc F() {}\n",
			[]Occurrence{occ("golangci-lint", "pkg/a.go", "funlen", "F")}},
		{"nolint in a doc comment run belongs to the func", "pkg/a.go",
			"package a\n\n// F does x.\n//nolint:gocyclo // long\n/* more */\nfunc F() {}\n",
			[]Occurrence{occ("golangci-lint", "pkg/a.go", "gocyclo", "F")}},
		{"nolint above a func with a blank line between has no function", "pkg/a.go",
			"package a\n\n//nolint:funlen\n\nfunc F() {}\n",
			[]Occurrence{occ("golangci-lint", "pkg/a.go", "funlen", "")}},
		{"a trailing comment on the line above a func is not its doc", "pkg/a.go",
			"package a\n\nvar x = 1 //nolint:gochecknoglobals\nfunc F() {}\n",
			[]Occurrence{occ("golangci-lint", "pkg/a.go", "gochecknoglobals", "")}},
		{"nolint on a type declaration has no function", "pkg/a.go",
			"package a\n\ntype T struct {\n\tf int //nolint:unused\n}\n",
			[]Occurrence{occ("golangci-lint", "pkg/a.go", "unused", "")}},
		{"nolint:all inside a function", "pkg/a.go",
			"package a\n\nfunc F() {\n\tx() //nolint:all\n}\n",
			[]Occurrence{occ("golangci-lint", "pkg/a.go", "*", "F")}},
		{"nolint followed by a space and explanation", "pkg/a.go",
			"package a\n\nfunc F() {\n\tx() //nolint // why\n}\n",
			[]Occurrence{occ("golangci-lint", "pkg/a.go", "*", "F")}},
		{"nolint with a space after the slashes does not count", "pkg/a.go",
			"package a\n\nfunc F() {\n\tx() // nolint\n\tx() // nolint:errcheck\n}\n", nil},
		{"nolint in a block comment does not count", "pkg/a.go",
			"package a\n\nfunc F() {\n\tx() /*nolint*/\n\tx() /*nolint:errcheck*/\n}\n", nil},
		{"nolint glued to other text does not count", "pkg/a.go",
			"package a\n\nfunc F() {\n\tx() //nolintfoo\n\tx() // see //nolint\n}\n", nil},
		{"S-29 nolint in a raw and an interpreted string", "pkg/a.go",
			"package a\n\nvar r = `//nolint`\nvar s = \"//nolint:errcheck\"\nvar u = \"t.Skip() // jscpd:ignore-start\"\n",
			nil},
		{"S-14 #nosec does not count", "pkg/a.go",
			method("\tx := f() // #nosec G115\n\t_ = x"), nil},
		{"S-42 go:build linux", "pkg/c_linux.go", "//go:build linux\n\npackage c\n",
			[]Occurrence{occ("go-build", "pkg/c_linux.go", "linux", "")}},
		{"S-42 go:build integration", "pkg/it_test.go", "//go:build integration\n\npackage c\n",
			[]Occurrence{occ("go-build", "pkg/it_test.go", "integration", "")}},
		{"go:build and +build lines with their expression text", "pkg/d.go",
			"//go:build  (linux && amd64) || darwin \n// +build linux,amd64 darwin\n\npackage d\n",
			[]Occurrence{
				occ("go-build", "pkg/d.go", "(linux && amd64) || darwin", ""),
				occ("go-build", "pkg/d.go", "linux,amd64 darwin", ""),
			}},
		{"go:build look-alikes do not count", "pkg/d.go",
			"// go:build linux\n//go:buildx linux\n/*go:build linux*/\n\npackage d\n", nil},
		{"S-44 Go generated header", "api/x.pb.go",
			"// Code generated by protoc-gen-go. DO NOT EDIT.\n\npackage api\n",
			[]Occurrence{occ("generated", "api/x.pb.go", "generated", "")}},
		{"generated header after a build line, at most one per file", "api/y.go",
			"//go:build tools\n\n// Code generated by x. DO NOT EDIT.\n// @generated\n\npackage api\n",
			[]Occurrence{
				occ("go-build", "api/y.go", "tools", ""),
				occ("generated", "api/y.go", "generated", ""),
			}},
		{"protobuf generated header", "api/z.go",
			"// Generated by the protocol buffer compiler.  DO NOT EDIT!\npackage api\n",
			[]Occurrence{occ("generated", "api/z.go", "generated", "")}},
		{"a generated marker after package is no header", "api/w.go",
			"package api\n\n// Code generated by x. DO NOT EDIT.\n// @generated\n", nil},
		{"S-10 jscpd ignore in function f", "pkg/a.go",
			"package a\n\nfunc f() {\n\t// jscpd:ignore-start\n\tx()\n\t// jscpd:ignore-end\n}\n",
			[]Occurrence{occ("jscpd", "pkg/a.go", "ignore", "f")}},
		{"jscpd outside any function", "pkg/a.go",
			"package a\n\n/* jscpd:ignore-start */\nvar x = 1\n\n// jscpd:ignore-end\n",
			[]Occurrence{occ("jscpd", "pkg/a.go", "ignore", "")}},
	} {
		t.Run(c.name, func(t *testing.T) { checkGo(t, c.file, c.src, c.want...) })
	}
}

func TestUnreadableGoFilesAreErrors(t *testing.T) {
	for _, c := range []struct{ name, src string }{
		{"S-23 pkg/v.go syntax error", "package v\n\nfunc F( {\n"},
		{"not UTF-8", "package v\n\n// \xff\n"},
		{"no package clause", "func F() {}\n"},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got, err := scanGo("pkg/v.go", []byte(c.src)); err == nil {
				t.Errorf("no error, got %+v", got)
			}
		})
	}
}

func TestGoMod(t *testing.T) {
	for _, c := range []struct {
		name, src string
		nested    bool
		want      []Occurrence
	}{
		{"S-43 single ignore", "module m\n\ngo 1.27\n\nignore ./scratch\n", false,
			[]Occurrence{occ("go-mod", "go.mod", "./scratch", "")}},
		{"block ignore", "module m\n\ngo 1.27\n\nignore (\n\t./a\n\tnode_modules\n\t\"b c\"\n)\n", false,
			[]Occurrence{
				occ("go-mod", "go.mod", "./a", ""),
				occ("go-mod", "go.mod", "node_modules", ""),
				occ("go-mod", "go.mod", "b c", ""),
			}},
		{"nested module", "module m\n\ngo 1.27\n", true,
			[]Occurrence{occ("go-mod", "go.mod", "module", "")}},
		{"plain module", "module m\n\ngo 1.27\n\nrequire golang.org/x/mod v0.43.0\n", false, nil},
	} {
		t.Run(c.name, func(t *testing.T) {
			got, err := scanGoMod("go.mod", []byte(c.src), c.nested)
			if err != nil {
				t.Fatal(err)
			}
			if c.want == nil {
				c.want = []Occurrence{}
			}
			if got == nil {
				got = []Occurrence{}
			}
			if !reflect.DeepEqual(sorted(got), sorted(c.want)) {
				t.Errorf("got %+v, want %+v", got, c.want)
			}
		})
	}
	for _, src := range []string{"module m\nrequire (\n", "module m\nignore a b\n", "module m\nignore \"a\n", "module m\n// \xff\n"} {
		if got, err := scanGoMod("go.mod", []byte(src), false); err == nil {
			t.Errorf("%q: no error, got %+v", src, got)
		}
	}
}

func TestNestedModules(t *testing.T) {
	for _, c := range []struct {
		name  string
		paths []string
		want  []string
	}{
		{"S-43 root and tools", []string{"go.mod", "tools/go.mod"}, []string{"tools/go.mod"}},
		{"no root module", []string{"a/go.mod", "a/b/c/go.mod", "ab/go.mod", "b/go.mod"}, []string{"a/b/c/go.mod"}},
		{"single module", []string{"x/go.mod"}, nil},
	} {
		t.Run(c.name, func(t *testing.T) {
			got := nestedModules(c.paths)
			var names []string
			for p := range got {
				names = append(names, p)
			}
			sort.Strings(names)
			if !reflect.DeepEqual(names, c.want) {
				t.Errorf("got %v, want %v", names, c.want)
			}
		})
	}
}

func writeTree(t *testing.T, files map[string]string) string {
	t.Helper()
	root := t.TempDir()
	for name, text := range files {
		path := filepath.Join(root, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func TestProtocolEndToEnd(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "pwned")
	root := writeTree(t, map[string]string{
		"go.mod":       "module m\n\ngo 1.27\n\nignore ./scratch\n",
		"tools/go.mod": "module m/tools\n\ngo 1.27\n",
		"tools/gen.go": "package main\n\nimport \"os\"\n\nfunc init() {\n\t_ = os.WriteFile(" +
			strconv.Quote(marker) + ", nil, 0o644)\n}\n\nfunc main() {}\n",
		"pkg/a.go": method("\tt.Skip(\"flaky\")"),
		"pkg/v.go": "package v\n\nfunc F( {\n",
	})
	stdin := strings.NewReader("go.mod\x00tools/go.mod\x00tools/gen.go\x00pkg/v.go\x00pkg/a.go\x00")
	var stdout, stderr bytes.Buffer
	if code := run([]string{root}, stdin, &stdout, &stderr); code != 0 {
		t.Fatalf("exit %d, stderr %q", code, stderr.String())
	}
	var got []Occurrence
	var errored []string
	for _, line := range strings.Split(strings.TrimSuffix(stdout.String(), "\n"), "\n") {
		var record map[string]string
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			t.Fatalf("line %q: %v", line, err)
		}
		if _, ok := record["error"]; ok {
			if len(record) != 2 || record["file"] == "" || record["error"] == "" {
				t.Errorf("bad error record %q", line)
			}
			errored = append(errored, record["file"])
			continue
		}
		if len(record) != 4 {
			t.Errorf("record %q has %d keys, want 4", line, len(record))
		}
		got = append(got, occ(record["tool"], record["file"], record["rule"], record["function"]))
	}
	want := []Occurrence{
		occ("go-mod", "go.mod", "./scratch", ""),
		occ("go-mod", "tools/go.mod", "module", ""),
		occ("go-test", "pkg/a.go", "Skip", "T.Run"),
	}
	if !reflect.DeepEqual(sorted(got), sorted(want)) {
		t.Errorf("got %+v, want %+v", got, want)
	}
	if !reflect.DeepEqual(errored, []string{"pkg/v.go"}) {
		t.Errorf("S-23 error records for %v, want [pkg/v.go]", errored)
	}
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Errorf("S-34 tools/gen.go init ran: marker stat err = %v", err)
	}
}

func TestProtocolWithoutRootFails(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if code := run(nil, strings.NewReader(""), &stdout, &stderr); code != 2 {
		t.Errorf("exit %d, want 2", code)
	}
	if stdout.Len() != 0 || stderr.Len() == 0 {
		t.Errorf("stdout %q, stderr %q", stdout.String(), stderr.String())
	}
}

func TestProtocolMissingFileIsAnErrorRecord(t *testing.T) {
	root := t.TempDir()
	var stdout, stderr bytes.Buffer
	if code := run([]string{root}, strings.NewReader("gone.go"), &stdout, &stderr); code != 0 {
		t.Fatalf("exit %d", code)
	}
	var record map[string]string
	if err := json.Unmarshal(stdout.Bytes(), &record); err != nil || record["file"] != "gone.go" || record["error"] == "" {
		t.Errorf("got %q, %v", stdout.String(), err)
	}
}
