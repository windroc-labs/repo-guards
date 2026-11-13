package basic

import (
	"fmt"
	"io"

	"example.com/basic/internal/store"
)

func Lookup(r io.Reader) (store.Item, error) { return store.Item{}, nil }

func Bytes(b []byte) rune { return 0 }

func (Kind) Len() int { return 0 }

type Left struct{ X int }

func (Left) M() {}

type Right struct{ X int }

func (Right) M() {}

type Amb struct {
	Left
	Right
}

type Shadow struct {
	Left
	X string
}

type Box[T any] struct{ V T }

type Boxed struct{ Box[int] }

func Use(v interface {
	Len() int
	Close() error
}) error {
	return nil
}

type Key interface {
	~string
	comparable
}

type Public = hidden

type hidden struct{ Field int }

func (hidden) Method() {}

type GenFn[T any] struct{ Fn func(T) }

type Cmp[T interface{ comparable }] struct{}

type Str[T interface{ fmt.Stringer }] struct{}
