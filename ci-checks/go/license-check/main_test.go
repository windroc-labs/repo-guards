package main

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/google/licenseclassifier/v2/assets"
)

const count = "module(s) under a license that licenses.toml's allow does not satisfy"

func licenseText(t *testing.T, asset string) []byte {
	t.Helper()
	text, err := assets.ReadLicenseFile(asset)
	if err != nil {
		t.Fatal(err)
	}
	return text
}

func mitText(t *testing.T) []byte { return licenseText(t, "License/MIT/pristine.txt") }

func gplText(t *testing.T) []byte { return licenseText(t, "License/GPL-3.0/license.txt") }

const unrecognisable = "You may do whatever the author of this file feels like allowing on a Tuesday.\n"

// moduleDir writes files (name -> content) into a fresh directory standing in for a module's source.
func moduleDir(t *testing.T, files map[string][]byte) string {
	t.Helper()
	dir := t.TempDir()
	for name, content := range files {
		path := filepath.Join(dir, name)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, content, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

type fakeProxy struct {
	dirs  map[string]string
	calls []string
}

func (p *fakeProxy) download(path, version string) (string, error) {
	key := path + "@" + version
	p.calls = append(p.calls, key)
	dir, ok := p.dirs[key]
	if !ok {
		return "", errors.New("not found: " + key)
	}
	return dir, nil
}

type fixture struct {
	config     string
	exceptions *string
	stream     string
	dirs       map[string]string
}

func writeFile(t *testing.T, name, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func (f fixture) run(t *testing.T) (int, string, []string) {
	t.Helper()
	config := f.config
	if config == "" {
		config = `allow = ["MIT", "Apache-2.0"]`
	}
	exceptions := filepath.Join(t.TempDir(), "absent.txt")
	if f.exceptions != nil {
		exceptions = writeFile(t, "license-exceptions.txt", *f.exceptions)
	}
	proxy := &fakeProxy{dirs: f.dirs}
	var stderr bytes.Buffer
	code := run(
		[]string{writeFile(t, "licenses.toml", config), exceptions},
		strings.NewReader(f.stream), &stderr, proxy.download,
	)
	return code, stderr.String(), proxy.calls
}

func lines(s string) []string {
	return strings.Split(strings.TrimSuffix(s, "\n"), "\n")
}

func ptr(s string) *string { return &s }

const mainModule = `{"Path": "example.com/project", "Main": true, "Dir": "/src/project", "GoMod": "/src/project/go.mod"}` + "\n"

func TestAllowedLicensePasses(t *testing.T) {
	code, stderr, calls := fixture{
		stream: mainModule + `{"Path": "example.com/mit", "Version": "v1.2.3"}`,
		dirs:   map[string]string{"example.com/mit@v1.2.3": moduleDir(t, map[string][]byte{"LICENSE": mitText(t)})},
	}.run(t)
	if code != 0 || stderr != "" {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
	if !slices.Equal(calls, []string{"example.com/mit@v1.2.3"}) {
		t.Fatalf("downloads %v", calls)
	}
}

func TestDisallowedLicenseFails(t *testing.T) {
	code, stderr, _ := fixture{
		stream: `{"Path": "example.com/gpl", "Version": "v0.1.0"}`,
		dirs:   map[string]string{"example.com/gpl@v0.1.0": moduleDir(t, map[string][]byte{"COPYING": gplText(t)})},
	}.run(t)
	want := []string{"example.com/gpl@v0.1.0: GPL-3.0", "1 " + count}
	if code != 1 || !slices.Equal(lines(stderr), want) {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
}

func TestUnrecognisedLicenseFails(t *testing.T) {
	code, stderr, _ := fixture{
		stream: `{"Path": "example.com/odd", "Version": "v1.0.0"}`,
		dirs:   map[string]string{"example.com/odd@v1.0.0": moduleDir(t, map[string][]byte{"LICENSE.txt": []byte(unrecognisable)})},
	}.run(t)
	want := []string{"example.com/odd@v1.0.0: no license found", "1 " + count}
	if code != 1 || !slices.Equal(lines(stderr), want) {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
}

func TestNoticeOnlyModuleFails(t *testing.T) {
	code, stderr, _ := fixture{
		stream: `{"Path": "example.com/notice", "Version": "v1.0.0"}`,
		dirs:   map[string]string{"example.com/notice@v1.0.0": moduleDir(t, map[string][]byte{"NOTICE": mitText(t)})},
	}.run(t)
	want := []string{"example.com/notice@v1.0.0: no license found", "1 " + count}
	if code != 1 || !slices.Equal(lines(stderr), want) {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
}

func TestOnlyRootLicenseFilesCount(t *testing.T) {
	code, stderr, _ := fixture{
		stream: `{"Path": "example.com/nested", "Version": "v1.0.0"}`,
		dirs:   map[string]string{"example.com/nested@v1.0.0": moduleDir(t, map[string][]byte{"sub/LICENSE": mitText(t)})},
	}.run(t)
	want := []string{"example.com/nested@v1.0.0: no license found", "1 " + count}
	if code != 1 || !slices.Equal(lines(stderr), want) {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
}

func TestLicenseFileNamesIgnoreCase(t *testing.T) {
	for _, name := range []string{"LICENSE", "license.md", "Licence", "LICENCE-MIT", "copying.txt"} {
		t.Run(name, func(t *testing.T) {
			code, stderr, _ := fixture{
				stream: `{"Path": "example.com/m", "Version": "v1.0.0"}`,
				dirs:   map[string]string{"example.com/m@v1.0.0": moduleDir(t, map[string][]byte{name: mitText(t)})},
			}.run(t)
			if code != 0 {
				t.Fatalf("code %d, stderr %q", code, stderr)
			}
		})
	}
}

func TestEveryRecognisedLicenseMustBeAllowed(t *testing.T) {
	code, stderr, _ := fixture{
		stream: `{"Path": "example.com/dual", "Version": "v2.0.0"}`,
		dirs: map[string]string{"example.com/dual@v2.0.0": moduleDir(t, map[string][]byte{
			"LICENSE-MIT":   mitText(t),
			"LICENSE-GPL":   gplText(t),
			"LICENSE-OTHER": []byte(unrecognisable),
		})},
	}.run(t)
	want := []string{"example.com/dual@v2.0.0: GPL-3.0, MIT", "1 " + count}
	if code != 1 || !slices.Equal(lines(stderr), want) {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
}

func TestUnrecognisedFileBesideAllowedLicensePasses(t *testing.T) {
	code, stderr, _ := fixture{
		stream: `{"Path": "example.com/m", "Version": "v1.0.0"}`,
		dirs: map[string]string{"example.com/m@v1.0.0": moduleDir(t, map[string][]byte{
			"LICENSE":  mitText(t),
			"COPYING":  []byte(unrecognisable),
			"NOTICE":   gplText(t),
			"README":   gplText(t),
			"main.go":  []byte("package m\n"),
			"go.mod":   []byte("module example.com/m\n"),
			"vendor/x": gplText(t),
		})},
	}.run(t)
	if code != 0 {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
}

func TestAllowCompareIgnoresCaseAndWhitespace(t *testing.T) {
	code, stderr, _ := fixture{
		config: `allow = ["  mit ", "GPL-3.0"]`,
		stream: `{"Path": "example.com/a", "Version": "v1.0.0"}{"Path": "example.com/b", "Version": "v1.0.0"}`,
		dirs: map[string]string{
			"example.com/a@v1.0.0": moduleDir(t, map[string][]byte{"LICENSE": mitText(t)}),
			"example.com/b@v1.0.0": moduleDir(t, map[string][]byte{"LICENSE": gplText(t)}),
		},
	}.run(t)
	if code != 0 {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
}

func TestRepoLicensesTomlAllowsMIT(t *testing.T) {
	config, err := os.ReadFile(filepath.Join("..", "..", "licenses.toml"))
	if err != nil {
		t.Fatal(err)
	}
	code, stderr, _ := fixture{
		config: string(config),
		stream: `{"Path": "example.com/mit", "Version": "v1.0.0"}{"Path": "example.com/gpl", "Version": "v1.0.0"}`,
		dirs: map[string]string{
			"example.com/mit@v1.0.0": moduleDir(t, map[string][]byte{"LICENSE": mitText(t)}),
			"example.com/gpl@v1.0.0": moduleDir(t, map[string][]byte{"LICENSE": gplText(t)}),
		},
	}.run(t)
	want := []string{"example.com/gpl@v1.0.0: GPL-3.0", "1 " + count}
	if code != 1 || !slices.Equal(lines(stderr), want) {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
}

func TestFailuresAreCounted(t *testing.T) {
	code, stderr, _ := fixture{
		stream: `{"Path": "example.com/a", "Version": "v1.0.0"}
{"Path": "example.com/ok", "Version": "v1.0.0"}
{"Path": "example.com/b", "Version": "v1.0.0"}`,
		dirs: map[string]string{
			"example.com/a@v1.0.0":  moduleDir(t, map[string][]byte{"LICENSE": gplText(t)}),
			"example.com/ok@v1.0.0": moduleDir(t, map[string][]byte{"LICENSE": mitText(t)}),
			"example.com/b@v1.0.0":  moduleDir(t, nil),
		},
	}.run(t)
	want := []string{
		"example.com/a@v1.0.0: GPL-3.0",
		"example.com/b@v1.0.0: no license found",
		"2 " + count,
	}
	if code != 1 || !slices.Equal(lines(stderr), want) {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
}

func TestExceptionMatchesExactPathOnly(t *testing.T) {
	gpl := moduleDir(t, map[string][]byte{"LICENSE": gplText(t)})
	code, stderr, calls := fixture{
		exceptions: ptr("\n  example.com/excepted  \n\nexample.com/other\n"),
		stream: `{"Path": "example.com/excepted", "Version": "v1.0.0"}
{"Path": "example.com/excepted/sub", "Version": "v1.0.0"}
{"Path": "example.com/exceptedx", "Version": "v1.0.0"}`,
		dirs: map[string]string{
			"example.com/excepted/sub@v1.0.0": gpl,
			"example.com/exceptedx@v1.0.0":    gpl,
		},
	}.run(t)
	want := []string{
		"example.com/excepted/sub@v1.0.0: GPL-3.0",
		"example.com/exceptedx@v1.0.0: GPL-3.0",
		"2 " + count,
	}
	if code != 1 || !slices.Equal(lines(stderr), want) {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
	if slices.Contains(calls, "example.com/excepted@v1.0.0") {
		t.Fatalf("excepted module downloaded: %v", calls)
	}
}

func TestReplacementOfExceptedPathIsChecked(t *testing.T) {
	code, stderr, calls := fixture{
		exceptions: ptr("example.com/old\nexample.com/excepted-fork\n"),
		stream: `{"Path": "example.com/old", "Version": "v1.0.0", "Replace": {"Path": "example.com/fork", "Version": "v1.0.1"}}
{"Path": "example.com/gone", "Version": "v1.0.0", "Replace": {"Path": "example.com/excepted-fork", "Version": "v3.0.0"}}`,
		dirs: map[string]string{
			"example.com/fork@v1.0.1": moduleDir(t, map[string][]byte{"LICENSE": gplText(t)}),
		},
	}.run(t)
	want := []string{"example.com/fork@v1.0.1: GPL-3.0", "1 " + count}
	if code != 1 || !slices.Equal(lines(stderr), want) {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
	if !slices.Equal(calls, []string{"example.com/fork@v1.0.1"}) {
		t.Fatalf("downloads %v", calls)
	}
}

func TestLocalReplaceAndMainAreSkipped(t *testing.T) {
	code, stderr, calls := fixture{
		stream: mainModule + `{"Path": "example.com/local", "Version": "v0.0.0", "Replace": {"Path": "../local", "Dir": "/src/local"}}
{"Path": "example.com/unversioned", "Replace": {"Path": "./x"}}`,
	}.run(t)
	if code != 0 || stderr != "" || len(calls) != 0 {
		t.Fatalf("code %d, stderr %q, downloads %v", code, stderr, calls)
	}
}

func TestEmptyBuildListPasses(t *testing.T) {
	for _, stream := range []string{"", mainModule} {
		code, stderr, calls := fixture{stream: stream}.run(t)
		if code != 0 || stderr != "" || len(calls) != 0 {
			t.Fatalf("stream %q: code %d, stderr %q, downloads %v", stream, code, stderr, calls)
		}
	}
}

func TestAbsentExceptionsFileMeansNone(t *testing.T) {
	code, stderr, _ := fixture{
		stream: `{"Path": "example.com/gpl", "Version": "v1.0.0"}`,
		dirs:   map[string]string{"example.com/gpl@v1.0.0": moduleDir(t, map[string][]byte{"LICENSE": gplText(t)})},
	}.run(t)
	if code != 1 || !strings.HasPrefix(stderr, "example.com/gpl@v1.0.0: GPL-3.0\n") {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
}

func TestDuplicateEffectiveModuleReportedOnce(t *testing.T) {
	code, stderr, calls := fixture{
		stream: `{"Path": "example.com/a", "Version": "v1.0.0", "Replace": {"Path": "example.com/fork", "Version": "v1.0.0"}}
{"Path": "example.com/b", "Version": "v1.0.0", "Replace": {"Path": "example.com/fork", "Version": "v1.0.0"}}`,
		dirs: map[string]string{"example.com/fork@v1.0.0": moduleDir(t, map[string][]byte{"LICENSE": gplText(t)})},
	}.run(t)
	want := []string{"example.com/fork@v1.0.0: GPL-3.0", "1 " + count}
	if code != 1 || !slices.Equal(lines(stderr), want) || len(calls) != 1 {
		t.Fatalf("code %d, stderr %q, downloads %v", code, stderr, calls)
	}
}

func TestDownloadErrorExits2(t *testing.T) {
	code, stderr, _ := fixture{
		stream: `{"Path": "example.com/missing", "Version": "v1.0.0"}`,
	}.run(t)
	if code != 2 || !strings.Contains(stderr, "example.com/missing@v1.0.0") {
		t.Fatalf("code %d, stderr %q", code, stderr)
	}
}

func TestBadInputExits2(t *testing.T) {
	cases := map[string]fixture{
		"toml syntax":       {config: `allow = [`},
		"allow missing":     {config: `deny = ["MIT"]`},
		"allow not strings": {config: `allow = "MIT"`},
		"stream not json":   {stream: `{"Path": "example.com/a"`},
		"stream not object": {stream: `["example.com/a"]`},
	}
	for name, f := range cases {
		t.Run(name, func(t *testing.T) {
			if code, stderr, _ := f.run(t); code != 2 || stderr == "" {
				t.Fatalf("code %d, stderr %q", code, stderr)
			}
		})
	}
}

func TestUnreadableFilesExit2(t *testing.T) {
	dir := t.TempDir()
	var stderr bytes.Buffer
	missingConfig := run([]string{filepath.Join(dir, "absent.toml"), filepath.Join(dir, "absent.txt")},
		strings.NewReader(""), &stderr, (&fakeProxy{}).download)
	config := writeFile(t, "licenses.toml", `allow = ["MIT"]`)
	exceptionsIsDir := run([]string{config, dir}, strings.NewReader(""), &stderr, (&fakeProxy{}).download)
	if missingConfig != 2 || exceptionsIsDir != 2 {
		t.Fatalf("missing config %d, exceptions dir %d", missingConfig, exceptionsIsDir)
	}
}

func TestUsageExits2(t *testing.T) {
	var stderr bytes.Buffer
	if code := run([]string{"only-one"}, strings.NewReader(""), &stderr, (&fakeProxy{}).download); code != 2 {
		t.Fatalf("code %d", code)
	}
	if !strings.Contains(stderr.String(), "usage: license-check LICENSES_TOML EXCEPTIONS_FILE") {
		t.Fatalf("stderr %q", stderr.String())
	}
}

func TestDownloadedDir(t *testing.T) {
	exitErr := errors.New("exit status 1")
	cases := []struct {
		name    string
		out     string
		runErr  error
		wantDir string
	}{
		{"dir", `{"Path": "example.com/a", "Version": "v1.0.0", "Dir": "/cache/example.com/a@v1.0.0"}`, nil, "/cache/example.com/a@v1.0.0"},
		{"error field", `{"Path": "example.com/a", "Version": "v1.0.0", "Error": "checksum mismatch"}`, exitErr, ""},
		{"error field, zero exit", `{"Path": "example.com/a", "Version": "v1.0.0", "Error": "checksum mismatch"}`, nil, ""},
		{"non-zero exit", `{"Path": "example.com/a", "Version": "v1.0.0", "Dir": "/cache/a"}`, exitErr, ""},
		{"no output", ``, exitErr, ""},
		{"no dir", `{"Path": "example.com/a", "Version": "v1.0.0"}`, nil, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			dir, err := downloadedDir([]byte(c.out), c.runErr)
			if dir != c.wantDir || (err == nil) != (c.wantDir != "") {
				t.Fatalf("dir %q, err %v", dir, err)
			}
		})
	}
}
