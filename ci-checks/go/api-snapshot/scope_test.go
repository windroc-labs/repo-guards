package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

const driverScope = `["example.com/scope/driver"]`

var wholeScopeModule = []string{"example.com/scope", "example.com/scope/driver", "example.com/scope/driver/io", "example.com/scope/driverx", "example.com/scope/net"}

// scoped runs the program in dir with API_SNAPSHOT_SCOPE set to scope.
func scoped(t *testing.T, dir, scope string) string {
	t.Helper()
	t.Setenv("API_SNAPSHOT_SCOPE", scope)
	r := runIn(t, dir)
	if r.code != 0 {
		t.Fatalf("scope %q: exit %d, stderr:\n%s", scope, r.code, r.stderr)
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

// copyFixture copies a fixture module to a directory the test may change.
func copyFixture(t *testing.T, name string) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.CopyFS(dir, os.DirFS(fixture(t, name))); err != nil {
		t.Fatal(err)
	}
	return dir
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestScopeKeepsUnitsUnderPrefix(t *testing.T) {
	dir := fixture(t, "scope")
	cases := []struct {
		scope string
		want  []string
	}{
		{driverScope, []string{"example.com/scope/driver", "example.com/scope/driver/io"}},
		{`["example.com/scope/driver/io","example.com/scope/net"]`, []string{"example.com/scope/driver/io", "example.com/scope/net"}},
		{`["example.com/scope"]`, wholeScopeModule},
		{`["example.com/sco"]`, nil},
	}
	for _, c := range cases {
		if got := unitsOf(t, scoped(t, dir, c.scope)); !slices.Equal(got, c.want) {
			t.Errorf("scope %s: units = %v, want %v", c.scope, got, c.want)
		}
	}
}

func TestNoScopeWritesWholeModule(t *testing.T) {
	dir := fixture(t, "scope")
	t.Setenv("API_SNAPSHOT_SCOPE", "") // restores the variable after the test
	if err := os.Unsetenv("API_SNAPSHOT_SCOPE"); err != nil {
		t.Fatal(err)
	}
	r := runIn(t, dir)
	if r.code != 0 {
		t.Fatalf("exit %d, stderr:\n%s", r.code, r.stderr)
	}
	if got := unitsOf(t, r.stdout); !slices.Equal(got, wholeScopeModule) {
		t.Errorf("unset scope: units = %v, want %v", got, wholeScopeModule)
	}
	for _, scope := range []string{"", "[]"} {
		if got := scoped(t, dir, scope); got != r.stdout {
			t.Errorf("scope %q: output differs from unset scope:\n%s\nwant:\n%s", scope, got, r.stdout)
		}
	}
}

func TestScopeIgnoresAdditionsOutside(t *testing.T) {
	dir := copyFixture(t, "scope")
	before := scoped(t, dir, driverScope)
	dev, ok := find(parse(t, before), "example.com/scope/driver", "", "type", "Dev")
	if want := "type Dev struct; implements Closer"; !ok || dev.Signature != want {
		t.Errorf("scoped Dev entry = %+v (found %v), want signature %q", dev, ok, want)
	}
	writeFile(t, filepath.Join(dir, "net", "more.go"), "package net\n\nfunc Listen() {}\n\ntype Closer interface{ Close() }\n")
	writeFile(t, filepath.Join(dir, "driverx", "more.go"), "package driverx\n\nfunc Y() {}\n")
	if after := scoped(t, dir, driverScope); after != before {
		t.Errorf("out-of-scope additions changed the scoped output:\n%s\nwant:\n%s", after, before)
	}
	// Unscoped, Dev lists the new interface: only the scope keeps it out of the scoped Dev entry.
	dev, ok = find(parse(t, scoped(t, dir, "")), "example.com/scope/driver", "", "type", "Dev")
	if want := "type Dev struct; implements Closer, example.com/scope/net.Closer"; !ok || dev.Signature != want {
		t.Errorf("unscoped Dev entry = %+v (found %v), want signature %q", dev, ok, want)
	}
}

func TestScopeReportsAdditionsInside(t *testing.T) {
	dir := copyFixture(t, "scope")
	before := scoped(t, dir, driverScope)
	writeFile(t, filepath.Join(dir, "driver", "io", "more.go"), "package io\n\nfunc Write() {}\n")
	after := scoped(t, dir, driverScope)
	if after == before {
		t.Fatalf("in-scope addition left the scoped output unchanged:\n%s", after)
	}
	want := entry{Unit: "example.com/scope/driver/io", Kind: "func", Name: "Write", Signature: "func Write()"}
	if got, ok := find(parse(t, after), want.Unit, "", "func", "Write"); !ok || got != want {
		t.Errorf("Write entry = %+v (found %v), want %+v", got, ok, want)
	}
}

func TestBadScopeExits2(t *testing.T) {
	dir := fixture(t, "scope")
	for _, scope := range []string{"not json", "{}", "[1]", "null", "[null]", `["example.com/scope/driver",null]`, `"example.com/scope"`, `["example.com/scope"] []`} {
		t.Setenv("API_SNAPSHOT_SCOPE", scope)
		r := runIn(t, dir)
		if r.code != 2 || !strings.Contains(r.stderr, "API_SNAPSHOT_SCOPE") || r.stdout != "" {
			t.Errorf("scope %q: exit %d, stdout %q, stderr %q; want exit 2 naming API_SNAPSHOT_SCOPE and no output", scope, r.code, r.stdout, r.stderr)
		}
	}
}
