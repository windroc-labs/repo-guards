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
	stdout, stderr string
	code           int
}

// runIn runs the program in dir, as go.yml runs it in the working directory.
func runIn(t *testing.T, dir string, args ...string) result {
	t.Helper()
	t.Setenv("GOWORK", "off")
	t.Setenv("GOFLAGS", "")
	t.Chdir(dir)
	var stdout, stderr bytes.Buffer
	code := run(args, &stdout, &stderr)
	return result{stdout.String(), stderr.String(), code}
}

func entriesOf(t *testing.T, dir string) []entry {
	t.Helper()
	r := runIn(t, dir)
	if r.code != 0 {
		t.Fatalf("exit %d, stderr:\n%s", r.code, r.stderr)
	}
	var entries []entry
	if err := json.Unmarshal([]byte(r.stdout), &entries); err != nil {
		t.Fatalf("output is not an entry array: %v\n%s", err, r.stdout)
	}
	return entries
}

func fixture(t *testing.T, name string) string {
	t.Helper()
	dir, err := filepath.Abs(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return dir
}

func find(entries []entry, unit, parent, kind, name string) (entry, bool) {
	for _, e := range entries {
		if e.Unit == unit && e.Parent == parent && e.Kind == kind && e.Name == name {
			return e, true
		}
	}
	return entry{}, false
}

func names(entries []entry) []string {
	var out []string
	for _, e := range entries {
		out = append(out, e.Name)
	}
	return out
}

func TestPublicFuncEntry(t *testing.T) {
	entries := entriesOf(t, fixture(t, "basic"))
	want := entry{Unit: "example.com/basic", Parent: "", Kind: "func", Name: "Open", Signature: "func Open(string) (*File, error)"}
	got, ok := find(entries, want.Unit, want.Parent, want.Kind, want.Name)
	if !ok || got != want {
		t.Errorf("Open entry = %+v (found %v), want %+v", got, ok, want)
	}
	if slices.Contains(names(entries), "helper") {
		t.Errorf("unexported helper is an entry: %v", names(entries))
	}
}

func TestSortedByKey(t *testing.T) {
	entries := entriesOf(t, fixture(t, "basic"))
	sorted := slices.IsSortedFunc(entries, func(a, b entry) int {
		return slices.Compare([]string{a.Unit, a.Parent, a.Kind, a.Name}, []string{b.Unit, b.Parent, b.Kind, b.Name})
	})
	if !sorted || len(entries) < 2 {
		t.Errorf("entries not sorted by (unit, parent, kind, name): %+v", entries)
	}
}

func TestInternalPackageWithoutTestFiles(t *testing.T) {
	entries := entriesOf(t, fixture(t, "basic"))
	if _, ok := find(entries, "example.com/basic/internal/store", "", "func", "Get"); !ok {
		t.Errorf("no internal/store Get entry: %+v", entries)
	}
	if slices.Contains(names(entries), "TestHelper") {
		t.Errorf("TestHelper from a _test.go file is an entry")
	}
}

func TestSkippedPackages(t *testing.T) {
	entries := entriesOf(t, fixture(t, "basic"))
	for _, e := range entries {
		if strings.HasSuffix(e.Unit, "/onlytest") || strings.HasSuffix(e.Unit, "/other") || strings.HasSuffix(e.Unit, "/cmd/app") {
			t.Errorf("entry from a skipped package: %+v", e)
		}
	}
	for _, name := range []string{"WindowsOnly", "Windows", "Exported", "TestOnly"} {
		if slices.Contains(names(entries), name) {
			t.Errorf("%s is an entry", name)
		}
	}
}

func TestMainOnlyModule(t *testing.T) {
	r := runIn(t, fixture(t, "mainonly"))
	if r.code != 0 || r.stdout != "[]\n" {
		t.Errorf("exit %d, stdout %q, want 0 and \"[]\\n\"; stderr:\n%s", r.code, r.stdout, r.stderr)
	}
}

func TestNoGoModExits2(t *testing.T) {
	r := runIn(t, t.TempDir())
	if r.code != 2 || r.stdout != "" || !strings.Contains(r.stderr, "api-snapshot: go list") {
		t.Errorf("exit %d, stdout %q, stderr %q; want 2, nothing, the go list failure", r.code, r.stdout, r.stderr)
	}
}

func TestBuildFailureExits2(t *testing.T) {
	r := runIn(t, fixture(t, "broken"))
	if r.code != 2 || r.stdout != "" || !strings.Contains(r.stderr, "broken.go") {
		t.Errorf("exit %d, stdout %q, stderr %q; want 2, nothing, the compiler's message", r.code, r.stdout, r.stderr)
	}
}

func TestExtraArgumentsExit2(t *testing.T) {
	r := runIn(t, fixture(t, "basic"), "./...")
	if r.code != 2 || r.stdout != "" || !strings.Contains(r.stderr, "usage") {
		t.Errorf("exit %d, stdout %q, stderr %q; want 2 and usage", r.code, r.stdout, r.stderr)
	}
}

func TestDocAndMoveDoNotChangeOutput(t *testing.T) {
	dirA, dirB := fixture(t, "moved_a"), fixture(t, "moved_b")
	a := runIn(t, dirA)
	b := runIn(t, dirB)
	if a.code != 0 || a.stdout != b.stdout || !strings.Contains(a.stdout, `"Open"`) {
		t.Errorf("outputs differ or fail (exit %d, %d):\n%s\n---\n%s%s%s", a.code, b.code, a.stdout, b.stdout, a.stderr, b.stderr)
	}
}

func TestDeterministic(t *testing.T) {
	dir := fixture(t, "basic")
	first := runIn(t, dir)
	second := runIn(t, dir)
	if first.code != 0 || first.stdout != second.stdout {
		t.Errorf("two runs differ (exit %d):\n%s\n---\n%s", first.code, first.stdout, second.stdout)
	}
	for _, path := range []string{dir, os.Getenv("GOCACHE"), os.Getenv("GOMODCACHE"), os.TempDir()} {
		if path != "" && strings.Contains(first.stdout, path) {
			t.Errorf("output contains the absolute path %s", path)
		}
	}
}
