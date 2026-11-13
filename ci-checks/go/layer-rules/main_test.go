package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

type result struct {
	records []map[string]any
	stderr  string
	code    int
}

// scan writes files under a temporary side root and runs the scanner on scan, as layer_rules.py does.
func scan(t *testing.T, files map[string]string, scan ...string) result {
	t.Helper()
	root := t.TempDir()
	names, context := []string{}, []string{}
	for name, text := range files {
		target := filepath.Join(root, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(target, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
		if strings.HasSuffix(name, ".go") {
			names = append(names, name)
		} else if filepath.Base(name) == "go.mod" {
			context = append(context, name)
		}
	}
	slices.Sort(names)
	slices.Sort(context)
	return send(t, root, map[string]any{"files": names, "scan": scan, "context": context, "settings": map[string]any{}})
}

func send(t *testing.T, root string, req map[string]any) result {
	t.Helper()
	data, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	var stdout, stderr bytes.Buffer
	code := run([]string{root}, bytes.NewReader(data), &stdout, &stderr)
	var records []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(stdout.String()), "\n") {
		if line == "" {
			continue
		}
		var record map[string]any
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			t.Fatalf("not a JSON line: %q", line)
		}
		records = append(records, record)
	}
	return result{records, stderr.String(), code}
}

func ok(t *testing.T, r result) []string {
	t.Helper()
	if r.code != 0 {
		t.Fatalf("exit %d: %s", r.code, r.stderr)
	}
	var out []string
	for _, record := range r.records {
		line, _ := json.Marshal(record)
		out = append(out, string(line))
	}
	return out
}

func TestResolvesModulePathsToPackageDirectories(t *testing.T) {
	files := map[string]string{
		"go.mod":          "module example.com/m\n\ngo 1.27\n",
		"main.go":         "package main\n",
		"core/core.go":    "package core\n",
		"core/inner/x.go": "package inner\n",
		"api/a.go": `package api

import (
	"fmt"
	"github.com/other/pkg"
	"example.com/m/core/inner"
	c "example.com/m/core"
	. "example.com/m/core"
	_ "example.com/m"
	"example.com/m/core/missing"
	"example.com/mx/core"
)

import "example.com/m/core"
`,
	}
	got := ok(t, scan(t, files, "api/a.go"))
	want := []string{
		`{"file":"api/a.go","line":6,"targets":["core/inner"]}`,
		`{"file":"api/a.go","line":7,"targets":["core"]}`,
		`{"file":"api/a.go","line":8,"targets":["core"]}`,
		`{"file":"api/a.go","line":9,"targets":[""]}`,
		`{"file":"api/a.go","line":14,"targets":["core"]}`,
	}
	if !slices.Equal(got, want) {
		t.Fatalf("got\n%s\nwant\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
}

func TestNestedModules(t *testing.T) {
	files := map[string]string{
		"go.mod":       "module example.com/m // the outer module\n",
		"lib/go.mod":   "module \"example.com/m/lib\"\n",
		"lib/x/x.go":   "package x\n",
		"other/go.mod": "module example.com/other\n",
		"other/y/y.go": "package y\n",
		"a/go.mod":     "module example.com/m\n", // same path as the root module: the shallower go.mod wins
		"api/a.go":     "package api\n\nimport (\n\t\"example.com/m/lib/x\"\n\t\"example.com/m/other/y\"\n\t\"example.com/other/y\"\n)\n",
	}
	got := ok(t, scan(t, files, "api/a.go"))
	want := []string{
		`{"file":"api/a.go","line":4,"targets":["lib/x"]}`,
		`{"file":"api/a.go","line":6,"targets":["other/y"]}`,
	}
	if !slices.Equal(got, want) {
		t.Fatalf("got\n%s\nwant\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
}

func TestModuleInASubdirectoryOfTheSide(t *testing.T) {
	files := map[string]string{
		"svc/go.mod":    "module example.com/svc\n",
		"svc/core/c.go": "package core\n",
		"svc/api/a.go":  "package api\n\nimport \"example.com/svc/core\"\n",
		"tools/t.go":    "package tools\n\nimport \"example.com/svc/core\"\n",
	}
	got := ok(t, scan(t, files, "svc/api/a.go", "tools/t.go"))
	// The importing file's own module does not matter: any module of the side resolves the path.
	want := []string{
		`{"file":"svc/api/a.go","line":3,"targets":["svc/core"]}`,
		`{"file":"tools/t.go","line":3,"targets":["svc/core"]}`,
	}
	if !slices.Equal(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestParseErrorIsARecord(t *testing.T) {
	files := map[string]string{"go.mod": "module m\n", "bad.go": "package bad\n\nimport (\n"}
	r := scan(t, files, "bad.go")
	if r.code != 0 || len(r.records) != 1 || r.records[0]["file"] != "bad.go" || r.records[0]["error"] == nil {
		t.Fatalf("got exit %d, %v, %s", r.code, r.records, r.stderr)
	}
	if len(r.records[0]) != 2 || strings.Contains(r.records[0]["error"].(string), "\n") {
		t.Fatalf("want {file, error} with a one-line error: %v", r.records[0])
	}
}

func TestNoGoModResolvesNothing(t *testing.T) {
	files := map[string]string{"core/c.go": "package core\n", "api/a.go": "package api\n\nimport \"core\"\n"}
	if got := ok(t, scan(t, files, "api/a.go")); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
}

func TestBadRequests(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "go.mod"), []byte("go 1.27\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	cases := map[string]map[string]any{
		"absolute path":        {"files": []string{"/a.go"}, "scan": []string{}, "context": []string{}, "settings": map[string]any{}},
		"dot dot":              {"files": []string{}, "scan": []string{"../a.go"}, "context": []string{}, "settings": map[string]any{}},
		"missing scan":         {"files": []string{}, "context": []string{}, "settings": map[string]any{}},
		"inner dot dot":        {"files": []string{}, "scan": []string{"a/../../b.go"}, "context": []string{}, "settings": map[string]any{}},
		"dot dot in files":     {"files": []string{"a/../b.go"}, "scan": []string{}, "context": []string{}, "settings": map[string]any{}},
		"dot dot in context":   {"files": []string{}, "scan": []string{}, "context": []string{"../go.mod"}, "settings": map[string]any{}},
		"missing go.mod":       {"files": []string{}, "scan": []string{}, "context": []string{"x/go.mod"}, "settings": map[string]any{}},
		"go.mod no module":     {"files": []string{}, "scan": []string{}, "context": []string{"go.mod"}, "settings": map[string]any{}},
		"unreadable scan file": {"files": []string{"a.go"}, "scan": []string{"a.go"}, "context": []string{}, "settings": map[string]any{}},
	}
	for name, req := range cases {
		t.Run(name, func(t *testing.T) {
			if r := send(t, root, req); r.code != 2 || r.stderr == "" {
				t.Fatalf("got exit %d, stderr %q", r.code, r.stderr)
			}
		})
	}
	var stdout, stderr bytes.Buffer
	if code := run(nil, strings.NewReader("{}"), &stdout, &stderr); code != 2 || !strings.Contains(stderr.String(), "usage") {
		t.Fatalf("no root: exit %d, %q", code, stderr.String())
	}
}

func TestModulePath(t *testing.T) {
	cases := map[string]string{
		"module example.com/m\n":                "example.com/m",
		"// c\nmodule   example.com/m   // c\n": "example.com/m",
		"module \"example.com/q\"\r\n":          "example.com/q",
		"module `example.com/r`\n":              "example.com/r",
		"go 1.27\nrequire x v1\n":               "",
		"modules example.com/m\n":               "",
	}
	for text, want := range cases {
		if got := modulePath(text); got != want {
			t.Errorf("modulePath(%q) = %q, want %q", text, got, want)
		}
	}
}
