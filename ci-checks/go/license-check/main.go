// Command license-check fails a Go project whose build list holds a module
// under a license that licenses.toml's allow does not satisfy.
//
// Usage: go list -mod=readonly -m -json all | license-check LICENSES_TOML EXCEPTIONS_FILE
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"sync"

	"github.com/BurntSushi/toml"
	licenseclassifier "github.com/google/licenseclassifier/v2"
	"github.com/google/licenseclassifier/v2/assets"
)

// download returns, for each module, the directory holding its hash-verified source.
type download func(modules []moduleVersion) (map[moduleVersion]string, error)

type moduleVersion struct {
	Path    string
	Version string
}

type listedModule struct {
	Path    string
	Version string
	Main    bool
	Replace *moduleVersion
}

var classifier = sync.OnceValues(assets.DefaultClassifier)

func main() {
	os.Exit(run(os.Args[1:], os.Stdin, os.Stderr, goModDownload))
}

func run(args []string, stdin io.Reader, stderr io.Writer, fetch download) int {
	if len(args) != 2 {
		_, _ = fmt.Fprintln(stderr, "usage: license-check LICENSES_TOML EXCEPTIONS_FILE")
		return 2
	}
	denied, err := check(args[0], args[1], stdin, fetch)
	if err != nil {
		_, _ = fmt.Fprintf(stderr, "license-check: %v\n", err)
		return 2
	}
	for _, line := range denied {
		_, _ = fmt.Fprintln(stderr, line)
	}
	if len(denied) > 0 {
		_, _ = fmt.Fprintf(stderr, "%d module(s) under a license that licenses.toml's allow does not satisfy\n", len(denied))
		return 1
	}
	return 0
}

func check(configPath, exceptionsPath string, stdin io.Reader, fetch download) ([]string, error) {
	allowed, err := readAllow(configPath)
	if err != nil {
		return nil, err
	}
	exceptions, err := readExceptions(exceptionsPath)
	if err != nil {
		return nil, err
	}
	modules, err := buildList(stdin)
	if err != nil {
		return nil, err
	}
	var checked []moduleVersion
	for _, m := range modules {
		if !exceptions[m.Path] {
			checked = append(checked, m)
		}
	}
	if len(checked) == 0 {
		return nil, nil
	}
	dirs, err := fetch(checked)
	if err != nil {
		return nil, fmt.Errorf("go mod download: %w", err)
	}
	var denied []string
	for _, m := range checked {
		dir, ok := dirs[m]
		if !ok {
			return nil, fmt.Errorf("go mod download: no result for %s@%s", m.Path, m.Version)
		}
		names, err := licensesIn(dir)
		if err != nil {
			return nil, fmt.Errorf("%s@%s: %w", m.Path, m.Version, err)
		}
		if !permitted(names, allowed) {
			found := "no license found"
			if len(names) > 0 {
				found = strings.Join(names, ", ")
			}
			denied = append(denied, fmt.Sprintf("%s@%s: %s", m.Path, m.Version, found))
		}
	}
	return denied, nil
}

func normalize(license string) string {
	return strings.ToLower(strings.Join(strings.Fields(license), " "))
}

func readAllow(path string) (map[string]bool, error) {
	var config struct {
		Allow []string `toml:"allow"`
	}
	meta, err := toml.DecodeFile(path, &config)
	if err != nil {
		return nil, err
	}
	if !meta.IsDefined("allow") {
		return nil, fmt.Errorf("%s: no allow list", path)
	}
	allowed := map[string]bool{}
	for _, license := range config.Allow {
		allowed[normalize(license)] = true
	}
	return allowed, nil
}

func readExceptions(path string) (map[string]bool, error) {
	text, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	exceptions := map[string]bool{}
	for line := range strings.Lines(string(text)) {
		if line = strings.TrimSpace(line); line != "" {
			exceptions[line] = true
		}
	}
	return exceptions, nil
}

// buildList reads the `go list -m -json all` stream: Main modules dropped, a
// replacement module counted in place of the one it replaces, and a module
// replaced by a local directory (no Replace.Version) dropped as the project's own.
func buildList(stdin io.Reader) ([]moduleVersion, error) {
	var modules []moduleVersion
	seen := map[moduleVersion]bool{}
	decoder := json.NewDecoder(stdin)
	for {
		var m listedModule
		err := decoder.Decode(&m)
		if errors.Is(err, io.EOF) {
			return modules, nil
		}
		if err != nil {
			return nil, fmt.Errorf("build list: %w", err)
		}
		if m.Main {
			continue
		}
		effective := moduleVersion{m.Path, m.Version}
		if m.Replace != nil {
			if m.Replace.Version == "" {
				continue
			}
			effective = *m.Replace
		}
		if !seen[effective] {
			seen[effective] = true
			modules = append(modules, effective)
		}
	}
}

func isLicenseFile(name string) bool {
	upper := strings.ToUpper(name)
	return strings.HasPrefix(upper, "LICENSE") || strings.HasPrefix(upper, "LICENCE") || strings.HasPrefix(upper, "COPYING")
}

// licensesIn returns the sorted names of the licenses recognised in the
// license files at the root of dir.
func licensesIn(dir string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var names []string
	for _, entry := range entries {
		if entry.IsDir() || !isLicenseFile(entry.Name()) {
			continue
		}
		found, err := classify(filepath.Join(dir, entry.Name()))
		if err != nil {
			return nil, err
		}
		names = append(names, found...)
	}
	slices.Sort(names)
	return slices.Compact(names), nil
}

func classify(path string) ([]string, error) {
	c, err := classifier()
	if err != nil {
		return nil, err
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer func() { _ = file.Close() }()
	results, err := c.MatchFrom(file)
	if err != nil {
		return nil, err
	}
	return licenseNames(results), nil
}

// licenseNames keeps the "License" matches, as go-licenses does; the default
// assets also hold "Header" matches (license notices in source headers).
func licenseNames(results licenseclassifier.Results) []string {
	var names []string
	for _, match := range results.Matches {
		if match.MatchType == "License" {
			names = append(names, match.Name)
		}
	}
	return names
}

func permitted(names []string, allowed map[string]bool) bool {
	if len(names) == 0 {
		return false
	}
	for _, name := range names {
		if !allowed[normalize(name)] {
			return false
		}
	}
	return true
}

// goModDownload fetches every module in one go command, which downloads them concurrently.
func goModDownload(modules []moduleVersion) (map[moduleVersion]string, error) {
	args := []string{"mod", "download", "-json"}
	for _, m := range modules {
		args = append(args, m.Path+"@"+m.Version)
	}
	cmd := exec.Command("go", args...)
	cmd.Stderr = os.Stderr
	out, err := cmd.Output()
	return downloadedDirs(out, err)
}

// downloadedDirs reads the Dir of each `go mod download -json` result; on failure
// go reports the reason in a result's Error field, which says more than the exit status.
func downloadedDirs(out []byte, runErr error) (map[moduleVersion]string, error) {
	dirs := map[moduleVersion]string{}
	decoder := json.NewDecoder(bytes.NewReader(out))
	for decoder.More() {
		var result struct {
			Path, Version, Dir, Error string
		}
		if err := decoder.Decode(&result); err != nil {
			if runErr != nil {
				return nil, runErr
			}
			return nil, fmt.Errorf("unreadable output: %w", err)
		}
		module := result.Path + "@" + result.Version
		switch {
		case result.Error != "":
			return nil, fmt.Errorf("%s: %s", module, result.Error)
		case result.Dir == "":
			return nil, fmt.Errorf("%s: no Dir in output", module)
		}
		dirs[moduleVersion{result.Path, result.Version}] = result.Dir
	}
	if runErr != nil {
		return nil, runErr
	}
	return dirs, nil
}
