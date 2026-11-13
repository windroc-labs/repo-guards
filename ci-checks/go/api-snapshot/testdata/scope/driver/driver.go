package driver

type Dev struct{}

func (Dev) Close() {}

type Closer interface{ Close() }
