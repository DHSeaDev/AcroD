"""Qt lifetime scan (stdlib only). Exit 0 = clean, 1 = findings, 2 = scan error.

(A) a bound method of self, or a self-capturing lambda, STORED on an object
    (`self.hook = self.method`, `child.on_x = self.method`), or passed to a
    same-file Qt class whose __init__ stores that parameter.
(B) a self-capturing lambda passed to .connect(...) / .addAction(...).
Both leave a reference cycle or an immortal widget; see preship §Custom PySide6.
"""
import ast, sys

QT_BASES = ("Q",)


def _refs_self(node):
    # lambda parameters named self do not count; default values are evaluated
    # outside the lambda, so `lambda p=item:` does not capture anything.
    return any(isinstance(n, ast.Name) and n.id == "self" for n in ast.walk(node.body))


def _is_qt_class(cls):
    for b in cls.bases:
        name = b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")
        if name.startswith(QT_BASES) or name in ("_WorkerPanelMixin",):
            return True
    return False


def scan(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    findings = []
    classes = {c.name: c for c in ast.walk(tree) if isinstance(c, ast.ClassDef)}
    storing = {}                      # class -> set of __init__ param names stored on self
    for name, c in classes.items():
        if not _is_qt_class(c):
            continue
        for fn in c.body:
            if isinstance(fn, ast.FunctionDef) and fn.name == "__init__":
                params = [a.arg for a in fn.args.args[1:]]
                kept = set()
                for n in ast.walk(fn):
                    if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Name)
                            and n.value.id in params
                            and any(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                                    and t.value.id == "self" for t in n.targets)):
                        kept.add(n.value.id)
                storing[name] = (params, kept)

    def is_self_method(v):
        return (isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name)
                and v.value.id == "self")

    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Attribute) and (
                        (is_self_method(n.value) and not isinstance(n.value.ctx, ast.Store)
                         and _looks_like_callable(n.value, classes))
                        or (isinstance(n.value, ast.Lambda) and _refs_self(n.value))):
                    findings.append((n.lineno, "A", ast.unparse(n)[:110]))
        elif isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in ("connect", "addAction"):
                for a in list(n.args) + [k.value for k in n.keywords]:
                    if isinstance(a, ast.Lambda) and _refs_self(a):
                        findings.append((n.lineno, "B", ast.unparse(n)[:110]))
            cname = f.id if isinstance(f, ast.Name) else None
            if cname in storing:
                params, kept = storing[cname]
                for i, a in enumerate(n.args):
                    if (i < len(params) and params[i] in kept and is_self_method(a)
                            and _looks_like_callable(a, classes)):
                        findings.append((n.lineno, "A", ast.unparse(n)[:110]))
                for k in n.keywords:
                    if (k.arg in kept and is_self_method(k.value)
                            and _looks_like_callable(k.value, classes)):
                        findings.append((n.lineno, "A", ast.unparse(n)[:110]))
    return sorted(set(findings))


def _looks_like_callable(attr, classes):
    # self.<name> where <name> is a method defined on some class in the file
    return any(isinstance(m, ast.FunctionDef) and m.name == attr.attr
               for c in classes.values() for m in c.body)


if __name__ == "__main__":
    bad = 0
    for p in sys.argv[1:]:
        for line, kind, text in scan(p):
            bad += 1
            print(f"{p}:{line}: [{kind}] {text}")
    print(f"{bad} finding(s)")
    sys.exit(1 if bad else 0)
