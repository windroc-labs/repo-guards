package basic

import "io"

const Version = "1"

const Mode Kind = 2

type Kind int

var Default *File

func Join(sep string, parts ...string) string { return "" }

func Map[T, U any](in []T, f func(T) U) []U { return nil }

func Sum[N ~int | ~float64](ns ...N) N { return 0 }

func Max[N interface{ ~float64 | ~int }](a, b N) N { return a }

func Any(v interface{}, w any, g Getter) Getter { return g }

type Number interface{ ~int | ~float64 }

type Closer interface {
	io.Closer
	Flush() error
}

type Sealed interface {
	Name() string
	seal()
}

type Callback struct{ Fn func() }

type Getter = func(string) string

type Pair[K comparable, V any] = map[K]V

type List[E any] struct{ items []E }

func (l *List[T]) Push(v T) {}

func (l List[T]) Len() int { return len(l.items) }

type Chans struct {
	In   <-chan int
	Both chan (<-chan int)
	Tag  struct {
		A int "json:\"a\""
	}
}
