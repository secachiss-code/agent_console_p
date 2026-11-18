using System.Text.Json;
using System.Text.RegularExpressions;
using System.Xml.Linq;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

// Project XML is data: never load MSBuild, tasks, analyzers or project assemblies.
try
{
    if (args.Length != 1) throw new InvalidOperationException("expected a project, solution or directory");
    var graph = new Extractor(Directory.GetCurrentDirectory());
    graph.Extract(Path.GetFullPath(args[0]));
}
catch (Exception error)
{
    Console.Error.WriteLine($"csgraph: {error.Message}");
    Environment.ExitCode = 1;
}

sealed class Extractor(string root)
{
    readonly Dictionary<string, CSharpCompilation> compilations = new(StringComparer.Ordinal);
    readonly HashSet<string> visiting = new(StringComparer.Ordinal);
    readonly Dictionary<string, SyntaxTree> trees = new(StringComparer.Ordinal);
    readonly Dictionary<string, SortedSet<string>> symbols = new(StringComparer.Ordinal);
    readonly HashSet<(string Source, string Target, string Kind)> edges = [];
    readonly MetadataReference[] framework = ((string?)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES") ?? "")
        .Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries)
        .Where(path => !Path.GetFileName(path).StartsWith("Microsoft.CodeAnalysis", StringComparison.Ordinal))
        .Select(path => MetadataReference.CreateFromFile(path)).ToArray();

    static bool SourcePath(string path) => !path.Split(Path.DirectorySeparatorChar)
        .Any(part => part is "bin" or "obj" or ".git");

    string Relative(string path)
    {
        var relative = Path.GetRelativePath(root, path).Replace('\\', '/');
        if (relative == ".." || relative.StartsWith("../", StringComparison.Ordinal))
            throw new InvalidOperationException("source is outside the repository");
        var current = root;
        foreach (var part in relative.Split('/'))
        {
            current = Path.Combine(current, part);
            if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("symlink source or project is unsupported");
        }
        return relative;
    }

    static IEnumerable<string> Projects(string input)
    {
        if (Directory.Exists(input))
            return Directory.GetFiles(input, "*.csproj", SearchOption.AllDirectories).Where(SourcePath);
        if (input.EndsWith(".csproj", StringComparison.OrdinalIgnoreCase)) return [input];
        var directory = Path.GetDirectoryName(input)!;
        if (input.EndsWith(".sln", StringComparison.OrdinalIgnoreCase))
            return Regex.Matches(File.ReadAllText(input), "\"([^\"]+\\.csproj)\"", RegexOptions.IgnoreCase)
                .Select(match => Path.GetFullPath(Path.Combine(directory, match.Groups[1].Value.Replace('\\', '/'))));
        if (input.EndsWith(".slnx", StringComparison.OrdinalIgnoreCase))
            return XDocument.Load(input).Descendants("Project").Select(element =>
                Path.GetFullPath(Path.Combine(directory, (string?)element.Attribute("Path")
                    ?? throw new InvalidOperationException("missing project path"))));
        throw new InvalidOperationException("unsupported input");
    }

    CSharpCompilation Load(string project)
    {
        project = Path.GetFullPath(project);
        _ = Relative(project);
        if (compilations.TryGetValue(project, out var cached)) return cached;
        if (!visiting.Add(project)) throw new InvalidOperationException("cyclic project references");
        var document = XDocument.Load(project);
        var directory = Path.GetDirectoryName(project)!;
        var elements = document.Descendants().ToArray();
        // Fail explicitly for source selection requiring MSBuild evaluation.
        if (elements.Any(element => element.Name.LocalName is "Compile" or "Import" or "DefineConstants"
            || (element.Name.LocalName == "EnableDefaultCompileItems" && element.Value != "true")))
            throw new InvalidOperationException("custom Compile/Import/DefineConstants requires unsupported MSBuild evaluation");
        var references = new List<MetadataReference>(framework);
        foreach (var reference in elements.Where(element => element.Name.LocalName == "ProjectReference"))
        {
            if (reference.Attribute("Condition") is not null || reference.Parent?.Attribute("Condition") is not null)
                throw new InvalidOperationException("conditional project reference is unsupported");
            var include = (string?)reference.Attribute("Include")
                ?? throw new InvalidOperationException("missing project reference");
            references.Add(Load(Path.GetFullPath(Path.Combine(directory, include.Replace('\\', '/')))).ToMetadataReference());
        }
        var files = Directory.GetFiles(directory, "*.cs", SearchOption.AllDirectories).Where(SourcePath)
            .Order(StringComparer.Ordinal).ToArray();
        var projectTrees = files.Select(file =>
        {
            _ = Relative(file);
            if (!trees.TryGetValue(file, out var tree))
            {
                tree = CSharpSyntaxTree.ParseText(File.ReadAllText(file), path: file);
                trees.Add(file, tree);
            }
            return tree;
        }).ToArray();
        var compilation = CSharpCompilation.Create(project, projectTrees, references,
            new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary));
        compilations.Add(project, compilation);
        visiting.Remove(project);
        return compilation;
    }

    public void Extract(string input)
    {
        var projects = Projects(input).Order(StringComparer.Ordinal).ToArray();
        if (projects.Length == 0) throw new InvalidOperationException("no C# projects found");
        foreach (var project in projects) Load(project);
        var display = SymbolDisplayFormat.CSharpErrorMessageFormat.WithMemberOptions(
            SymbolDisplayMemberOptions.IncludeContainingType | SymbolDisplayMemberOptions.IncludeType
            | SymbolDisplayMemberOptions.IncludeParameters | SymbolDisplayMemberOptions.IncludeModifiers);
        foreach (var compilation in compilations.OrderBy(pair => pair.Key, StringComparer.Ordinal).Select(pair => pair.Value))
        foreach (var tree in compilation.SyntaxTrees)
        {
            var path = tree.FilePath;
            if (!symbols.TryGetValue(path, out var signatures)) symbols[path] = signatures = new(StringComparer.Ordinal);
            // A malformed source keeps its file identity but has no interface or outgoing edges.
            if (tree.GetDiagnostics().Any(diagnostic => diagnostic.Severity == DiagnosticSeverity.Error)) continue;
            var model = compilation.GetSemanticModel(tree);
            foreach (var declaration in tree.GetRoot().DescendantNodes().OfType<BaseTypeDeclarationSyntax>())
            {
                if (model.GetDeclaredSymbol(declaration) is not INamedTypeSymbol type) continue;
                signatures.Add($"{type.TypeKind} {type.ToDisplayString(display)}");
                foreach (var member in type.GetMembers().Where(member => !member.IsImplicitlyDeclared
                    && member.DeclaredAccessibility is Accessibility.Public or Accessibility.Protected or Accessibility.ProtectedOrInternal))
                    signatures.Add(member.ToDisplayString(display));
                foreach (var baseSyntax in declaration.BaseList?.Types ?? [])
                {
                    if (model.GetTypeInfo(baseSyntax.Type).Type is not INamedTypeSymbol target
                        || target.TypeKind == TypeKind.Error) continue;
                    var kind = target.TypeKind == TypeKind.Interface && type.TypeKind != TypeKind.Interface
                        ? "implements" : "inherits";
                    signatures.Add($"{type.ToDisplayString()} {kind} {target.ToDisplayString()}");
                    foreach (var location in target.OriginalDefinition.Locations.Where(location => location.IsInSource))
                    {
                        var targetPath = location.SourceTree?.FilePath;
                        if (targetPath is not null && trees.ContainsKey(targetPath) && targetPath != path)
                            edges.Add((path, targetPath, kind));
                    }
                }
            }
        }
        var nodes = trees.Keys.Order(StringComparer.Ordinal).Select(path => new
        {
            id = "cs:" + Relative(path), path = Relative(path), name = Relative(path), kind = "module",
            symbols = symbols.GetValueOrDefault(path)?.ToArray() ?? []
        });
        var links = edges.OrderBy(edge => edge.Source, StringComparer.Ordinal)
            .ThenBy(edge => edge.Target, StringComparer.Ordinal).ThenBy(edge => edge.Kind, StringComparer.Ordinal)
            .Select(edge => new { source = "cs:" + Relative(edge.Source), target = "cs:" + Relative(edge.Target), kind = edge.Kind });
        Console.WriteLine(JsonSerializer.Serialize(new { version = 1, nodes, edges = links }));
    }
}
