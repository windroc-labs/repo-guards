// Package app uses the domain layer.
package app

import "example.com/layers/domain"

// Total applies the domain rate.
func Total(n int) int { return n * domain.Rate }
