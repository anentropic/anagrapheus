"""Generate API reference pages for all modules."""

from pathlib import Path

import mkdocs_gen_files


def main():
    src = Path("src")
    nav = mkdocs_gen_files.Nav()

    for path in sorted(src.rglob("*.py")):
        module_path = path.relative_to(src).with_suffix("")
        doc_path = path.relative_to(src).with_suffix(".md")
        # A module named ``index`` would become its section's landing page under
        # navigation.indexes, so give its page another name.
        if doc_path.stem == "index":
            doc_path = doc_path.with_name("index-module.md")
        full_doc_path = Path("reference", doc_path)

        parts = tuple(module_path.parts)

        # The top-level package gets a page of its own: it defines
        # register_builtin_events, which no submodule page would show.
        if parts == ("anagrapheus", "__init__"):
            parts = ("anagrapheus",)
            doc_path = Path("anagrapheus", "index.md")
            full_doc_path = Path("reference", doc_path)

        # Skip __init__, __main__, conftest, and private modules
        if parts[-1] in ("__init__", "__main__", "conftest"):
            continue
        if any(part.startswith("_") for part in parts):
            continue

        nav[parts] = doc_path.as_posix()

        with mkdocs_gen_files.open(full_doc_path, "w") as fd:
            ident = ".".join(parts)
            fd.write(f"# `{ident}`\n\n::: {ident}\n")

        mkdocs_gen_files.set_edit_path(full_doc_path, path)

    with mkdocs_gen_files.open("reference/SUMMARY.md", "w") as nav_file:
        # The hand-written landing page comes first, so it becomes the section's index.
        if Path("docs/src/reference/index.md").exists():
            nav_file.write("* [Reference](index.md)\n")
        nav_file.writelines(nav.build_literate_nav())


# mkdocs-gen-files executes this script via runpy.run_path() without setting
# run_name="__main__", so call main() unconditionally rather than guarding it.
main()
