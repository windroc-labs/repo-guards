// Package legacy sits in the domain layer but still imports app.
package legacy

import "example.com/layers/app"

// Old calls up into app.
func Old() int { return app.Total(1) }
