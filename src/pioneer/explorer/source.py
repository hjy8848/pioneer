"""[INDEPENDENT] AST extraction of [OFFICIAL] upstream source."""
import ast


def extract_tools(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RetailTools")
    methods = {}
    for node in cls.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        guards = []

        def walk(n, conditions=(), loops=()):
            if isinstance(n, ast.If):
                for child in n.body:
                    walk(child, (*conditions, ast.unparse(n.test)), loops)
                for child in n.orelse:
                    walk(child, (*conditions, f"not ({ast.unparse(n.test)})"), loops)
                return
            if isinstance(n, (ast.For, ast.While)):
                loop = ast.unparse(n.target) + " in " + ast.unparse(n.iter) if isinstance(n, ast.For) else ast.unparse(n.test)
                for child in n.body:
                    walk(child, conditions, (*loops, loop))
                for child in n.orelse:
                    walk(child, conditions, loops)
                return
            if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) and ast.unparse(n.exc.func) == "ValueError":
                guards.append(dict(line=n.lineno, error=ast.unparse(n.exc),
                                   failure_when=list(conditions), loops=list(loops),
                                   note="Path conditions; fall-through also depends on preceding returns."))
            for child in ast.iter_child_nodes(n):
                walk(child, conditions, loops)

        walk(node)
        kind = next((ast.unparse(d.args[0]).split(".")[-1].lower() for d in node.decorator_list
                     if isinstance(d, ast.Call) and ast.unparse(d.func) == "is_tool"), None)
        calls = sorted({n.func.attr for n in ast.walk(node) if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                        and n.func.value.id == "self"})
        methods[node.name] = dict(signature=f"{node.name}({ast.unparse(node.args)}) -> {ast.unparse(node.returns) if node.returns else 'Any'}",
                                  tool_type=kind, raises=guards, calls=calls,
                                  doc=ast.get_docstring(node), line=node.lineno, source="[OFFICIAL]")
    return methods


def extract_models(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    literals = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and ast.unparse(node.value) == "Literal":
            values = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            literals[str(node.lineno)] = [ast.literal_eval(v) for v in values]
    fields = {n.name: {f.target.id: ast.unparse(f.annotation) for f in n.body
                       if isinstance(f, ast.AnnAssign) and isinstance(f.target, ast.Name)}
              for n in tree.body if isinstance(n, ast.ClassDef)}
    return dict(fields=fields, literals=literals)
