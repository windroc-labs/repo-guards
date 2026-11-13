// Command layer-rules is the Layer rules scanner for Go: it lists each import spec and the package directory of the
// project it resolves to, by the module paths in the side's go.mod files. It only parses; it never builds the code.
// The protocol, shared by every language's scanner, is in ci-checks/python/layer-rules/imports.py.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"go/parser"
	"go/token"
	"io"
	"os"
	"path"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
)

const usage = "usage: layer-rules <side root>  (JSON request on stdin)"

type request struct {
	Files   []string `json:"files"`
	Scan    []string `json:"scan"`
	Context []string `json:"context"`
}

type module struct{ dir, path string }

type record struct {
	File    string   `json:"file"`
	Line    int      `json:"line,omitempty"`
	Targets []string `json:"targets,omitempty"`
	Error   string   `json:"error,omitempty"`
}

func main() { os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr)) }

func run(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	if len(args) != 1 {
		fmt.Fprintln(stderr, usage)
		return 2
	}
	if err := scanSide(args[0], stdin, json.NewEncoder(stdout)); err != nil {
		fmt.Fprintf(stderr, "layer-rules: %v\n", err)
		return 2
	}
	return 0
}

func scanSide(root string, stdin io.Reader, out *json.Encoder) error {
	var req request
	if err := json.NewDecoder(stdin).Decode(&req); err != nil {
		return err
	}
	if req.Files == nil || req.Scan == nil || req.Context == nil {
		return errors.New(`the request needs "files", "scan" and "context"`)
	}
	packages := map[string]bool{}
	for _, name := range req.Files {
		if err := relative(name); err != nil {
			return err
		}
		packages[dir(name)] = true
	}
	var modules []module
	for _, name := range req.Context {
		if err := relative(name); err != nil {
			return err
		}
		if path.Base(name) != "go.mod" {
			continue
		}
		data, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(name)))
		if err != nil {
			return err
		}
		modulePath := modulePath(string(data))
		if modulePath == "" {
			return fmt.Errorf("%s has no module directive", name)
		}
		modules = append(modules, module{dir(name), modulePath})
	}
	for _, name := range req.Scan {
		if err := relative(name); err != nil {
			return err
		}
		data, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(name)))
		if err != nil {
			return err
		}
		for _, r := range scanFile(name, data, modules, packages) {
			if err := out.Encode(r); err != nil {
				return err
			}
		}
	}
	return nil
}

// scanFile gives one record per import spec that resolves to a package of the project: each spec imports one
// package under its own name, so it is one reference, at its own line in the .go file (not a //line directive's).
func scanFile(name string, data []byte, modules []module, packages map[string]bool) []record {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, name, data, parser.ImportsOnly)
	if err != nil {
		message, _, _ := strings.Cut(err.Error(), "\n")
		if len(message) > 300 {
			message = message[:300]
		}
		return []record{{File: name, Error: message}}
	}
	var records []record
	for _, spec := range file.Imports {
		importPath, err := strconv.Unquote(spec.Path.Value)
		if err != nil {
			continue
		}
		if target, found := resolve(importPath, modules, packages); found {
			records = append(records, record{File: name, Line: fset.PositionFor(spec.Pos(), false).Line, Targets: []string{target}})
		}
	}
	return records
}

// resolve maps an import path to its package directory: under the module with the longest matching path (the
// shallowest go.mod on a tie), kept only when that directory holds Go files and belongs to that module, not to a
// module nested inside it.
func resolve(importPath string, modules []module, packages map[string]bool) (string, bool) {
	var owner *module
	for i, m := range modules {
		if importPath != m.path && !strings.HasPrefix(importPath, m.path+"/") {
			continue
		}
		if owner == nil || len(m.path) > len(owner.path) || len(m.path) == len(owner.path) && depth(m.dir) < depth(owner.dir) {
			owner = &modules[i]
		}
	}
	if owner == nil {
		return "", false
	}
	target := path.Join(owner.dir, strings.TrimPrefix(importPath, owner.path))
	target = strings.TrimPrefix(target, "/")
	if target == "." {
		target = ""
	}
	if !packages[target] || enclosing(target, modules) != owner.dir {
		return "", false
	}
	return target, true
}

// enclosing is the directory of the nearest go.mod at or above directory.
func enclosing(directory string, modules []module) string {
	best := ""
	found := false
	for _, m := range modules {
		inside := m.dir == "" || directory == m.dir || strings.HasPrefix(directory, m.dir+"/")
		if inside && (!found || len(m.dir) > len(best)) {
			best, found = m.dir, true
		}
	}
	return best
}

// modulePath is the path in go.mod's module directive, single-line or block form, or "" when it has none.
func modulePath(text string) string {
	inBlock := false
	for _, line := range strings.Split(text, "\n") {
		line, _, _ = strings.Cut(line, "//")
		fields := strings.Fields(line)
		switch {
		case inBlock && len(fields) == 1 && fields[0] != ")":
			return unquote(fields[0])
		case inBlock && len(fields) > 0:
			return ""
		case len(fields) == 2 && fields[0] == "module" && fields[1] == "(":
			inBlock = true
		case len(fields) == 2 && fields[0] == "module":
			return unquote(fields[1])
		}
	}
	return ""
}

func unquote(field string) string {
	if unquoted, err := strconv.Unquote(field); err == nil {
		return unquoted
	}
	return field
}

func depth(directory string) int {
	if directory == "" {
		return 0
	}
	return strings.Count(directory, "/") + 1
}

func dir(name string) string {
	d := path.Dir(name)
	if d == "." {
		return ""
	}
	return d
}

func relative(name string) error {
	if name == "" || path.IsAbs(name) || slices.Contains(strings.Split(name, "/"), "..") {
		return fmt.Errorf("bad path %q", name)
	}
	return nil
}
