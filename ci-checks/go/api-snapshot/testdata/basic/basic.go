// Package basic is the generator's main test fixture.
package basic

// Open opens a file.
func Open(name string) (*File, error) { return &File{Name: name}, nil }

func helper() {}

// File is an open file.
type File struct {
	Name string
	path string
}
