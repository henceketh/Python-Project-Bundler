import os
import sys
import ast
from collections import defaultdict, deque
import pprint

debug = False

def dprint(*args, **kwargs):
    if debug:
        print(*args, **kwargs)

def build_module_mapping(project_dir):
    mapping = {}
    for root, dirs, files in os.walk(project_dir):
        if "__pycache__" in root:
            continue
        for file in files:
            if file.endswith(".py"):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, project_dir)
                parts = rel_path.split(os.sep)
                if file == "__init__.py":
                    mod_name = "__init__" if len(parts) == 1 else ".".join(parts[:-1])
                else:
                    mod_name = ".".join(parts)[:-3]
                mapping[mod_name] = full_path
    dprint("DEBUG: Module mapping:")
    dprint(mapping)
    return mapping

def extract_local_dependencies(module_name, file_path, local_modules):
    with open(file_path, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=file_path)
    local_deps = set()

    def is_local(mod_name):
        result = mod_name in local_modules or any(mod_name.startswith(lm + ".") for lm in local_modules)
        dprint(f"DEBUG: Проверка is_local для '{mod_name}' в модуле '{module_name}': {result}")
        return result

    class LocalImportRemover(ast.NodeTransformer):
        def visit_Import(self, node):
            new_names = []
            for alias in node.names:
                dprint(f"DEBUG: В модуле '{module_name}' нашли import: {alias.name}")
                if is_local(alias.name):
                    base = alias.name.split('.')[0]
                    local_deps.add(base)
                    dprint(f"DEBUG: Импорт {alias.name} признан локальным и удалён.")
                else:
                    new_names.append(alias)
            if new_names:
                node.names = new_names
                return node
            else:
                dprint(f"DEBUG: Все импорты в модуле '{module_name}' удалены.")
                return None

        def visit_ImportFrom(self, node):
            if node.module:
                dprint(f"DEBUG: В модуле '{module_name}' нашли from-import: {node.module}")
                if is_local(node.module):
                    base = node.module.split('.')[0]
                    local_deps.add(base)
                    dprint(f"DEBUG: From-import {node.module} признан локальным и удалён.")
                    return None
            return node

    transformer = LocalImportRemover()
    new_tree = transformer.visit(tree)
    ast.fix_missing_locations(new_tree)
    dprint(f"DEBUG: Модуль '{module_name}' зависит от: {local_deps}")
    return local_deps, new_tree

def to_source(tree):
    return ast.unparse(tree)

def build_dependency_graph(module_mapping):
    graph = {}
    modules_ast = {}
    local_mods = set(module_mapping.keys())
    dprint("DEBUG: Local modules:", local_mods)
    for mod, path in module_mapping.items():
        deps, new_tree = extract_local_dependencies(mod, path, local_mods)
        filtered = set()
        for d in deps:
            if d in local_mods:
                filtered.add(d)
            else:
                base = d.split(".")[0]
                if base in local_mods:
                    filtered.add(base)
        graph[mod] = filtered
        modules_ast[mod] = new_tree
    dprint("DEBUG: Граф зависимостей:")
    dprint(graph)
    return graph, modules_ast

def topological_sort(graph):
    all_nodes = set(graph.keys())
    in_degree = {node: len(graph[node]) for node in all_nodes}
    reverse_graph = defaultdict(set)
    for mod, deps in graph.items():
        for dep in deps:
            reverse_graph[dep].add(mod)

    queue = deque([n for n in all_nodes if in_degree[n] == 0])
    sorted_order = []
    while queue:
        node = queue.popleft()
        sorted_order.append(node)
        for dependent in reverse_graph[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)
    if len(sorted_order) != len(all_nodes):
        raise ValueError("DEBUG: Обнаружен цикл в зависимостях модулей!")
    dprint("DEBUG: Отсортированный порядок модулей:")
    dprint(sorted_order)
    return sorted_order

def assemble_code(sorted_modules, modules_ast):
    combined = []
    for mod in sorted_modules:
        source = to_source(modules_ast[mod])
        module_block = f"# --- MODULE: {mod} ---\n{source}\n"
        combined.append(module_block)
    final_code = "\n".join(combined)
    dprint("DEBUG: Собранный итоговый код (начало):")
    dprint(final_code[:500])
    return final_code

def main():
    global debug

    if "-d" in sys.argv:
        debug = True
        sys.argv.remove("-d")

    if len(sys.argv) < 4:
        print("Использование: python bundler.py <папка_проекта> <точка_входа> <итоговый_файл.py> [-d]")
        sys.exit(1)

    project_dir = os.path.abspath(sys.argv[1])
    entry_file = os.path.abspath(os.path.join(project_dir, sys.argv[2]))
    output_file = os.path.abspath(sys.argv[3])

    if not os.path.exists(project_dir):
        print(f"[Ошибка] Папка проекта не найдена: {project_dir}")
        sys.exit(1)
    if not os.path.isfile(entry_file):
        print(f"[Ошибка] Точка входа не найдена: {entry_file}")
        sys.exit(1)

    module_mapping = build_module_mapping(project_dir)

    entry_rel = os.path.relpath(entry_file, project_dir)
    if os.path.basename(entry_file) == "__init__.py":
        parts = entry_rel.split(os.sep)[:-1]
    else:
        parts = entry_rel.split(os.sep)
        parts[-1] = parts[-1][:-3]
    entry_mod = ".".join(parts)

    if entry_mod not in module_mapping:
        module_mapping[entry_mod] = entry_file

    dep_graph, modules_ast = build_dependency_graph(module_mapping)
    sorted_mods = topological_sort(dep_graph)

    if entry_mod in sorted_mods:
        sorted_mods.remove(entry_mod)
        sorted_mods.append(entry_mod)

    combined_code = assemble_code(sorted_mods, modules_ast)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("# Holly damnmnnn.\n")
        f.write(combined_code)

    print(f"[OK] Сформирован единый файл: {output_file}")

if __name__ == "__main__":
    main()
