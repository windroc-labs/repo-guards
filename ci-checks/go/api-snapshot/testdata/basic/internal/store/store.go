package store

func Get(key string) string { return key }

type Item struct{ Key string }

type Sizer interface{ Len() int }
