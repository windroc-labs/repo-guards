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
	entries, err := generate(stderr)
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

func generate(stderr io.Writer) ([]entry, error) {
	units, exports, err := listPackages(stderr)
	if err != nil {
		return nil, err
	}
	imp := importer.ForCompiler(token.NewFileSet(), "gc", func(path string) (io.ReadCloser, error) {
		if exports[path] == "" {
			return nil, fmt.Errorf("no export data for %s", path)
		}
		return os.Open(exports[path])
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
		entries = append(entries, packageEntries(pkg, candidates)...)
	}
	slices.SortFunc(entries, func(a, b entry) int {
		return slices.Compare([]string{a.Unit, a.Parent, a.Kind, a.Name}, []string{b.Unit, b.Parent, b.Kind, b.Name})
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

func packageEntries(pkg *types.Package, candidates []*types.Named) []entry {
	f := formatter{pkg}
	var entries []entry
	add := func(parent, kind, name, signature string) {
		entries = append(entries, entry{pkg.Path(), parent, kind, name, signature})
	}
	scope := pkg.Scope()
	for _, name := range scope.Names() {
		switch obj := scope.Lookup(name).(type) {
		case *types.Const:
			if obj.Exported() {
				add("", "const", name, "const "+name+" "+f.typ(obj.Type()))
			}
		case *types.Var:
			if obj.Exported() {
				add("", "var", name, "var "+name+" "+f.typ(obj.Type()))
			}
		case *types.Func:
			if obj.Exported() {
				add("", "func", name, "func "+name+f.signature(obj.Signature()))
			}
		case *types.TypeName:
			if !obj.Exported() {
				continue
			}
			add("", "type", name, f.typeDecl(obj, candidates))
			if named, ok := obj.Type().(*types.Named); ok {
				for _, m := range f.members(named) {
					add(name, m.Kind, m.Name, m.Signature)
				}
			}
		}
	}
	return entries
}

// formatter writes types for entries of one unit: no parameter names, aliases
// resolved, named types qualified by import path unless declared in the unit.
type formatter struct{ pkg *types.Package }

func (f formatter) typeDecl(obj *types.TypeName, candidates []*types.Named) string {
	if alias, ok := obj.Type().(*types.Alias); ok {
		return "type " + obj.Name() + f.typeParams(alias.TypeParams()) + " = " + f.typ(alias.Rhs())
	}
	named := obj.Type().(*types.Named)
	return "type " + obj.Name() + f.typeParams(named.TypeParams()) + " " + f.underlying(named) + f.implements(named, candidates)
}

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

func (f formatter) implements(named *types.Named, candidates []*types.Named) string {
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
// promoted ones included. A generic type is instantiated with its own type
// parameters, so members use the declaration's names, not each receiver's.
func (f formatter) members(named *types.Named) []entry {
	t := types.Type(named)
	if params := named.TypeParams(); params.Len() > 0 {
		args := make([]types.Type, params.Len())
		for i := range args {
			args[i] = params.At(i)
		}
		if inst, err := types.Instantiate(nil, named, args, false); err == nil {
			t = inst
		}
	}
	recv := f.typ(t)
	var out []entry
	if iface, ok := named.Underlying().(*types.Interface); ok {
		for i := range iface.NumMethods() {
			if m := iface.Method(i); m.Exported() {
				out = append(out, entry{Kind: "method", Name: m.Name(), Signature: "func (" + recv + ") " + m.Name() + f.signature(m.Signature())})
			}
		}
		return out
	}
	values := types.NewMethodSet(t)
	pointers := types.NewMethodSet(types.NewPointer(t))
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
		out = append(out, entry{Kind: "method", Name: m.Name(), Signature: "func (" + r + ") " + m.Name() + f.signature(sel.Type().(*types.Signature))})
	}
	for _, name := range fieldNames(t.Underlying(), map[types.Type]bool{}) {
		// The lookup applies Go's selector rules: the shallowest field wins,
		// a name ambiguous at that depth or shadowed by a method is not selectable.
		field, ok := lookupField(t, f.pkg, name)
		if !ok {
			continue
		}
		sig := field.Name() + " " + f.typ(field.Type())
		if field.Embedded() {
			sig = "embedded " + f.typ(field.Type())
		}
		out = append(out, entry{Kind: "field", Name: name, Signature: sig})
	}
	return out
}

func lookupField(t types.Type, pkg *types.Package, name string) (*types.Var, bool) {
	obj, _, _ := types.LookupFieldOrMethod(t, true, pkg, name)
	field, ok := obj.(*types.Var)
	return field, ok && field.IsField()
}

// fieldNames lists the exported field names of a struct and of the structs it
// embeds, at any depth.
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
	slices.Sort(out)
	return slices.Compact(out)
}

func (f formatter) typ(t types.Type) string {
	switch t := types.Unalias(t).(type) {
	case *types.Basic:
		if t.Kind() == types.UnsafePointer {
			return "unsafe.Pointer"
		}
		return t.Name()
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
	if pkg := t.Obj().Pkg(); pkg != nil && pkg != f.pkg {
		name = pkg.Path() + "." + name
	}
	if args := t.TypeArgs(); args.Len() > 0 {
		list := make([]string, args.Len())
		for i := range list {
			list[i] = f.typ(args.At(i))
		}
		name += "[" + strings.Join(list, ", ") + "]"
	}
	return name
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
	switch results.Len() {
	case 0:
		return out
	case 1:
		return out + " " + f.typ(results.At(0).Type())
	}
	list := make([]string, results.Len())
	for i := range list {
		list[i] = f.typ(results.At(i).Type())
	}
	return out + " (" + strings.Join(list, ", ") + ")"
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

// constraint writes [T ~int] and [T interface{~int}] the same way.
func (f formatter) constraint(t types.Type) string {
	if iface, ok := types.Unalias(t).(*types.Interface); ok && iface.NumMethods() == 0 && iface.NumEmbeddeds() == 1 {
		if _, isIface := iface.EmbeddedType(0).Underlying().(*types.Interface); !isIface {
			return f.typ(iface.EmbeddedType(0))
		}
	}
	return f.typ(t)
}

func (f formatter) structType(t *types.Struct) string {
	fields := make([]string, t.NumFields())
	for i := range fields {
		field := t.Field(i)
		if field.Embedded() {
			fields[i] = f.typ(field.Type())
		} else {
			fields[i] = field.Name() + " " + f.typ(field.Type())
		}
		if tag := t.Tag(i); tag != "" {
			fields[i] += " " + fmt.Sprintf("%q", tag)
		}
	}
	return "struct{" + strings.Join(fields, "; ") + "}"
}

// interfaceType writes the complete method set, so embedding an interface and
// declaring its methods read the same, plus any type terms.
func (f formatter) interfaceType(t *types.Interface) string {
	var elems []string
	for i := range t.NumMethods() {
		m := t.Method(i)
		elems = append(elems, m.Name()+f.signature(m.Signature()))
	}
	for i := range t.NumEmbeddeds() {
		embedded := t.EmbeddedType(i)
		if iface, ok := embedded.Underlying().(*types.Interface); ok && iface.IsMethodSet() {
			continue
		}
		elems = append(elems, f.typ(embedded))
	}
	if len(elems) == 0 {
		return "any"
	}
	return "interface{" + strings.Join(elems, "; ") + "}"
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
