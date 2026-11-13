// Command api-snapshot prints the public API of the Go module in the current
// directory as snapshot entries, read from the compiler's export data.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"go/importer"
	"go/token"
	"go/types"
	"io"
	"os"
	"os/exec"
	"slices"
	"strings"
)

type entry struct {
	Unit      string `json:"unit"`
	Parent    string `json:"parent"`
	Kind      string `json:"kind"`
	Name      string `json:"name"`
	Signature string `json:"signature"`
}

func (e entry) key() [4]string { return [4]string{e.Unit, e.Parent, e.Kind, e.Name} }

type listedPackage struct {
	ImportPath string
	Name       string
	Export     string
	DepOnly    bool
	GoFiles    []string
	CgoFiles   []string
}

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

func run(args []string, stdout, stderr io.Writer) int {
	if len(args) > 0 {
		fmt.Fprintln(stderr, "usage: api-snapshot, run in the module directory; prints the public API entries as JSON")
		return 2
	}
	scope, err := readScope()
	if err != nil {
		fmt.Fprintf(stderr, "api-snapshot: %v\n", err)
		return 2
	}
	entries, err := generate(stderr, scope)
	if err != nil {
		fmt.Fprintf(stderr, "api-snapshot: %v\n", err)
		return 2
	}
	out, err := json.MarshalIndent(entries, "", "  ")
	if err == nil {
		_, err = stdout.Write(append(out, '\n'))
	}
	if err != nil {
		fmt.Fprintf(stderr, "api-snapshot: %v\n", err)
		return 2
	}
	return 0
}

// readScope reads the freeze scope the API snapshot job passes: import path
// prefixes as a JSON array of strings. Unset, empty or [] is the whole module (nil).
func readScope() ([]string, error) {
	value := os.Getenv("API_SNAPSHOT_SCOPE")
	if value == "" {
		return nil, nil
	}
	var scope []string
	if err := json.Unmarshal([]byte(value), &scope); err != nil || scope == nil {
		return nil, fmt.Errorf("API_SNAPSHOT_SCOPE is not a JSON array of strings: %s", value)
	}
	if len(scope) == 0 {
		return nil, nil
	}
	return scope, nil
}

// inScope matches by path segment: a/b covers a/b and a/b/c, not a/bc.
func inScope(scope []string, path string) bool {
	if scope == nil {
		return true
	}
	return slices.ContainsFunc(scope, func(prefix string) bool {
		return path == prefix || strings.HasPrefix(path, prefix+"/")
	})
}

func generate(stderr io.Writer, scope []string) ([]entry, error) {
	units, exports, err := listPackages(stderr)
	if err != nil {
		return nil, err
	}
	// Filtered before import, so implements lists name only in-scope interfaces.
	units = slices.DeleteFunc(units, func(path string) bool { return !inScope(scope, path) })
	imp := importer.ForCompiler(token.NewFileSet(), "gc", func(path string) (io.ReadCloser, error) {
		file := exports[path]
		if file == "" {
			return nil, fmt.Errorf("no export data for %s", path)
		}
		return os.Open(file)
	})
	var pkgs []*types.Package
	for _, path := range units {
		pkg, err := imp.Import(path)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		pkgs = append(pkgs, pkg)
	}
	candidates := candidateInterfaces(pkgs)
	entries := []entry{}
	for _, pkg := range pkgs {
		unitEntries, err := packageEntries(pkg, candidates)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", pkg.Path(), err)
		}
		entries = append(entries, unitEntries...)
	}
	slices.SortFunc(entries, func(a, b entry) int {
		ka, kb := a.key(), b.key()
		return slices.Compare(ka[:], kb[:])
	})
	return entries, nil
}

// listPackages builds every package of the module and its dependencies, and
// returns the units' import paths and each package's export data file.
func listPackages(stderr io.Writer) ([]string, map[string]string, error) {
	cmd := exec.Command("go", "list", "-export", "-deps", "-json=ImportPath,Name,Export,DepOnly,GoFiles,CgoFiles", "./...")
	cmd.Stderr = stderr
	out, err := cmd.Output()
	if err != nil {
		return nil, nil, fmt.Errorf("go list: %w", err)
	}
	var units []string
	exports := map[string]string{}
	dec := json.NewDecoder(bytes.NewReader(out))
	for {
		var p listedPackage
		if err := dec.Decode(&p); errors.Is(err, io.EOF) {
			break
		} else if err != nil {
			return nil, nil, fmt.Errorf("go list output: %w", err)
		}
		exports[p.ImportPath] = p.Export
		if !p.DepOnly && p.Name != "main" && len(p.GoFiles)+len(p.CgoFiles) > 0 {
			units = append(units, p.ImportPath)
		}
	}
	return units, exports, nil
}

// candidateInterfaces are the interfaces a type's implements lists may name:
// error and the units' exported, non-generic interfaces with methods.
func candidateInterfaces(pkgs []*types.Package) []*types.Named {
	candidates := []*types.Named{types.Universe.Lookup("error").Type().(*types.Named)}
	for _, pkg := range pkgs {
		for _, name := range pkg.Scope().Names() {
			obj, ok := pkg.Scope().Lookup(name).(*types.TypeName)
			if !ok || !obj.Exported() || obj.IsAlias() {
				continue
			}
			named := obj.Type().(*types.Named)
			iface, ok := named.Underlying().(*types.Interface)
			if ok && named.TypeParams().Len() == 0 && iface.IsMethodSet() && iface.NumMethods() > 0 {
				candidates = append(candidates, named)
			}
		}
	}
	return candidates
}

// member is an entry under a type: the type is its parent.
type member struct{ kind, name, signature string }

func packageEntries(pkg *types.Package, candidates []*types.Named) ([]entry, error) {
	f := formatter{pkg}
	var entries []entry
	add := func(parent, kind, name, signature string) {
		entries = append(entries, entry{pkg.Path(), parent, kind, name, signature})
	}
	scope := pkg.Scope()
	for _, name := range scope.Names() {
		obj := scope.Lookup(name)
		if !obj.Exported() {
			continue
		}
		switch obj := obj.(type) {
		case *types.Const:
			add("", "const", name, "const "+name+" "+f.typ(obj.Type()))
		case *types.Var:
			add("", "var", name, "var "+name+" "+f.typ(obj.Type()))
		case *types.Func:
			add("", "func", name, "func "+name+f.signature(obj.Signature()))
		case *types.TypeName:
			alias, isAlias := obj.Type().(*types.Alias)
			if isAlias {
				add("", "type", name, "type "+name+f.typeParams(alias.TypeParams())+" = "+f.typ(alias.Rhs()))
				continue
			}
			named := obj.Type().(*types.Named)
			add("", "type", name, "type "+name+f.typeParams(named.TypeParams())+" "+f.underlying(named)+implements(f, named, candidates))
			members, err := members(f, named)
			if err != nil {
				return nil, err
			}
			for _, m := range members {
				add(name, m.kind, m.name, m.signature)
			}
		}
	}
	return entries, nil
}

func implements(f formatter, named *types.Named, candidates []*types.Named) string {
	if _, isIface := named.Underlying().(*types.Interface); isIface || named.TypeParams().Len() > 0 {
		return ""
	}
	var value, pointer []string
	for _, c := range candidates {
		iface := c.Underlying().(*types.Interface)
		if types.Implements(named, iface) {
			value = append(value, f.typ(c))
		} else if types.Implements(types.NewPointer(named), iface) {
			pointer = append(pointer, f.typ(c))
		}
	}
	slices.Sort(value)
	slices.Sort(pointer)
	out := ""
	if len(value) > 0 {
		out += "; implements " + strings.Join(value, ", ")
	}
	if len(pointer) > 0 {
		out += "; *" + named.Obj().Name() + " implements " + strings.Join(pointer, ", ")
	}
	return out
}

// members are the exported methods and fields selectable on a defined type,
// promoted ones included.
func members(f formatter, named *types.Named) ([]member, error) {
	t, err := ownInstance(named)
	if err != nil {
		return nil, err
	}
	recv := f.typ(t)
	if iface, ok := named.Underlying().(*types.Interface); ok {
		var out []member
		for i := range iface.NumMethods() {
			if m := iface.Method(i); m.Exported() {
				out = append(out, member{"method", m.Name(), "func (" + recv + ") " + m.Name() + f.signature(m.Signature())})
			}
		}
		return out, nil
	}
	return append(methods(f, t, recv), fields(f, t)...), nil
}

// ownInstance instantiates a generic type with its own type parameters, so its
// members use the declaration's names, not each method receiver's.
func ownInstance(named *types.Named) (types.Type, error) {
	params := named.TypeParams()
	if params.Len() == 0 {
		return named, nil
	}
	args := make([]types.Type, params.Len())
	for i := range args {
		args[i] = params.At(i)
	}
	return types.Instantiate(nil, named, args, false)
}

func methods(f formatter, t types.Type, recv string) []member {
	values := types.NewMethodSet(t)
	pointers := types.NewMethodSet(types.NewPointer(t))
	var out []member
	for i := range pointers.Len() {
		sel := pointers.At(i)
		m := sel.Obj()
		if !m.Exported() {
			continue
		}
		r := "*" + recv
		if values.Lookup(m.Pkg(), m.Name()) != nil {
			r = recv
		}
		out = append(out, member{"method", m.Name(), "func (" + r + ") " + m.Name() + f.signature(sel.Type().(*types.Signature))})
	}
	return out
}

func fields(f formatter, t types.Type) []member {
	names := fieldNames(t.Underlying(), map[types.Type]bool{})
	slices.Sort(names)
	var out []member
	for _, name := range slices.Compact(names) {
		// Go's selector rules: the shallowest field wins; a name ambiguous at
		// that depth, or a method at a shallower one, is not a field of t.
		obj, _, _ := types.LookupFieldOrMethod(t, true, f.pkg, name)
		field, ok := obj.(*types.Var)
		if !ok || !field.IsField() {
			continue
		}
		sig := field.Name() + " " + f.typ(field.Type())
		if field.Embedded() {
			sig = "embedded " + f.typ(field.Type())
		}
		out = append(out, member{"field", name, sig})
	}
	return out
}

// fieldNames lists the exported field names of a struct and of the structs it
// embeds, at any depth, with repeats.
func fieldNames(u types.Type, visited map[types.Type]bool) []string {
	st, ok := u.(*types.Struct)
	if !ok {
		return nil
	}
	var out []string
	for i := range st.NumFields() {
		field := st.Field(i)
		if field.Exported() {
			out = append(out, field.Name())
		}
		if !field.Embedded() {
			continue
		}
		embedded := types.Unalias(field.Type())
		if ptr, ok := embedded.(*types.Pointer); ok {
			embedded = types.Unalias(ptr.Elem())
		}
		if !visited[embedded] {
			visited[embedded] = true
			out = append(out, fieldNames(embedded.Underlying(), visited)...)
		}
	}
	return out
}

// formatter writes types for entries of one unit: no parameter names, aliases
// resolved, named types qualified by import path unless declared in the unit.
type formatter struct{ pkg *types.Package }

func (f formatter) underlying(named *types.Named) string {
	switch u := named.Underlying().(type) {
	case *types.Struct:
		if named.TypeParams().Len() == 0 && !types.Comparable(named) {
			return "struct; not comparable"
		}
		return "struct"
	case *types.Interface:
		if !u.IsMethodSet() {
			break
		}
		for i := range u.NumMethods() {
			if !u.Method(i).Exported() {
				return "interface; sealed"
			}
		}
		return "interface"
	}
	return f.typ(named.Underlying())
}

func (f formatter) typ(t types.Type) string {
	switch t := types.Unalias(t).(type) {
	case *types.Basic:
		if t.Kind() == types.UnsafePointer {
			return "unsafe.Pointer"
		}
		// The kind's own name: byte and rune are aliases of uint8 and int32.
		return types.Typ[t.Kind()].Name()
	case *types.Pointer:
		return "*" + f.typ(t.Elem())
	case *types.Slice:
		return "[]" + f.typ(t.Elem())
	case *types.Array:
		return fmt.Sprintf("[%d]%s", t.Len(), f.typ(t.Elem()))
	case *types.Map:
		return "map[" + f.typ(t.Key()) + "]" + f.typ(t.Elem())
	case *types.Chan:
		return f.chanType(t)
	case *types.Signature:
		return "func" + f.signature(t)
	case *types.Named:
		return f.named(t)
	case *types.TypeParam:
		return t.Obj().Name()
	case *types.Struct:
		return f.structType(t)
	case *types.Interface:
		return f.interfaceType(t)
	case *types.Union:
		return f.union(t)
	}
	return types.TypeString(t, f.qualifier)
}

func (f formatter) qualifier(pkg *types.Package) string {
	if pkg == f.pkg {
		return ""
	}
	return pkg.Path()
}

func (f formatter) named(t *types.Named) string {
	name := t.Obj().Name()
	if pkg := t.Obj().Pkg(); pkg != nil && f.qualifier(pkg) != "" {
		name = f.qualifier(pkg) + "." + name
	}
	if args := t.TypeArgs(); args.Len() > 0 {
		name += "[" + f.join(args.Len(), args.At) + "]"
	}
	return name
}

// join formats n types and joins them with ", ".
func (f formatter) join(n int, at func(int) types.Type) string {
	list := make([]string, n)
	for i := range list {
		list[i] = f.typ(at(i))
	}
	return strings.Join(list, ", ")
}

func (f formatter) chanType(t *types.Chan) string {
	switch t.Dir() {
	case types.SendOnly:
		return "chan<- " + f.typ(t.Elem())
	case types.RecvOnly:
		return "<-chan " + f.typ(t.Elem())
	}
	if elem, ok := types.Unalias(t.Elem()).(*types.Chan); ok && elem.Dir() == types.RecvOnly {
		return "chan (" + f.typ(elem) + ")"
	}
	return "chan " + f.typ(t.Elem())
}

// signature writes type parameters, parameter types and result types.
func (f formatter) signature(s *types.Signature) string {
	params := make([]string, s.Params().Len())
	for i := range params {
		t := s.Params().At(i).Type()
		if slice, ok := t.(*types.Slice); ok && s.Variadic() && i == len(params)-1 {
			params[i] = "..." + f.typ(slice.Elem())
		} else {
			params[i] = f.typ(t)
		}
	}
	out := f.typeParams(s.TypeParams()) + "(" + strings.Join(params, ", ") + ")"
	results := s.Results()
	at := func(i int) types.Type { return results.At(i).Type() }
	switch results.Len() {
	case 0:
		return out
	case 1:
		return out + " " + f.typ(at(0))
	}
	return out + " (" + f.join(results.Len(), at) + ")"
}

func (f formatter) typeParams(list *types.TypeParamList) string {
	if list.Len() == 0 {
		return ""
	}
	params := make([]string, list.Len())
	for i := range params {
		tp := list.At(i)
		params[i] = tp.Obj().Name() + " " + f.constraint(tp.Constraint())
	}
	return "[" + strings.Join(params, ", ") + "]"
}

// constraint writes [T ~int] and [T interface{~int}], or [T comparable] and
// [T interface{comparable}], the same way.
func (f formatter) constraint(t types.Type) string {
	if iface, ok := types.Unalias(t).(*types.Interface); ok && iface.NumExplicitMethods() == 0 && iface.NumEmbeddeds() == 1 {
		return f.constraint(iface.EmbeddedType(0))
	}
	return f.typ(t)
}

func (f formatter) structType(t *types.Struct) string {
	list := make([]string, t.NumFields())
	for i := range list {
		field := t.Field(i)
		if field.Embedded() {
			list[i] = f.typ(field.Type())
		} else {
			list[i] = field.Name() + " " + f.typ(field.Type())
		}
		if tag := t.Tag(i); tag != "" {
			list[i] += " " + fmt.Sprintf("%q", tag)
		}
	}
	return "struct{" + strings.Join(list, "; ") + "}"
}

// interfaceType writes the complete method set, so embedding an interface and
// declaring its methods read the same, then the other elements, sorted.
func (f formatter) interfaceType(t *types.Interface) string {
	var methods, elems []string
	for i := range t.NumMethods() {
		m := t.Method(i)
		methods = append(methods, m.Name()+f.signature(m.Signature()))
	}
	for i := range t.NumEmbeddeds() {
		embedded := t.EmbeddedType(i)
		if iface, ok := embedded.Underlying().(*types.Interface); ok && iface.IsMethodSet() {
			continue
		}
		elems = append(elems, f.typ(embedded))
	}
	slices.Sort(elems)
	if all := append(methods, elems...); len(all) > 0 {
		return "interface{" + strings.Join(all, "; ") + "}"
	}
	return "any"
}

// union sorts its terms: their order does not change the type set.
func (f formatter) union(t *types.Union) string {
	terms := make([]string, t.Len())
	for i := range terms {
		term := t.Term(i)
		terms[i] = f.typ(term.Type())
		if term.Tilde() {
			terms[i] = "~" + terms[i]
		}
	}
	slices.Sort(terms)
	return strings.Join(terms, " | ")
}
