//! External, syntax-only Rust graph. No project build or macro execution.
use quote::ToTokens;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{self, Read},
    path::{Path, PathBuf},
};
use syn::{Item, UseTree, visit::Visit};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

struct Module {
    id: String,
    path: String,
    symbols: BTreeSet<String>,
    uses: Vec<Vec<String>>,
}

fn ident(text: impl ToString) -> String {
    text.to_string().trim_start_matches("r#").to_string()
}
fn tokens(value: &impl ToTokens) -> String {
    value.to_token_stream().to_string()
}

fn use_paths(tree: &UseTree, prefix: Vec<String>, out: &mut Vec<Vec<String>>) {
    match tree {
        UseTree::Path(p) => {
            let mut next = prefix;
            next.push(ident(&p.ident));
            use_paths(&p.tree, next, out);
        }
        UseTree::Group(g) => {
            for tree in &g.items {
                use_paths(tree, prefix.clone(), out);
            }
        }
        UseTree::Name(n) => {
            let mut next = prefix;
            next.push(ident(&n.ident));
            out.push(next);
        }
        UseTree::Rename(n) => {
            let mut next = prefix;
            next.push(ident(&n.ident));
            out.push(next);
        }
        UseTree::Glob(_) => out.push(prefix),
    }
}

#[derive(Default)]
struct Uses(Vec<Vec<String>>);
impl<'ast> Visit<'ast> for Uses {
    // Nested modules are visited independently with their own namespace.
    fn visit_item_mod(&mut self, _: &'ast syn::ItemMod) {}
    fn visit_item_use(&mut self, item: &'ast syn::ItemUse) {
        use_paths(&item.tree, vec![], &mut self.0);
    }
}

fn declaration(item: &Item) -> Option<String> {
    // Keep import/module declarations even when private: changing them must
    // invalidate outgoing graph edges. Public signatures omit bodies.
    match item {
        Item::Use(v) => Some(tokens(v)),
        Item::Mod(v) => Some(format!("{} mod {} ;", tokens(&v.vis), v.ident)),
        Item::Fn(v) if !matches!(v.vis, syn::Visibility::Inherited) => {
            Some(format!("{} {}", tokens(&v.vis), tokens(&v.sig)))
        }
        Item::Struct(v) if !matches!(v.vis, syn::Visibility::Inherited) => Some(tokens(v)),
        Item::Enum(v) if !matches!(v.vis, syn::Visibility::Inherited) => Some(tokens(v)),
        Item::Type(v) if !matches!(v.vis, syn::Visibility::Inherited) => Some(tokens(v)),
        Item::Trait(v) => {
            let mut v = v.clone();
            for member in &mut v.items {
                if let syn::TraitItem::Fn(f) = member {
                    f.default = None;
                    f.semi_token = Some(Default::default());
                }
            }
            Some(tokens(&v))
        }
        Item::Impl(v) => {
            let mut v = v.clone();
            for member in &mut v.items {
                if let syn::ImplItem::Fn(f) = member {
                    f.block.stmts.clear();
                }
            }
            Some(tokens(&v))
        }
        _ => None,
    }
}

#[expect(
    clippy::too_many_arguments,
    reason = "recursive syntax walk carries module identity and graph accumulators"
)]
fn scan(
    items: &[Item],
    file: &Path,
    directory: &Path,
    root: &Path,
    ns: Vec<String>,
    crate_id: &str,
    modules: &mut BTreeMap<Vec<String>, Module>,
    links: &mut BTreeSet<(String, String, String)>,
    active: &mut BTreeSet<PathBuf>,
) -> Result<()> {
    let relative = file
        .strip_prefix(root)?
        .to_string_lossy()
        .replace('\\', "/");
    let id = format!("rs:{}/{}", crate_id, ns.join("/"));
    let mut uses = Uses::default();
    for item in items {
        uses.visit_item(item);
    }
    let mut symbols: BTreeSet<_> = items.iter().filter_map(declaration).collect();
    // Function-local imports also affect outgoing edges.
    for path in &uses.0 {
        symbols.insert(format!("use {}", path.join("::")));
    }
    symbols.insert("mod".into()); // distinguish a valid empty module from parse failure
    modules.insert(
        ns.clone(),
        Module {
            id: id.clone(),
            path: relative,
            symbols,
            uses: uses.0,
        },
    );
    for item in items {
        let Item::Mod(m) = item else {
            continue;
        };
        let name = ident(&m.ident);
        let mut child = ns.clone();
        child.push(name.clone());
        let child_id = format!("rs:{}/{}", crate_id, child.join("/"));
        if let Some((_, items)) = &m.content {
            scan(
                items,
                file,
                &directory.join(&name),
                root,
                child.clone(),
                crate_id,
                modules,
                links,
                active,
            )?;
        } else {
            let mut explicit = None;
            for attr in &m.attrs {
                if attr.path().is_ident("path")
                    && let syn::Meta::NameValue(v) = &attr.meta
                    && let syn::Expr::Lit(v) = &v.value
                    && let syn::Lit::Str(s) = &v.lit
                {
                    explicit = Some(directory.join(s.value()));
                }
            }
            let candidates = explicit.map(|p| vec![p]).unwrap_or_else(|| {
                vec![
                    directory.join(format!("{name}.rs")),
                    directory.join(&name).join("mod.rs"),
                ]
            });
            let Some(path) = candidates.iter().find(|p| p.is_file()) else {
                continue;
            };
            let canonical = path.canonicalize()?;
            if !canonical.starts_with(root) || !active.insert(canonical.clone()) {
                return Err("module escapes workspace or has a recursive path".into());
            }
            let source = fs::read_to_string(&canonical)?;
            let parsed = syn::parse_file(&source)?;
            let next_dir = if path.file_name().is_some_and(|n| n == "mod.rs") {
                path.parent().unwrap().to_path_buf()
            } else {
                path.with_extension("")
            };
            scan(
                &parsed.items,
                &canonical,
                &next_dir,
                root,
                child.clone(),
                crate_id,
                modules,
                links,
                active,
            )?;
            active.remove(&canonical);
        }
        if modules.contains_key(&child) {
            links.insert((id.clone(), child_id, "imports".into()));
        }
    }
    Ok(())
}

fn resolve(
    path: &[String],
    ns: &[String],
    modules: &BTreeMap<Vec<String>, Module>,
    crate_name: &str,
) -> Option<String> {
    let (mut base, mut offset) = (vec![], 0);
    if path
        .first()
        .is_some_and(|p| p == "crate" || p == crate_name)
    {
        offset = 1;
    } else if path.first().is_some_and(|p| p == "self") {
        base = ns.to_vec();
        offset = 1;
    } else if path.first().is_some_and(|p| p == "super") {
        base = ns.to_vec();
        while path.get(offset).is_some_and(|p| p == "super") {
            base.pop()?;
            offset += 1;
        }
    } else {
        // Rust 2018 use paths are crate-root-relative. Unknown external roots
        // are ignored rather than guessed as symbols of the current module.
        modules.get(&vec![path.first()?.clone()])?;
    }
    base.extend_from_slice(&path[offset..]);
    while !modules.contains_key(&base) {
        base.pop()?;
    }
    // An unresolved symbol in the current root is not a module dependency.
    if base.is_empty() && offset == 0 {
        return None;
    }
    Some(modules.get(&base)?.id.clone())
}

fn extract(metadata: &Value) -> Result<Value> {
    let root = PathBuf::from(
        metadata["workspace_root"]
            .as_str()
            .ok_or("workspace_root missing")?,
    )
    .canonicalize()?;
    let packages = metadata["packages"].as_array().ok_or("packages missing")?;
    let members = metadata["workspace_members"]
        .as_array()
        .ok_or("workspace_members missing")?;
    let packages: Vec<_> = packages
        .iter()
        .filter(|p| members.contains(&p["id"]))
        .collect();
    let mut nodes = BTreeMap::new();
    let mut links = BTreeSet::new();
    let mut manifests = BTreeMap::new();
    for p in &packages {
        let manifest =
            PathBuf::from(p["manifest_path"].as_str().ok_or("manifest missing")?).canonicalize()?;
        let relative = manifest
            .strip_prefix(&root)?
            .to_string_lossy()
            .replace('\\', "/");
        let id = format!("rs-crate:{relative}");
        manifests.insert(
            (
                manifest.parent().unwrap().to_path_buf(),
                p["name"].as_str().unwrap_or("").to_string(),
            ),
            id.clone(),
        );
        nodes.insert(
            id.clone(),
            json!({"id":id,"path":relative,"name":p["name"],"kind":"package"}),
        );
    }
    for p in &packages {
        let manifest = PathBuf::from(p["manifest_path"].as_str().unwrap()).canonicalize()?;
        let relative = manifest
            .strip_prefix(&root)?
            .to_string_lossy()
            .replace('\\', "/");
        let crate_id = format!("rs-crate:{relative}");
        if let Some(dependencies) = p["dependencies"].as_array() {
            for d in dependencies {
                if let (Some(path), Some(name)) = (d["path"].as_str(), d["name"].as_str())
                    && let Ok(directory) = Path::new(path).canonicalize()
                    && let Some(target) = manifests.get(&(directory, name.to_string()))
                {
                    links.insert((crate_id.clone(), target.clone(), "depends_on".into()));
                }
            }
        }
        for target in p["targets"].as_array().ok_or("targets missing")? {
            let file = PathBuf::from(target["src_path"].as_str().ok_or("src_path missing")?)
                .canonicalize()?;
            if !file.starts_with(&root) {
                return Err("target escapes workspace".into());
            }
            let name = target["name"].as_str().ok_or("target name missing")?;
            // Manifest and target identity avoid collisions across workspaces,
            // lib/bin targets and packages whose names differ only in punctuation.
            let target_id = format!(
                "{relative}/{name}/{}",
                file.strip_prefix(&root)?.to_string_lossy()
            );
            let source = fs::read_to_string(&file)?;
            let parsed = syn::parse_file(&source)?;
            let mut modules = BTreeMap::new();
            let mut active = BTreeSet::from([file.clone()]);
            scan(
                &parsed.items,
                &file,
                file.parent().unwrap(),
                &root,
                vec![],
                &target_id,
                &mut modules,
                &mut links,
                &mut active,
            )?;
            for (ns, module) in &modules {
                for path in &module.uses {
                    if let Some(target) = resolve(path, ns, &modules, name)
                        && target != module.id
                    {
                        links.insert((module.id.clone(), target, "imports".into()));
                    }
                }
                nodes.insert(module.id.clone(), json!({"id":module.id,"path":module.path,"name":ns.last().map(String::as_str).unwrap_or(name),"kind":"module","symbols":module.symbols}));
            }
        }
    }
    Ok(
        json!({"version":1,"nodes":nodes.into_values().collect::<Vec<_>>(),"edges":links.into_iter().map(|(source,target,kind)| json!({"source":source,"target":target,"kind":kind})).collect::<Vec<_>>()}),
    )
}

fn main() {
    let result = (|| -> Result<()> {
        let mut raw = String::new();
        io::stdin().read_to_string(&mut raw)?;
        let metadata: Value = serde_json::from_str(&raw)?;
        println!("{}", extract(&metadata)?);
        Ok(())
    })();
    if let Err(error) = result {
        eprintln!("rsgraph: {error}");
        std::process::exit(1);
    }
}
