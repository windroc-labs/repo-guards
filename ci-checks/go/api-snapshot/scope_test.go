package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

var wholeScopeModule = []string{"example.com/scope", "example.com/scope/driver", "example.com/scope/driver/io", "example.com/scope/driverx", "example.com/scope/net"}

// goScope is a repo-guards.toml setting api-snapshot-scope.go for working directory ".".
func goScope(prefixes string) string {
	return "[project.\".\"]\napi-snapshot-scope.go = " + prefixes + "\n"
}

var driverScope = goScope(`["example.com/scope/driver"]`)

// repoWith copies the scope fixture module to working directory wd of a new
// repo root, and returns the root.
func repoWith(t *testing.T, wd string) string {
	t.Helper()
	root := t.TempDir()
	if err := os.CopyFS(filepath.Join(root, wd), os.DirFS(fixture(t, "scope"))); err != nil {
		t.Fatal(err)
	}
	return root
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

// generateIn writes config as the root's repo-guards.toml and runs the program
// as go.yml's Generate step does.
func generateIn(t *testing.T, root, wd, config string) result {
	t.Helper()
	writeFile(t, filepath.Join(root, ".github", "checks", "repo-guards.toml"), config)
	return runIn(t, filepath.Join(root, wd), root, wd)
}

func scoped(t *testing.T, root, config string) string {
	t.Helper()
	r := generateIn(t, root, ".", config)
	if r.code != 0 {
		t.Fatalf("config %q: exit %d, stderr:\n%s", config, r.code, r.stderr)
	}
	return r.stdout
}

func parse(t *testing.T, out string) []entry {
	t.Helper()
	var entries []entry
	if err := json.Unmarshal([]byte(out), &entries); err != nil {
		t.Fatalf("output is not an entry array: %v\n%s", err, out)
	}
	return entries
}

func unitsOf(t *testing.T, out string) []string {
	t.Helper()
	var units []string
	for _, e := range parse(t, out) {
		units = append(units, e.Unit)
	}
	slices.Sort(units)
	return slices.Compact(units)
}

func TestScopeKeepsUnitsUnderPrefix(t *testing.T) {
	root := repoWith(t, ".")
	cases := []struct {
		config string
		want   []string
	}{
		{driverScope, []string{"example.com/scope/driver", "example.com/scope/driver/io"}},
		{goScope(`["example.com/scope/driver/io", "example.com/scope/net"]`), []string{"example.com/scope/driver/io", "example.com/scope/net"}},
		{goScope(`["example.com/scope"]`), wholeScopeModule},
		{goScope(`["example.com/sco"]`), nil},
	}
	for _, c := range cases {
		if got := unitsOf(t, scoped(t, root, c.config)); !slices.Equal(got, c.want) {
			t.Errorf("config %q: units = %v, want %v", c.config, got, c.want)
		}
	}
}

func TestNoScopeWritesWholeModule(t *testing.T) {
	root := repoWith(t, ".")
	r := runIn(t, root)
	if r.code != 0 {
		t.Fatalf("exit %d, stderr:\n%s", r.code, r.stderr)
	}
	if got := unitsOf(t, r.stdout); !slices.Equal(got, wholeScopeModule) {
		t.Errorf("no arguments: units = %v, want %v", got, wholeScopeModule)
	}
	if got := runIn(t, root, root, "."); got.code != 0 || got.stdout != r.stdout {
		t.Errorf("no repo-guards.toml: exit %d, stderr %q; output differs from no arguments:\n%s", got.code, got.stderr, got.stdout)
	}
	for _, config := range []string{
		"",
		"[project.\"other\"]\napi-snapshot-scope.go = [\"example.com/scope/net\"]\n",
		"[project.\".\"]\napi-snapshot.go = \".github/checks/api-go.json\"\n",
		"[project.\".\"]\napi-snapshot-scope.python = [\"pkg\"]\n",
	} {
		if got := scoped(t, root, config); got != r.stdout {
			t.Errorf("config %q: output differs from no arguments:\n%s\nwant:\n%s", config, got, r.stdout)
		}
	}
}

func TestScopeOfTheWorkingDirectory(t *testing.T) {
	root := repoWith(t, "svc")
	config := "[project.\".\"]\napi-snapshot-scope.go = [\"example.com/scope/net\"]\n" +
		"[project.\"svc\"]\napi-snapshot-scope.go = [\"example.com/scope/driver\"]\n"
	r := generateIn(t, root, "svc", config)
	if r.code != 0 {
		t.Fatalf("exit %d, stderr:\n%s", r.code, r.stderr)
	}
	if got, want := unitsOf(t, r.stdout), []string{"example.com/scope/driver", "example.com/scope/driver/io"}; !slices.Equal(got, want) {
		t.Errorf("units = %v, want %v", got, want)
	}
}

func TestScopeIgnoresAdditionsOutside(t *testing.T) {
	root := repoWith(t, ".")
	before := scoped(t, root, driverScope)
	dev, ok := find(parse(t, before), "example.com/scope/driver", "", "type", "Dev")
	if want := "type Dev struct; implements Closer"; !ok || dev.Signature != want {
		t.Errorf("scoped Dev entry = %+v (found %v), want signature %q", dev, ok, want)
	}
	writeFile(t, filepath.Join(root, "net", "more.go"), "package net\n\nfunc Listen() {}\n\ntype Closer interface{ Close() }\n")
	writeFile(t, filepath.Join(root, "driverx", "more.go"), "package driverx\n\nfunc Y() {}\n")
	if after := scoped(t, root, driverScope); after != before {
		t.Errorf("out-of-scope additions changed the scoped output:\n%s\nwant:\n%s", after, before)
	}
	// Unscoped, Dev lists the new interface: only the scope keeps it out of the scoped Dev entry.
	dev, ok = find(parse(t, scoped(t, root, "")), "example.com/scope/driver", "", "type", "Dev")
	if want := "type Dev struct; implements Closer, example.com/scope/net.Closer"; !ok || dev.Signature != want {
		t.Errorf("unscoped Dev entry = %+v (found %v), want signature %q", dev, ok, want)
	}
}

func TestScopeReportsAdditionsInside(t *testing.T) {
	root := repoWith(t, ".")
	before := scoped(t, root, driverScope)
	writeFile(t, filepath.Join(root, "driver", "io", "more.go"), "package io\n\nfunc Write() {}\n")
	after := scoped(t, root, driverScope)
	if after == before {
		t.Fatalf("in-scope addition left the scoped output unchanged:\n%s", after)
	}
	want := entry{Unit: "example.com/scope/driver/io", Kind: "func", Name: "Write", Signature: "func Write()"}
	if got, ok := find(parse(t, after), want.Unit, "", "func", "Write"); !ok || got != want {
		t.Errorf("Write entry = %+v (found %v), want %+v", got, ok, want)
	}
}

func TestBadScopeExits2(t *testing.T) {
	root := repoWith(t, ".")
	const where = `[project."."] api-snapshot-scope.go`
	notStrings := where + " is not an array of strings"
	badPrefix := `must be a package import path such as "example.com/mod/driver"`
	cases := []struct{ config, message string }{
		{"[project.\".\"\n", "toml:"},
		{"project = 1\n", "[project] is not a table"},
		{"[project]\n\".\" = 1\n", `[project."."] is not a table`},
		{"[project.\".\"]\napi-snapshot-scope = [\"example.com/scope\"]\n", `[project."."] api-snapshot-scope is not a table`},
		{goScope(`[]`), where + " = []: must list at least one package import path"},
		{goScope(`"example.com/scope"`), notStrings},
		{goScope(`[1]`), notStrings},
		{goScope(`["example.com/scope", 1]`), notStrings},
		{goScope(`[["example.com/scope"]]`), notStrings},
		{goScope(`["example.com/scope/"]`), badPrefix},
		{goScope(`["/example.com/scope"]`), badPrefix},
		{goScope(`["example.com/../scope"]`), badPrefix},
		{goScope(`["example.com/./scope"]`), badPrefix},
		{goScope(`["example.com/sc ope"]`), badPrefix},
		{goScope(`["example.com/scope/*"]`), badPrefix},
		{goScope(`["example.com/scope\u0001"]`), "must not contain control characters"},
	}
	for _, c := range cases {
		r := generateIn(t, root, ".", c.config)
		prefix := "api-snapshot: .github/checks/repo-guards.toml: "
		if r.code != 2 || r.stdout != "" || !strings.HasPrefix(r.stderr, prefix) || !strings.Contains(r.stderr, c.message) {
			t.Errorf("config %q: exit %d, stdout %q, stderr %q; want exit 2, no output, stderr %q... naming %q", c.config, r.code, r.stdout, r.stderr, prefix, c.message)
		}
	}
}

func TestScopeArgumentsAreAPair(t *testing.T) {
	root := repoWith(t, ".")
	for _, args := range [][]string{{root, ".", "go"}, {root, ".", "", ""}} {
		if r := runIn(t, root, args...); r.code != 2 || !strings.Contains(r.stderr, "usage: api-snapshot [REPO_ROOT WORKING_DIRECTORY]") {
			t.Errorf("args %q: exit %d, stderr %q; want exit 2 with the usage", args, r.code, r.stderr)
		}
	}
}
