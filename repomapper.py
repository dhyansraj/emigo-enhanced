#!/usr/bin/env python

"""
Repository Mapping and Analysis.

This module provides functionality to analyze a code repository, identify key
definitions and references using tree-sitter, and generate a concise "map"
of the codebase structure and relevant code snippets. This map is intended
to be included in the context provided to a Large Language Model (LLM) to
give it a better understanding of the project structure.

Based on code from the Aider project (https://github.com/paul-gauthier/aider),
this module implements:
- File discovery, respecting `.gitignore` and excluding binary/ignored files.
- Tag generation (definitions and references) using tree-sitter queries and
  pygments as a fallback.
- Caching of tags using `diskcache` to speed up repeated analysis.
- A ranking algorithm (PageRank) applied to the code dependency graph to
  identify the most relevant files and code elements based on context
  (e.g., files currently in chat, mentioned identifiers).
- Rendering of ranked code snippets using `grep-ast`'s `TreeContext`.
- Pruning the final map to fit within a specified token limit.

The main class `RepoMapper` is intended to be used by `session.py` to manage
the map generation for a specific user session. It also includes a command-line
interface for standalone usage and debugging.
"""

import argparse
import math
import os
import shutil
import sys # Import sys module
import time
from collections import Counter, defaultdict, namedtuple
from pathlib import Path

import tiktoken
from diskcache import Cache
from grep_ast import TreeContext, filename_to_lang
from pygments.lexers import guess_lexer_for_filename
from pygments.token import Token
import networkx as nx # Import networkx here

# --- Configuration Constants ---

IGNORED_DIRS = [
    r'\.emigo_repomap$',
    r'\.aider.*$',
    r'\.(git|hg|svn)$',
    r'__pycache__$',
    r'node_modules$',
    r'(\.venv|venv|\.env|env)$',
    r'(build|dist)$',
    r'vendor$'
]

BINARY_EXTS = {
    # Images
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.ico', '.svg',
    # Media
    '.mp3', '.mp4', '.mov', '.avi', '.mkv', '.wav',
    # Archives
    '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar',
    # Documents
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    # Other binaries
    '.exe', '.dll', '.so', '.o', '.a', '.class', '.jar',
    # Logs/Temp
    '.log', '.tmp', '.swp'
}

# Used in is_important
ROOT_IMPORTANT_FILES_LIST = [
    # Version Control
    ".gitignore",
    ".gitattributes",
    # Documentation
    "README",
    "README.md",
    "README.txt",
    "README.rst",
    "CONTRIBUTING",
    "CONTRIBUTING.md",
    "CONTRIBUTING.txt",
    "CONTRIBUTING.rst",
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "CHANGELOG",
    "CHANGELOG.md",
    "CHANGELOG.txt",
    "CHANGELOG.rst",
    "SECURITY",
    "SECURITY.md",
    "SECURITY.txt",
    "CODEOWNERS",
    # Package Management and Dependencies
    "requirements.txt",
    "Pipfile",
    "Pipfile.lock",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "npm-shrinkwrap.json",
    "Gemfile",
    "Gemfile.lock",
    "composer.json",
    "composer.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "build.sbt",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "mix.exs",
    "rebar.config",
    "project.clj",
    "Podfile",
    "Cartfile",
    "dub.json",
    "dub.sdl",
    # Configuration and Settings
    ".env",
    ".env.example",
    ".editorconfig",
    "tsconfig.json",
    "jsconfig.json",
    ".babelrc",
    "babel.config.js",
    ".eslintrc",
    ".eslintignore",
    ".prettierrc",
    ".stylelintrc",
    "tslint.json",
    ".pylintrc",
    ".flake8",
    ".rubocop.yml",
    ".scalafmt.conf",
    ".dockerignore",
    ".gitpod.yml",
    "sonar-project.properties",
    "renovate.json",
    "dependabot.yml",
    ".pre-commit-config.yaml",
    "mypy.ini",
    "tox.ini",
    ".yamllint",
    "pyrightconfig.json",
    # Build and Compilation
    "webpack.config.js",
    "rollup.config.js",
    "parcel.config.js",
    "gulpfile.js",
    "Gruntfile.js",
    "build.xml",
    "build.boot",
    "project.json",
    "build.cake",
    "MANIFEST.in",
    # Testing
    "pytest.ini",
    "phpunit.xml",
    "karma.conf.js",
    "jest.config.js",
    "cypress.json",
    ".nycrc",
    ".nycrc.json",
    # CI/CD
    ".travis.yml",
    ".gitlab-ci.yml",
    "Jenkinsfile",
    "azure-pipelines.yml",
    "bitbucket-pipelines.yml",
    "appveyor.yml",
    "circle.yml",
    ".circleci/config.yml",
    ".github/dependabot.yml",
    "codecov.yml",
    ".coveragerc",
    # Docker and Containers
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.override.yml",
    # Cloud and Serverless
    "serverless.yml",
    "firebase.json",
    "now.json",
    "netlify.toml",
    "vercel.json",
    "app.yaml",
    "terraform.tf",
    "main.tf",
    "cloudformation.yaml",
    "cloudformation.json",
    "ansible.cfg",
    "kubernetes.yaml",
    "k8s.yaml",
    # Database
    "schema.sql",
    "liquibase.properties",
    "flyway.conf",
    # Framework-specific
    "next.config.js",
    "nuxt.config.js",
    "vue.config.js",
    "angular.json",
    "gatsby-config.js",
    "gridsome.config.js",
    # API Documentation
    "swagger.yaml",
    "swagger.json",
    "openapi.yaml",
    "openapi.json",
    # Development environment
    ".nvmrc",
    ".ruby-version",
    ".python-version",
    "Vagrantfile",
    # Quality and metrics
    ".codeclimate.yml",
    "codecov.yml",
    # Documentation
    "mkdocs.yml",
    "_config.yml",
    "book.toml",
    "readthedocs.yml",
    ".readthedocs.yaml",
    # Package registries
    ".npmrc",
    ".yarnrc",
    # Linting and formatting
    ".isort.cfg",
    ".markdownlint.json",
    ".markdownlint.yaml",
    # Security
    ".bandit",
    ".secrets.baseline",
    # Misc
    ".pypirc",
    ".gitkeep",
    ".npmignore",
]

NORMALIZED_ROOT_IMPORTANT_FILES = set(os.path.normpath(path) for path in ROOT_IMPORTANT_FILES_LIST)

# Import tree-sitter related functions directly
try:
    from grep_ast.tsl import get_language, get_parser
except ImportError as e:
    raise ImportError("grep-ast with tree-sitter bindings is required. Try: pip install grep-ast") from e

# --- Constants and Definitions ---

Tag = namedtuple("Tag", "rel_fname fname line name kind".split())
TAGS_CACHE_DIR = ".emigo_repomap"

# --- Utility Functions ---

def read_text(filename, encoding="utf-8", errors="ignore"):
    """Reads a file and returns its content, returning None on error."""
    try:
        with open(str(filename), "r", encoding=encoding, errors=errors) as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError, OSError, UnicodeError):
        # Log specific errors if verbose is enabled elsewhere, otherwise fail silently
        return None


def get_rel_fname(fname, root):
    """Gets the relative path of fname from the root."""
    try:
        return os.path.relpath(fname, root)
    except ValueError:
        # Handle cases where fname and root are on different drives (Windows)
        return fname


# --- Important Files Logic ---

def is_important(file_path):
    """Checks if a file path is considered important based on defined constants."""
    file_name = os.path.basename(file_path)
    dir_name = os.path.normpath(os.path.dirname(file_path))
    normalized_path = os.path.normpath(file_path)

    # Check for GitHub Actions workflow files
    if dir_name == os.path.normpath(".github/workflows") and file_name.endswith((".yml", ".yaml")):
        return True

    return normalized_path in NORMALIZED_ROOT_IMPORTANT_FILES

def filter_important_files(file_paths):
    """Filter a list of file paths to return only those considered important."""
    return [fp for fp in file_paths if is_important(fp)]


# --- RepoMap Class ---

class RepoMap:
    warned_files = set() # Track files we warned about (e.g., not found)

    def __init__(
        self,
        root,
        map_tokens=4096,
        verbose=False,
        tokenizer_name="cl100k_base",
        force_refresh=False,
    ):
        self.verbose = verbose
        self.root = os.path.abspath(root)
        self.max_map_tokens = map_tokens
        self.force_refresh = force_refresh

        try:
            self.tokenizer = tiktoken.get_encoding(tokenizer_name)
        except Exception as e:
            raise ValueError(f"Error initializing tokenizer '{tokenizer_name}': {e}. Ensure tiktoken is installed.") from e

        self.load_tags_cache()
        self.tree_cache = {}
        self.tree_context_cache = {}
        self.map_processing_time = 0

    def token_count(self, text):
        """Counts tokens using the tiktoken tokenizer."""
        if not isinstance(text, str):
            text = str(text)
        return len(self.tokenizer.encode(text))

    def get_repo_map(self, chat_files, other_files, mentioned_fnames=None, mentioned_idents=None):
        """Generates the repository map string."""
        if self.max_map_tokens <= 0:
            return "" # Skip if no tokens allowed
        if not other_files and not chat_files:
            return "" # Skip if no files to map

        start_time = time.time()
        try:
            files_listing = self.get_ranked_tags_map_uncached(
                chat_files, other_files, self.max_map_tokens, mentioned_fnames, mentioned_idents
            )
        except Exception as e:
            # Log the error if verbose, but return empty string otherwise
            if self.verbose:
                print(f"ERROR: Failed map generation: {e}", file=sys.stderr)
            return ""
        end_time = time.time()
        self.map_processing_time = end_time - start_time

        if not files_listing:
            return ""

        if self.verbose:
            num_tokens = self.token_count(files_listing)
            print(f"Repo Map: {num_tokens} tokens, {self.map_processing_time:.2f}s", file=sys.stderr)

        return f"Repository Map:\n{files_listing}"

    def load_tags_cache(self):
        """Loads the tags cache from disk or initializes it."""
        path = Path(self.root) / TAGS_CACHE_DIR
        try:
            self.TAGS_CACHE = Cache(path)
            # Basic check to see if cache is usable
            _ = len(self.TAGS_CACHE)
        except Exception as e:
            # Fallback to in-memory dict if disk cache fails
            if self.verbose:
                print(f"Warning: Disk cache error at {path}: {e}. Using in-memory cache.", file=sys.stderr)
            self.TAGS_CACHE = dict()

    def get_mtime(self, fname):
        """Gets the modification time of a file, returns None on error."""
        try:
            return os.path.getmtime(fname)
        except OSError: # Catches FileNotFoundError and other OS issues
            return None

    def get_tags(self, fname, rel_fname):
        """Gets tags for a file, using the cache if possible."""
        file_mtime = self.get_mtime(fname)
        if file_mtime is None:
            return [] # File not accessible

        cache_key = fname
        cached_val = None
        try:
            cached_val = self.TAGS_CACHE.get(cache_key)
        except Exception as e:
            # Log if verbose, but treat as cache miss
            if self.verbose:
                print(f"Warning: Cache read error for {fname}: {e}", file=sys.stderr)

        # Check cache validity
        if (not self.force_refresh and
            isinstance(cached_val, dict) and
            cached_val.get("mtime") == file_mtime):
            return cached_val.get("data", []) # Return cached data

        # Cache miss or invalid: Generate tags
        if self.verbose and not cached_val:
             print(f"Cache miss for {rel_fname}, generating tags...", file=sys.stderr)
        elif self.verbose:
             print(f"Cache outdated for {rel_fname}, regenerating tags...", file=sys.stderr)

        data = list(self.get_tags_raw(fname, rel_fname))

        # Update cache
        try:
            cache_entry = {"mtime": file_mtime, "data": data}
            self.TAGS_CACHE[cache_key] = cache_entry
        except Exception as e:
            # Log if verbose, but continue
            if self.verbose:
                print(f"Warning: Cache write error for {fname}: {e}", file=sys.stderr)

        return data

    def get_tags_raw(self, fname, rel_fname):
        """Generates tags using tree-sitter and pygments fallback."""
        lang = filename_to_lang(fname)
        if not lang:
            return

        language, parser = None, None
        try:
            language = get_language(lang)
            parser = get_parser(lang)
        except Exception:
            # Silently skip if parser not found for this language
            return

        query_scm_path = get_scm_fname(lang)
        query_scm = None
        if query_scm_path:
            try:
                query_scm = query_scm_path.read_text(encoding='utf-8')
            except OSError:
                query_scm = None # Ignore if SCM file is unreadable

        code = read_text(fname)
        if not code:
            return
        tree = parser.parse(bytes(code, "utf-8"))

        saw_defs = False
        saw_refs = False

        # Tree-sitter tags
        if query_scm:
            try:
                query = language.query(query_scm)
                captures = query.captures(tree.root_node)
                all_nodes = [(node, tag_name) for tag_name, nodes in captures.items() for node in nodes]

                for node, tag_name in all_nodes:
                    kind = None
                    if tag_name.startswith("name.definition."):
                        kind = "def"
                        saw_defs = True
                    elif tag_name.startswith("name.reference."):
                        kind = "ref"
                        saw_refs = True

                    if kind:
                        try:
                            name_text = node.text.decode("utf-8")
                            yield Tag(rel_fname, fname, node.start_point[0], name_text, kind)
                        except (AttributeError, UnicodeDecodeError):
                            continue # Skip invalid nodes
            except Exception as e:
                 if self.verbose:
                     print(f"Warning: Tree-sitter query failed for {fname}: {e}", file=sys.stderr)

        # Pygments fallback for references if needed
        if not saw_refs and query_scm or not query_scm: # Use pygments if no refs found or no SCM
            try:
                lexer = guess_lexer_for_filename(fname, code)
                tokens = lexer.get_tokens(code)
                for token_type, token_text in tokens:
                    if token_type in Token.Name:
                        yield Tag(rel_fname, fname, -1, token_text, "ref") # Line -1 for pygments
            except Exception as e:
                 if self.verbose:
                     print(f"Warning: Pygments failed for {fname}: {e}", file=sys.stderr)

    def get_ranked_tags(self, chat_fnames, other_fnames, mentioned_fnames, mentioned_idents):
        """Ranks tags using PageRank."""
        defines = defaultdict(set)      # {ident: {rel_fname, ...}}
        references = defaultdict(list)  # {ident: [rel_fname, ...]}
        definitions = defaultdict(set)  # {(rel_fname, ident): {Tag, ...}}
        personalization = {}

        all_fnames = set(chat_fnames) | set(other_fnames)
        chat_rel_fnames = {get_rel_fname(f, self.root) for f in chat_fnames}
        mentioned_rel_fnames = {get_rel_fname(f, self.root) for f in mentioned_fnames}

        # Calculate base personalization value
        personalize_base = 100 / len(all_fnames) if all_fnames else 1

        # --- Scan files and build initial data structures ---
        for fname in sorted(list(all_fnames)):
            if not Path(fname).is_file():
                if fname not in self.warned_files:
                    # Only warn once per file if verbose
                    if self.verbose: print(f"Warning: Skipping non-file {fname}", file=sys.stderr)
                    self.warned_files.add(fname)
                continue

            rel_fname = get_rel_fname(fname, self.root)
            if rel_fname in chat_rel_fnames or rel_fname in mentioned_rel_fnames:
                personalization[rel_fname] = personalize_base

            tags = self.get_tags(fname, rel_fname) # Use cached tags
            for tag in tags:
                if tag.kind == "def":
                    defines[tag.name].add(rel_fname)
                    definitions[(rel_fname, tag.name)].add(tag)
                elif tag.kind == "ref":
                    references[tag.name].append(rel_fname)

        # Use definitions as references if no explicit references found
        if not references and defines:
            references = {k: list(v) for k, v in defines.items()}

        # --- Build Dependency Graph ---
        G = nx.MultiDiGraph()
        idents = set(defines.keys()) & set(references.keys())

        for ident in idents:
            definers = defines[ident]
            mul = 10 if ident in mentioned_idents else (0.1 if ident.startswith("_") else 1)

            for referencer, num_refs in Counter(references[ident]).items():
                weight = math.sqrt(num_refs) * mul
                for definer in definers:
                    G.add_edge(referencer, definer, weight=weight, ident=ident)

        # Add nodes for files without edges to ensure they are in the graph for PageRank
        all_rel_fnames = {get_rel_fname(f, self.root) for f in all_fnames if Path(f).is_file()}
        for rel_fname in all_rel_fnames:
            if not G.has_node(rel_fname):
                G.add_node(rel_fname)

        # --- Run PageRank ---
        ranked = {}
        if G.number_of_nodes() > 0:
            try:
                pers_args = dict(personalization=personalization, dangling=personalization) if personalization else {}
                ranked = nx.pagerank(G, weight="weight", **pers_args)
            except Exception as e:
                # Fallback: Rank nodes equally, respecting personalization
                if self.verbose: print(f"Warning: PageRank failed ({e}), using fallback ranking.", file=sys.stderr)
                num_nodes = G.number_of_nodes()
                base_rank = 1.0 / num_nodes if num_nodes > 0 else 0
                ranked = {node: personalization.get(node, base_rank) for node in G.nodes()}
                # Normalize if personalization was used
                total_rank = sum(ranked.values())
                if personalization and total_rank > 0:
                    ranked = {node: r / total_rank for node, r in ranked.items()}

        # --- Distribute Rank to Definitions ---
        ranked_definitions = defaultdict(float)
        if G.edges():
            for src in G.nodes():
                src_rank = ranked.get(src, 0)
                total_weight = sum(d.get("weight", 0) for _, _, d in G.out_edges(src, data=True))
                if total_weight > 0:
                    for _, dst, data in G.out_edges(src, data=True):
                        ident, weight = data.get("ident"), data.get("weight", 0)
                        if ident:
                            ranked_definitions[(dst, ident)] += src_rank * weight / total_weight

        # --- Collect and Sort Ranked Tags/Files ---
        ranked_tags_list = []
        fnames_added = set()

        # Add definitions sorted by rank (excluding chat files)
        sorted_defs = sorted(ranked_definitions.items(), key=lambda x: x[1], reverse=True)
        for (rel_fname, ident), _rank in sorted_defs:
            if rel_fname not in chat_rel_fnames:
                def_tags = definitions.get((rel_fname, ident), set())
                ranked_tags_list.extend(list(def_tags))
                fnames_added.add(rel_fname)

        # Add remaining files sorted by rank (excluding chat files and those already added)
        rel_other_fnames = {get_rel_fname(f, self.root) for f in other_fnames if Path(f).is_file()}
        sorted_files = sorted(ranked.items(), key=lambda item: item[1], reverse=True)
        for rel_fname, _rank in sorted_files:
            if rel_fname in rel_other_fnames and rel_fname not in fnames_added:
                ranked_tags_list.append((rel_fname,)) # Represent file-only as tuple
                fnames_added.add(rel_fname)

        # Add any remaining other_fnames not ranked (e.g., disconnected)
        remaining_others = sorted(list(rel_other_fnames - fnames_added))
        for rel_fname in remaining_others:
             ranked_tags_list.append((rel_fname,))

        return ranked_tags_list

    def get_ranked_tags_map_uncached(
        self, chat_fnames, other_fnames, max_map_tokens, mentioned_fnames=None, mentioned_idents=None
    ):
        """Generates the map string from ranked tags, fitting it into the token limit."""
        mentioned_fnames = set(mentioned_fnames or [])
        mentioned_idents = set(mentioned_idents or [])

        ranked_items = self.get_ranked_tags(
            chat_fnames, other_fnames, mentioned_fnames, mentioned_idents
        )

        # Prioritize important files
        other_rel_fnames = sorted({get_rel_fname(f, self.root) for f in other_fnames if Path(f).is_file()})
        special_fnames = filter_important_files(other_rel_fnames)

        # Get filenames already represented by ranked items
        ranked_fnames = set()
        for item in ranked_items:
            if isinstance(item, Tag):
                ranked_fnames.add(item.rel_fname)
            elif isinstance(item, tuple):
                ranked_fnames.add(item[0])

        # Combine: special files first (if not already ranked), then ranked items
        special_fnames_to_add = [(fn,) for fn in special_fnames if fn not in ranked_fnames]
        combined_items = special_fnames_to_add + ranked_items

        if not combined_items:
            return ""

        # --- Binary search for optimal number of items ---
        num_items = len(combined_items)
        low, high = 0, num_items
        best_tree, best_tokens = "", 0
        self.tree_cache.clear() # Clear render cache for this run

        # Estimate initial mid-point (heuristic)
        mid = min(int(max_map_tokens / 20), num_items) if num_items > 0 else 0 # Adjusted heuristic

        iterations = 0
        max_iterations = int(math.log2(num_items)) + 5 if num_items > 0 else 0

        chat_rel_fnames = {get_rel_fname(f, self.root) for f in chat_fnames}

        while low <= high and iterations < max_iterations:
            iterations += 1
            current_items = combined_items[:mid]
            if not current_items:
                if num_items > 0 and mid == 0: # Ensure low advances if mid starts at 0
                    low = mid + 1
                    mid = (low + high) // 2
                    continue
                else:
                    break # No items left to try

            tree = self.to_tree(current_items, chat_rel_fnames)
            num_tokens = self.token_count(tree)

            if num_tokens <= max_map_tokens:
                if num_tokens >= best_tokens: # Prefer larger maps if tokens are equal
                    best_tree, best_tokens = tree, num_tokens
                low = mid + 1 # Try more items
            else:
                high = mid - 1 # Try fewer items

            mid = (low + high) // 2

            # Optimization: Stop early if close to limit
            if best_tokens > max_map_tokens * 0.98:
                 break

        if self.verbose:
            print(f"Selected map size: {best_tokens} tokens", file=sys.stderr)
        return best_tree

    def render_tree(self, abs_fname, rel_fname, lois):
        """Renders code snippets using TreeContext, with caching."""
        mtime = self.get_mtime(abs_fname)
        if mtime is None: return f"# Error: Cannot access {rel_fname}\n"

        lois_tuple = tuple(sorted(list(set(lois))))
        render_key = (rel_fname, lois_tuple, mtime)
        if render_key in self.tree_cache:
            return self.tree_cache[render_key]

        # Check/Update TreeContext cache
        context_cache_entry = self.tree_context_cache.get(rel_fname)
        context = None
        if context_cache_entry and context_cache_entry.get("mtime") == mtime:
            context = context_cache_entry["context"]
        else:
            code = read_text(abs_fname)
            if code is None: return f"# Error: Cannot read {rel_fname}\n"
            if not code.endswith("\n"): code += "\n"
            try:
                context = TreeContext(
                    rel_fname, code, color=False, line_number=False, child_context=False,
                    last_line=False, margin=0, mark_lois=False, loi_pad=0,
                    show_top_of_file_parent_scope=False,
                )
                self.tree_context_cache[rel_fname] = {"context": context, "mtime": mtime}
            except Exception as e:
                 return f"# Error processing {rel_fname}: {e}\n"

        # Render using the cached or new context
        rendered_output = f"# Error formatting {rel_fname}\n" # Default error
        if context:
            try:
                context.lines_of_interest = set(lois)
                context.add_context()
                rendered_output = context.format()
            except Exception as e:
                 if self.verbose:
                     print(f"Warning: Error formatting {rel_fname} lines {lois}: {e}", file=sys.stderr)

        self.tree_cache[render_key] = rendered_output
        return rendered_output

    def to_tree(self, tags_or_files, chat_rel_fnames):
        """Formats ranked tags/files into the final map string."""
        if not tags_or_files: return ""

        grouped_tags = defaultdict(list) # {rel_fname: [Tag, ...]}
        files_only = set()              # {rel_fname, ...}

        # Group items, skipping those in chat
        for item in tags_or_files:
            rel_fname = None
            if isinstance(item, Tag):
                rel_fname = item.rel_fname
                if rel_fname not in chat_rel_fnames:
                    grouped_tags[rel_fname].append(item)
            elif isinstance(item, tuple) and len(item) == 1:
                rel_fname = item[0]
                if rel_fname not in chat_rel_fnames:
                    files_only.add(rel_fname)

        output_parts = []

        # Process files with tags
        for rel_fname in sorted(grouped_tags.keys()):
            file_tags = grouped_tags[rel_fname]
            abs_fname = file_tags[0].fname # Assumes all tags for a file have same abs path
            lois = [tag.line for tag in file_tags if tag.line >= 0]

            output_parts.append("\n" + rel_fname + ":\n")
            if lois:
                rendered_tree = self.render_tree(abs_fname, rel_fname, lois)
                output_parts.append(rendered_tree)
            # else: No specific lines, just list filename (implicitly done by adding header)

        # Add files that were ranked but had no specific tags selected
        # Filter out files already processed via grouped_tags
        remaining_files_only = sorted(list(files_only - set(grouped_tags.keys())))
        for rel_fname in remaining_files_only:
             output_parts.append("\n" + rel_fname + "\n")

        # Join, truncate long lines, and add trailing newline
        full_output = "".join(output_parts)
        truncated_output = "\n".join([line[:200] for line in full_output.splitlines()])
        return truncated_output + "\n" if truncated_output else ""


# --- Helper Functions ---

def get_scm_fname(lang):
    """Finds the tree-sitter query file for a language."""
    script_dir = Path(__file__).parent.resolve()
    query_path = script_dir / "queries" / "tree-sitter-languages" / f"{lang}-tags.scm"
    return query_path if query_path.is_file() else None


# --- RepoMapper Wrapper Class ---

class RepoMapper:
    def __init__(self, root_dir, map_tokens=4096, tokenizer="cl100k_base", verbose=False, force_refresh=False):
        self.root = os.path.abspath(root_dir)
        self.verbose = verbose
        # Instantiate the core RepoMap logic handler
        self.repo_mapper = RepoMap(
            root=self.root,
            map_tokens=map_tokens,
            verbose=verbose,
            tokenizer_name=tokenizer,
            force_refresh=force_refresh,
        )
        self.map_generation_time = time.time() # Track last generation time

    def _is_gitignored(self, path):
        """Checks if a path is gitignored. Requires gitignore_parser."""
        try:
            # Lazy import to avoid hard dependency if not used/installed
            from gitignore_parser import parse_gitignore
            gitignore_path = Path(self.root) / '.gitignore'
            if gitignore_path.is_file():
                with gitignore_path.open() as f:
                    matches = parse_gitignore(f)
                    return matches(path)
        except ImportError:
            # Silently ignore if gitignore_parser is not installed
            pass
        except Exception as e:
            if self.verbose:
                print(f"Warning: Error checking .gitignore for {path}: {e}", file=sys.stderr)
        return False

    def _find_src_files(self, directory):
        """Finds source files recursively, respecting ignores."""
        import re # Import locally as it's only used here now

        src_files = []
        compiled_ignored_dirs = [re.compile(pattern) for pattern in IGNORED_DIRS]

        if not Path(directory).is_dir():
            # Handle case where input is a single file
            if Path(directory).is_file() and Path(directory).suffix.lower() not in BINARY_EXTS:
                return [str(directory)]
            return []

        for root, dirs, files in os.walk(directory, topdown=True):
            root_path = Path(root)
            # Filter ignored directories using compiled regex
            dirs[:] = [d for d in dirs if not (
                d.startswith('.') or
                any(pattern.search(d) for pattern in compiled_ignored_dirs)
            )]

            for file in files:
                file_path = root_path / file
                # Check ignores: hidden, binary extension, gitignored
                if (file.startswith('.') or
                    file_path.suffix.lower() in BINARY_EXTS or
                    self._is_gitignored(str(file_path))):
                    continue
                src_files.append(str(file_path))

        return src_files

    def generate_map(self, chat_files=None, mentioned_files=None, mentioned_idents=None, force_refresh=None):
        """Generates the repository map."""
        chat_files = chat_files or []
        mentioned_files = mentioned_files or []
        mentioned_idents = set(mentioned_idents or [])
        if force_refresh is not None:
            self.repo_mapper.force_refresh = force_refresh # Update underlying mapper

        self.map_generation_time = time.time()

        # Resolve paths relative to root, ensuring they exist
        def resolve_path(p):
            abs_p = Path(self.root) / p
            return str(abs_p.resolve()) if abs_p.exists() else None

        chat_files_abs = [p for p in (resolve_path(f) for f in chat_files) if p]
        mentioned_files_abs = [p for p in (resolve_path(f) for f in mentioned_files) if p]

        # Find all potential source files in the repository
        all_repo_files = self._find_src_files(self.root)
        if not all_repo_files:
            return "" # No files found

        # Separate files into chat context vs. others
        chat_files_set = set(chat_files_abs)
        other_files_abs = [f for f in all_repo_files if f not in chat_files_set]

        # Delegate map generation to the core RepoMap instance
        return self.repo_mapper.get_repo_map(
            chat_files=chat_files_abs,
            other_files=other_files_abs,
            mentioned_fnames=mentioned_files_abs,
            mentioned_idents=mentioned_idents,
        )

    def render_cache(self):
        """Renders all cached tags without ranking (for debugging)."""
        cache_path = Path(self.root) / TAGS_CACHE_DIR
        if not cache_path.is_dir():
            return ""

        all_tags = []
        all_cached_fnames = set()
        try:
            cache = Cache(str(cache_path)) # Ensure path is string for Cache
            for key in cache.iterkeys():
                try:
                    abs_fname = key # Assuming key is the absolute filename
                    if not Path(abs_fname).is_file(): continue # Skip non-files

                    all_cached_fnames.add(abs_fname)
                    cached_item = cache.get(key)
                    if isinstance(cached_item, dict) and "data" in cached_item:
                        all_tags.extend(cached_item.get("data", []))
                except Exception as e:
                    if self.verbose: print(f"Warning: Error processing cache key {key}: {e}", file=sys.stderr)

            cache.close()

            # Use a temporary RepoMap instance for rendering logic
            temp_mapper = RepoMap(
                root=self.root,
                map_tokens=1_000_000, # Effectively unlimited for cache dump
                verbose=self.verbose,
                tokenizer_name=self.repo_mapper.tokenizer.name, # Use same tokenizer
            )

            # Prepare items: all tags + filenames not represented by tags
            tag_fnames = {tag.rel_fname for tag in all_tags}
            all_cached_rel_fnames = {get_rel_fname(f, self.root) for f in all_cached_fnames}
            files_only = all_cached_rel_fnames - tag_fnames
            items_to_render = list(all_tags) + [(fname,) for fname in sorted(files_only)]

            return temp_mapper.to_tree(items_to_render, chat_rel_fnames=set())

        except Exception as e:
            if self.verbose: print(f"Error rendering cache: {e}", file=sys.stderr)
            return ""


def main():
    """Command line interface for standalone repomapper execution."""
    parser = argparse.ArgumentParser(description="Generate a repository map.")
    parser.add_argument("--dir", required=True, help="Root directory of the repository.")
    parser.add_argument("--map-tokens", type=int, default=4096, help="Target token limit for the map.")
    parser.add_argument("--tokenizer", default="cl100k_base", help="Tiktoken tokenizer name.")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore cache and regenerate all tags.")
    parser.add_argument("--verbose", action="store_true", help="Enable detailed output.")
    parser.add_argument("--output", help="File path to write the map to (stdout if not specified).")
    parser.add_argument("--render-cache", action="store_true", help="Render all cached tags instead of generating a ranked map.")
    parser.add_argument("--chat-files", nargs='*', default=[], help="Files currently in chat context.")
    parser.add_argument("--mentioned-files", nargs='*', default=[], help="Files mentioned for context.")
    parser.add_argument("--mentioned-idents", nargs='*', default=[], help="Identifiers mentioned for context.")

    args = parser.parse_args()

    try:
        mapper = RepoMapper(
            root_dir=args.dir,
            map_tokens=args.map_tokens,
            tokenizer=args.tokenizer,
            verbose=args.verbose,
            force_refresh=args.force_refresh
        )

        if args.render_cache:
            content = mapper.render_cache()
            if content:
                print("\n--- Rendered Cache Map ---", file=sys.stderr)
                print(content) # Print cache content to stdout
                print("--- End Rendered Cache Map ---", file=sys.stderr)
            else:
                print("Cache is empty or could not be rendered.", file=sys.stderr)
            return

        content = mapper.generate_map(
            chat_files=args.chat_files,
            mentioned_files=args.mentioned_files,
            mentioned_idents=args.mentioned_idents
        )

        if content:
            if args.output:
                try:
                    with open(args.output, "w", encoding="utf-8") as f:
                        f.write(content)
                    if args.verbose: print(f"Repository map written to: {args.output}", file=sys.stderr)
                except IOError as e:
                    print(f"Error writing map to {args.output}: {e}", file=sys.stderr)
                    # Fallback to printing to stdout
                    print("\n--- Repository Map ---")
                    print(content)
                    print("--- End Repository Map ---")
            else:
                # Print final map to stdout
                print(content)
        else:
            print("Failed to generate repository map.", file=sys.stderr)

    except (ImportError, ValueError, FileNotFoundError) as e:
         print(f"Error: {e}", file=sys.stderr)
         # Exit with non-zero status on critical errors
         exit(1)
    except Exception as e:
         print(f"An unexpected error occurred: {e}", file=sys.stderr)
         if args.verbose:
             import traceback
             traceback.print_exc(file=sys.stderr)
         exit(1)


if __name__ == "__main__":
    main()
