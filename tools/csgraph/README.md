# C# graph extractor

Publish once **inside devtools**, using its installed .NET 10 SDK and Roslyn
compiler libraries. No NuGet packages or network feeds are needed:

```sh
dotnet publish tools/csgraph/csgraph.csproj -c Release -p:NuGetAudit=false \
  -o /home/sas/.containers/containers/devtools/.local/lib/aconsole-csgraph
```

Hostrun executes `dotnet exec` with that absolute `csgraph.dll` path and the
repository-relative input as separate arguments. Keep the whole publish
directory, including runtimeconfig, deps and the two Roslyn assemblies.
Missing DLL/runtime fails normally; there is no automatic build or restore.
The updated hostrun action must be activated separately in the main checkout.

Input is an SDK-style `.csproj`, `.sln`, `.slnx`, or a directory containing
projects. ProjectReference XML is followed recursively without MSBuild or
Workspaces. Roslyn compilation references preserve semantic identities between
projects without emitting or loading project assemblies. Project build tasks,
analyzers and generators are never run. Default recursive C# source selection
excludes bin, obj and .git. Custom Compile, Import, DefineConstants,
EnableDefaultCompileItems=false and conditional ProjectReference are rejected.
Directory.Build.props/targets, NuGet packages, custom build configurations and
multitargeting are not evaluated; this extractor supports the default source
graph, not arbitrary MSBuild solutions. Symlink inputs/sources and references
outside the repository are rejected. Runtime framework metadata resolves basic
system types; unresolved external bases create no edge.

Output is deterministic graph protocol v1: one module per source file with
`cs:<repository-relative path>` identity, sorted declaration signatures without
bodies, direct `inherits` and `implements` edges between source files. Interface
inheritance is `inherits`; a class or struct's interface base is `implements`.
Same-file relations need no module edge. A syntax error preserves the module
with empty symbols and no outgoing edges. Errors go to stderr with nonzero
status and no partial graph on stdout.

`tests/hostrun/test_csharp_info.py` publishes an isolated copy of the extractor
and tests two-project semantic references, repeatability after deleting build
outputs, project task isolation, syntax errors and unsupported/cyclic projects.
Run the tests in devtools so .NET integration checks are executed.

Native AOT is only a probe; see `artifacts/0.1.4/csgraph-aot.md`. The release
path is the framework-dependent DLL.
