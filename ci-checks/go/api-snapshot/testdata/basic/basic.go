// Package basic is the generator's main test fixture.
package basic

// Open opens a file.
func Open(name string) (*File, error) { return &File{Name: name}, nil }

func helper() {}

// File is an open file.
type File struct {
	Name string
	path string
	*Header
	meta
}

func (f *File) Close() error { return nil }

func (f *File) Flush() error { return nil }

func (f File) Error() string { return f.path }

type Header struct{ Size int64 }

type meta struct{ Path string }

func (meta) Describe() string { return "" }
