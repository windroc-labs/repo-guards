package main

import (
	"bytes"
	"encoding/json"
	"os/exec"
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
		ka, kb := a.key(), b.key()
		return slices.Compare(ka[:], kb[:])
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
	if strings.Contains(first.stdout, dir) {
		t.Errorf("output contains the fixture's absolute path %s", dir)
	}
	for _, v := range []string{"GOCACHE", "GOMODCACHE", "GOROOT"} {
		out, err := exec.Command("go", "env", v).Output()
		if path := strings.TrimSpace(string(out)); err != nil || path == "" || strings.Contains(first.stdout, path) {
			t.Errorf("go env %s = %q (%v); the output must not contain it", v, path, err)
		}
	}
}

func TestSignatures(t *testing.T) {
	entries := entriesOf(t, fixture(t, "basic"))
	const unit = "example.com/basic"
	want := []entry{
		{unit, "", "func", "Open", "func Open(string) (*File, error)"},
		{unit, "", "const", "Version", "const Version untyped string"},
		{unit, "", "const", "Mode", "const Mode Kind"},
		{unit, "", "type", "Kind", "type Kind int; implements example.com/basic/internal/store.Sizer"},
		{unit, "Kind", "method", "Len", "func (Kind) Len() int"},
		{unit, "", "var", "Default", "var Default *File"},
		{unit, "", "func", "Join", "func Join(string, ...string) string"},
		{unit, "", "func", "Map", "func Map[T any, U any]([]T, func(T) U) []U"},
		{unit, "", "func", "Sum", "func Sum[N ~float64 | ~int](...N) N"},
		{unit, "", "func", "Max", "func Max[N ~float64 | ~int](N, N) N"},
		{unit, "", "func", "Any", "func Any(any, any, func(string) string) func(string) string"},
		{unit, "", "func", "Lookup", "func Lookup(io.Reader) (example.com/basic/internal/store.Item, error)"},
		{unit, "", "func", "Bytes", "func Bytes([]uint8) int32"},
		{unit, "", "func", "Use", "func Use(interface{Close() error; Len() int}) error"},
		{unit, "", "type", "Number", "type Number interface{~float64 | ~int}"},
		{unit, "", "type", "Key", "type Key interface{comparable; ~string}"},
		{unit, "", "type", "Closer", "type Closer interface"},
		{unit, "Closer", "method", "Close", "func (Closer) Close() error"},
		{unit, "Closer", "method", "Flush", "func (Closer) Flush() error"},
		{unit, "", "type", "Sealed", "type Sealed interface; sealed"},
		{unit, "Sealed", "method", "Name", "func (Sealed) Name() string"},
		{unit, "", "type", "Callback", "type Callback struct; not comparable"},
		{unit, "Callback", "field", "Fn", "Fn func()"},
		{unit, "", "type", "GenFn", "type GenFn[T any] struct"},
		{unit, "GenFn", "field", "Fn", "Fn func(T)"},
		{unit, "", "type", "Cmp", "type Cmp[T comparable] struct"},
		{unit, "", "type", "Str", "type Str[T fmt.Stringer] struct"},
		{unit, "", "type", "Getter", "type Getter = func(string) string"},
		{unit, "", "type", "Pair", "type Pair[K comparable, V any] = map[K]V"},
		{unit, "", "type", "Public", "type Public = hidden"},
		{unit, "", "type", "List", "type List[E any] struct"},
		{unit, "List", "method", "Push", "func (*List[E]) Push(E)"},
		{unit, "List", "method", "Len", "func (List[E]) Len() int"},
		{unit, "", "type", "File", "type File struct; implements error; *File implements Closer"},
		{unit, "File", "field", "Name", "Name string"},
		{unit, "File", "field", "Header", "embedded *Header"},
		{unit, "File", "field", "Size", "Size int64"},
		{unit, "File", "field", "Path", "Path string"},
		{unit, "File", "method", "Close", "func (*File) Close() error"},
		{unit, "File", "method", "Flush", "func (*File) Flush() error"},
		{unit, "File", "method", "Error", "func (File) Error() string"},
		{unit, "File", "method", "Describe", "func (File) Describe() string"},
		{unit, "", "type", "Header", "type Header struct"},
		{unit, "Header", "field", "Size", "Size int64"},
		{unit, "", "type", "Chans", "type Chans struct"},
		{unit, "Chans", "field", "In", "In <-chan int"},
		{unit, "Chans", "field", "Both", "Both chan (<-chan int)"},
		{unit, "Chans", "field", "Tag", `Tag struct{A int "json:\"a\""}`},
		{unit, "", "type", "Left", "type Left struct"},
		{unit, "Left", "field", "X", "X int"},
		{unit, "Left", "method", "M", "func (Left) M()"},
		{unit, "", "type", "Right", "type Right struct"},
		{unit, "Right", "field", "X", "X int"},
		{unit, "Right", "method", "M", "func (Right) M()"},
		{unit, "", "type", "Amb", "type Amb struct"},
		{unit, "Amb", "field", "Left", "embedded Left"},
		{unit, "Amb", "field", "Right", "embedded Right"},
		{unit, "", "type", "Shadow", "type Shadow struct"},
		{unit, "Shadow", "field", "Left", "embedded Left"},
		{unit, "Shadow", "field", "X", "X string"},
		{unit, "Shadow", "method", "M", "func (Shadow) M()"},
		{unit, "", "type", "Box", "type Box[T any] struct"},
		{unit, "Box", "field", "V", "V T"},
		{unit, "", "type", "Boxed", "type Boxed struct"},
		{unit, "Boxed", "field", "Box", "embedded Box[int]"},
		{unit, "Boxed", "field", "V", "V int"},
	}
	var got []entry
	for _, e := range entries {
		if e.Unit == unit {
			got = append(got, e)
		}
	}
	for _, w := range want {
		g, ok := find(got, w.Unit, w.Parent, w.Kind, w.Name)
		if !ok {
			t.Errorf("missing %s %s.%s", w.Kind, w.Parent, w.Name)
		} else if g.Signature != w.Signature {
			t.Errorf("%s %s.%s signature\n got %q\nwant %q", w.Kind, w.Parent, w.Name, g.Signature, w.Signature)
		}
	}
	for _, g := range got {
		if _, ok := find(want, g.Unit, g.Parent, g.Kind, g.Name); !ok {
			t.Errorf("unexpected entry %+v", g)
		}
	}
}

func TestForeignUnitEntries(t *testing.T) {
	entries := entriesOf(t, fixture(t, "basic"))
	const unit = "example.com/basic/internal/store"
	for _, w := range []entry{
		{unit, "", "type", "Item", "type Item struct"},
		{unit, "Item", "field", "Key", "Key string"},
		{unit, "", "type", "Sizer", "type Sizer interface"},
		{unit, "Sizer", "method", "Len", "func (Sizer) Len() int"},
	} {
		if g, ok := find(entries, w.Unit, w.Parent, w.Kind, w.Name); !ok || g != w {
			t.Errorf("entry %+v (found %v), want %+v", g, ok, w)
		}
	}
}

func TestUniqueKeys(t *testing.T) {
	seen := map[[4]string]bool{}
	for _, e := range entriesOf(t, fixture(t, "basic")) {
		key := e.key()
		if seen[key] {
			t.Errorf("duplicate key %v", key)
		}
		seen[key] = true
	}
}
