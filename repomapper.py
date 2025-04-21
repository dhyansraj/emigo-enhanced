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
import sys
import time
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import List

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

# --- Constants ---

TAGS_CACHE_DIR = ".emigo_repomap"
# Simple tuple for tags instead of namedtuple
Tag = tuple # (rel_fname, fname, line, name, kind)

# --- Utility Functions ---

def read_text(filename, encoding="utf-8", errors="ignore"):
    """Reads a file and returns its content, returning None on error."""
    try:
        with open(str(filename), "r", encoding=encoding, errors=errors) as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError, OSError, UnicodeError):
        return None # Fail silently on read errors


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


# --- Core Logic ---

class RepoMap:
    warned_files = set()

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
        self.tokenizer = tiktoken.get_encoding(tokenizer_name) # Let exceptions propagate

        self.TAGS_CACHE = self.load_tags_cache()
        self.tree_cache = {}
        self.tree_context_cache = {}
        self.map_processing_time = 0
        # Store scanned tag info globally within the instance
        self._all_tags = []
        self._defines = defaultdict(set)
        self._references = defaultdict(list)
        self._scanned_fnames = set()

    def token_count(self, text):
        """Counts tokens using the tiktoken tokenizer."""
        if not isinstance(text, str):
            text = str(text)
        return len(self.tokenizer.encode(text))

    def get_repo_map(self, chat_files, other_files):
        """Generates the repository map string."""
        if self.max_map_tokens <= 0 or not (other_files or chat_files):
            return ""

        start_time = time.time()
        # Ensure tags are scanned before ranking
        self._scan_all_tags(set(chat_files) | set(other_files))

        files_listing = self.get_ranked_tags_map_uncached(
            chat_files, other_files, self.max_map_tokens
        )
        self.map_processing_time = time.time() - start_time

        if not files_listing:
            return ""

        print(f"Repo Map processed in {self.map_processing_time:.2f}s", file=sys.stderr)

        return files_listing

    def load_tags_cache(self):
        """Loads the tags cache from disk or initializes it."""
        path = Path(self.root) / TAGS_CACHE_DIR
        try:
            # Attempt to initialize disk cache
            cache = Cache(path)
            _ = len(cache) # Basic check
            return cache
        except Exception:
            # Fallback to in-memory dict if disk cache fails
            return dict() # Return a simple dict as the cache interface

    def get_mtime(self, fname):
        """Gets the modification time of a file."""
        return os.path.getmtime(fname)

    def get_tags(self, fname, rel_fname):
        """Gets tags for a file, using the cache if possible."""
        file_mtime = self.get_mtime(fname)
        if file_mtime is None:
            return []

        cache_key = fname
        cached_val = None # Initialize
        if hasattr(self.TAGS_CACHE, 'get'):
            try:
                # Attempt to retrieve from cache
                cached_val = self.TAGS_CACHE.get(cache_key)
            except TypeError as e:
                # Handle potential unpickling errors (e.g., type definition changed)
                if self.verbose:
                    print(f"Warning: Cache read error for {fname} (likely format change): {e}. Regenerating.", file=sys.stderr)
                # Treat as cache miss by leaving cached_val as None
                # Explicitly delete the bad cache entry to prevent repeated warnings
                if hasattr(self.TAGS_CACHE, 'delete'):
                    try:
                        self.TAGS_CACHE.delete(cache_key)
                        if self.verbose:
                            print(f"Info: Deleted invalid cache entry for {fname}.", file=sys.stderr)
                    except Exception as delete_e:
                        if self.verbose:
                            print(f"Warning: Failed to delete invalid cache entry for {fname}: {delete_e}", file=sys.stderr)
            except Exception as e:
                # Handle other potential cache read errors
                if self.verbose:
                    print(f"Warning: Unexpected cache read error for {fname}: {e}. Regenerating.", file=sys.stderr)
                # Treat as cache miss

        # Check cache validity (only if cached_val was successfully retrieved)
        if not self.force_refresh and isinstance(cached_val, dict) and cached_val.get("mtime") == file_mtime:
            # Ensure 'data' exists and is a list before returning
            data = cached_val.get("data")
            if isinstance(data, list):
                return data
            elif self.verbose:
                 print(f"Warning: Invalid cache data format for {fname}. Regenerating.", file=sys.stderr)
            # Fall through to regenerate if data is not a list

        # Cache miss or invalid: Generate tags
        data = list(self.get_tags_raw(fname, rel_fname))

        # Update cache if it's a real cache object
        if hasattr(self.TAGS_CACHE, '__setitem__'):
            try:
                cache_entry = {"mtime": file_mtime, "data": data}
                self.TAGS_CACHE[cache_key] = cache_entry
            except Exception as e:
                if self.verbose:
                    print(f"Warning: Cache write error for {fname}: {e}", file=sys.stderr)
                # Ignore cache write errors silently otherwise

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
            return # Silently skip if parser not found

        code = read_text(fname)
        if not code:
            return

        # Tree-sitter tags
        query_scm_path = get_scm_fname(lang)
        tree_sitter_refs_found = False
        if query_scm_path:
            try:
                query_scm = query_scm_path.read_text(encoding='utf-8')
                tree = parser.parse(bytes(code, "utf-8"))
                query = language.query(query_scm)
                captures = query.captures(tree.root_node)

                for node, tag_name in [(node, tag_name) for tag_name, nodes in captures.items() for node in nodes]:
                    kind = None
                    if tag_name.startswith("name.definition."):
                        kind = "def"
                    elif tag_name.startswith("name.reference."):
                        kind = "ref"
                        tree_sitter_refs_found = True

                    if kind:
                        try:
                            name_text = node.text.decode("utf-8")
                            # Use tuple directly: (rel_fname, fname, line, name, kind)
                            yield (rel_fname, fname, node.start_point[0], name_text, kind)
                        except (AttributeError, UnicodeDecodeError):
                            continue # Skip invalid nodes
            except Exception:
                 pass # Silently ignore tree-sitter errors

        # Pygments fallback for references if tree-sitter didn't find any or wasn't used
        if not tree_sitter_refs_found:
            try:
                lexer = guess_lexer_for_filename(fname, code)
                tokens = lexer.get_tokens(code)
                for token_type, token_text in tokens:
                    if token_type in Token.Name:
                        # Use tuple directly: (rel_fname, fname, line, name, kind)
                        yield (rel_fname, fname, -1, token_text, "ref") # Line -1 for pygments
            except Exception:
                 pass # Silently ignore pygments errors

    def _scan_all_tags(self, fnames_to_scan):
        if self.force_refresh:
            self._all_tags = []
            self._defines = defaultdict(set)
            self._references = defaultdict(list)
            self._definitions = defaultdict(set)
            self._scanned_fnames = set()
            files_to_process = fnames_to_scan # Rescan everything requested
        else:
            files_to_process = fnames_to_scan - self._scanned_fnames
            if not files_to_process:
                return # Nothing new to scan

        new_tags = []
        processed_fnames = set()
        for fname in sorted(list(files_to_process)):
            abs_fname = os.path.abspath(os.path.join(self.root, fname))
            if not Path(abs_fname).is_file():
                if abs_fname not in self.warned_files:
                    # Keep minimal warning for non-existent files
                    print(f"Warning: Skipping non-file {abs_fname}", file=sys.stderr)
                    self.warned_files.add(abs_fname)
                continue

            rel_fname = get_rel_fname(abs_fname, self.root)
            tags = self.get_tags(abs_fname, rel_fname)
            new_tags.extend(tags)
            processed_fnames.add(fname) # Track successfully processed files

        self._scanned_fnames.update(processed_fnames)

        # Update global tag structures only if new tags were found or refreshing
        if new_tags or self.force_refresh:
            self._all_tags.extend(new_tags) # Append new tags
            # Rebuild defines/references from the potentially updated _all_tags
            self._defines = defaultdict(set)
            self._references = defaultdict(list)
            self._definitions = defaultdict(set)
            for tag_tuple in self._all_tags:
                # Unpack the tuple: (rel_fname, fname, line, name, kind)
                rel_fname, _, _, name, kind = tag_tuple
                if kind == "def":
                    self._defines[name].add(rel_fname)
                    self._definitions[(rel_fname, name)].add(tag_tuple)
                elif kind == "ref":
                    self._references[name].append(rel_fname)

            # Fallback: Use definitions as references if no explicit references found
            if not self._references and self._defines:
                self._references = {k: list(v) for k, v in self._defines.items()}


    def get_ranked_tags(self, chat_fnames, other_fnames):
        # chat_fnames and other_fnames are expected to be absolute paths here
        all_abs_fnames = set(chat_fnames) | set(other_fnames)
        # Filter chat files to ensure they exist before getting rel_fname
        chat_rel_fnames = {get_rel_fname(f, self.root) for f in chat_fnames if Path(f).is_file()}

        # Ensure tags for all relevant files are loaded/scanned
        # Scan based on relative paths derived from existing absolute paths
        self._scan_all_tags({get_rel_fname(f, self.root) for f in all_abs_fnames if Path(f).is_file()})

        # Use the scanned data stored in the instance attributes
        defines = self._defines
        references = self._references

        # --- Build Dependency Graph ---
        G = nx.MultiDiGraph()
        # Use only identifiers that have both definitions and references recorded
        idents = set(defines.keys()) & set(references.keys())
        # Get all relative filenames that were successfully scanned and exist
        scanned_rel_fnames = {get_rel_fname(f, self.root) for f in self._scanned_fnames if Path(os.path.join(self.root, f)).is_file()}

        for ident in idents:
            definers = defines[ident] & scanned_rel_fnames # Intersect with scanned files
            referencers_counts = Counter(ref for ref in references[ident] if ref in scanned_rel_fnames) # Filter refs too
            mul = 0.1 if ident.startswith("_") else 1

            for referencer, num_refs in referencers_counts.items():
                weight = math.sqrt(num_refs) * mul
                for definer in definers:
                    # Both referencer and definer are guaranteed to be in scanned_rel_fnames now
                    G.add_edge(referencer, definer, weight=weight, ident=ident)

        # Add nodes for any scanned files not yet in the graph (e.g., only definitions or only refs)
        for rel_fname in scanned_rel_fnames:
            if rel_fname not in G: # Use 'in' for node check
                G.add_node(rel_fname)

        # --- Run PageRank ---
        ranked = {}
        if G: # Check if graph is not empty
            # Calculate personalization based on chat files present in the graph
            personalization = {
                rel_fname: 100.0 / len(chat_rel_fnames)
                for rel_fname in chat_rel_fnames if rel_fname in G
            } if chat_rel_fnames else {}

            try:
                pers_args = dict(personalization=personalization, dangling=personalization) if personalization else {}
                ranked = nx.pagerank(G, weight="weight", alpha=0.85, **pers_args)
            except Exception:
                # Fallback: Rank nodes equally, respecting personalization if PageRank fails
                num_nodes = len(G)
                base_rank = 1.0 / num_nodes if num_nodes > 0 else 0
                ranked = {node: personalization.get(node, base_rank) for node in G}
                # Normalize if personalization was used
                total_rank = sum(ranked.values())
                if personalization and total_rank > 0:
                    ranked = {node: r / total_rank for node, r in ranked.items()}

        # Add rank 0 for any scanned files that didn't make it into the ranking (shouldn't happen with current logic)
        for rel_fname in scanned_rel_fnames:
            ranked.setdefault(rel_fname, 0.0)

        # --- Return sorted list of relative filenames (excluding chat files) based on rank ---
        ranked_non_chat_files = {fname: rank for fname, rank in ranked.items() if fname not in chat_rel_fnames}
        sorted_ranked_fnames = sorted(ranked_non_chat_files, key=ranked_non_chat_files.get, reverse=True)

        return sorted_ranked_fnames


    def get_related_files(self, target_fnames: List[str]) -> List[str]:
        """Finds files referencing identifiers defined in target_fnames."""
        if not target_fnames:
            return []

        target_rel_fnames = {get_rel_fname(f, self.root) for f in target_fnames if Path(f).is_file()}
        if not target_rel_fnames:
            return []

        # Assume _scan_all_tags was called previously by get_repo_map or similar
        # Use instance attributes directly
        all_tags = self._all_tags
        references = self._references

        # Find identifiers defined *in* target files using the tag tuples
        defs_in_targets = {tag[3] for tag in all_tags if tag[0] in target_rel_fnames and tag[4] == 'def'} # name is index 3, rel_fname index 0, kind index 4

        related_files = set()
        for ident in defs_in_targets:
            # references[ident] contains list of rel_fnames
            for ref_file in references.get(ident, []):
                if ref_file not in target_rel_fnames:
                    related_files.add(ref_file)
                    print(f"  [Related] File '{ref_file}' references identifier '{ident}' (defined in target file: {target_rel_fnames})", file=sys.stderr)

        return sorted(list(related_files))


    def get_ranked_tags_map_uncached(
        self, chat_fnames, other_fnames, max_map_tokens
    ):
        """Generates the map string from ranked files, fitting token limit."""

        # Get ranked list of relative filenames (excluding chat files)
        ranked_fnames = self.get_ranked_tags(chat_fnames, other_fnames)
        chat_rel_fnames = {get_rel_fname(f, self.root) for f in chat_fnames if Path(f).is_file()}

        # Prioritize important files
        other_rel_fnames = {get_rel_fname(f, self.root) for f in other_fnames if Path(f).is_file()}
        special_fnames_abs = filter_important_files([os.path.join(self.root, f) for f in other_rel_fnames])
        special_fnames_rel = {get_rel_fname(f, self.root) for f in special_fnames_abs} - chat_rel_fnames

        # Combine: special files first, then ranked, ensuring no duplicates and excluding chat files
        combined_fnames = sorted(list(special_fnames_rel)) + [
            fn for fn in ranked_fnames if fn not in special_fnames_rel and fn not in chat_rel_fnames
        ]

        if not combined_fnames:
            return ""

        # --- Binary search for optimal number of files ---
        num_files = len(combined_fnames)
        low, high = 0, num_files
        best_tree, best_tokens = "", 0
        self.tree_cache.clear() # Clear render cache for this run

        # Heuristic start point for binary search
        mid = min(int(max_map_tokens / 50), num_files) if num_files > 0 else 0

        iterations = 0
        max_iterations = int(math.log2(num_files)) + 5 if num_files > 0 else 0

        while low <= high and iterations < max_iterations:
            iterations += 1
            # Check if mid is valid before slicing
            if mid < 0 or mid > num_files: mid = (low + high) // 2 # Recalculate if out of bounds
            if mid == 0 and low == 0: # Handle initial case where mid could be 0
                 current_fnames = []
            else:
                 current_fnames = combined_fnames[:mid]

            if not current_fnames:
                 # If mid is 0, we haven't tested any files yet.
                 # If low > 0, it means even 1 file was too much.
                 if mid == 0 and low == 0:
                     # Try with 1 file if possible
                     if num_files > 0:
                         mid = 1
                         continue # Re-run loop with mid=1
                     else:
                         break # No files to test
                 else: # mid became 0 because high < low
                     break # No suitable number of files found

            # Pass only the list of filenames to render (already filtered)
            tree = self.to_tree(current_fnames)
            num_tokens = self.token_count(tree)

            if num_tokens <= max_map_tokens:
                if num_tokens >= best_tokens: # Prefer larger maps
                    best_tree, best_tokens = tree, num_tokens
                low = mid + 1 # Try more files
            else:
                high = mid - 1 # Try fewer files

            mid = (low + high) // 2

            # Optimization: Stop early if close enough to the limit
            if 0.95 * max_map_tokens < best_tokens <= max_map_tokens:
                 break

        print(f"Selected map size: {best_tokens} tokens using approx {mid} files", file=sys.stderr)
        return best_tree

    def render_tree(self, abs_fname, rel_fname, lois):
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

    def to_tree(self, file_list: List[str]):
        """Formats files into the map string by rendering their definitions."""
        if not file_list: return ""

        output_parts = []
        # Assume tags are already scanned and available in self._all_tags

        # Process the provided list of relative filenames
        for rel_fname in sorted(file_list):
            # Find definition tags for this file directly from _all_tags
            # tag tuple: (rel_fname, fname, line, name, kind)
            file_defs = [tag for tag in self._all_tags if tag[0] == rel_fname and tag[4] == 'def']

            if not file_defs:
                # List filename if no definitions found (or if ranked for other reasons)
                output_parts.append(f"\n{rel_fname}\n")
                continue

            # Get absolute path from the first definition tag found
            abs_fname = file_defs[0][1] # fname is index 1
            # Collect valid lines of interest (definitions)
            lois = sorted(list({tag[2] for tag in file_defs if tag[2] >= 0})) # line is index 2

            if lois:
                output_parts.append(f"\n{rel_fname}:\n")
                rendered_tree = self.render_tree(abs_fname, rel_fname, lois)
                output_parts.append(rendered_tree)
            else:
                # List filename if file has defs but no valid line numbers (e.g., only pygments refs)
                output_parts.append(f"\n{rel_fname}\n") # List name only

        # Join and add trailing newline
        full_output = "".join(output_parts)
        return full_output + "\n" if full_output else ""


# --- Helper Functions ---

def get_scm_fname(lang):
    """Finds the tree-sitter query file for a language."""
    script_dir = Path(__file__).parent.resolve()
    query_path = script_dir / "queries" / "tree-sitter-languages" / f"{lang}-tags.scm"
    return query_path if query_path.is_file() else None


# --- Public API Wrapper ---

class RepoMapper:
    """Manages repository analysis and map generation."""
    def __init__(self, root_dir, map_tokens=4096, tokenizer="cl100k_base", verbose=False, force_refresh=False):
        self.root = os.path.abspath(root_dir)
        self.verbose = verbose # Keep verbose flag for potential debugging needs
        self.repomap = RepoMap(
            root=self.root,
            map_tokens=map_tokens,
            verbose=verbose,
            tokenizer_name=tokenizer,
            force_refresh=force_refresh
        )

    def _is_gitignored(self, path, matches_func=None):
        """Checks if a path is gitignored using a pre-parsed matcher."""
        return matches_func and matches_func(path)

    def _get_gitignore_matcher(self):
        """Parses .gitignore and returns a matching function."""
        try:
            from gitignore_parser import parse_gitignore # Lazy import
            gitignore_path = Path(self.root) / '.gitignore'
            if gitignore_path.is_file():
                return parse_gitignore(gitignore_path)
        except ImportError:
            pass # Silently ignore if gitignore_parser is not installed
        except Exception:
            pass # Silently ignore parsing errors
        return lambda _: False # Return a function that never matches if no gitignore

    def _find_src_files(self, directory):
        """Finds source code files recursively, respecting ignores."""
        src_files = []
        compiled_ignored_dirs = [re.compile(pattern) for pattern in IGNORED_DIRS]
        gitignore_matcher = self._get_gitignore_matcher()

        root_path_obj = Path(directory)
        if not root_path_obj.is_dir():
            # Handle case where input is a single file (less common for repo mapping)
            if root_path_obj.is_file() and \
               root_path_obj.suffix.lower() not in BINARY_EXTS and \
               not self._is_gitignored(str(root_path_obj), gitignore_matcher):
                return [str(root_path_obj)]
            return []

        for root, dirs, files in os.walk(directory, topdown=True):
            root_path = Path(root)
            # Filter ignored directories
            dirs[:] = [d for d in dirs if not (
                d.startswith('.') or # Hidden dirs
                any(pattern.search(d) for pattern in compiled_ignored_dirs) or # Pattern ignores
                self._is_gitignored(str(root_path / d), gitignore_matcher) # Gitignored dirs
            )]

            for file in files:
                file_path = root_path / file
                # Check file ignores
                if not (file.startswith('.') or # Hidden files
                        file_path.suffix.lower() in BINARY_EXTS or # Binary extensions
                        self._is_gitignored(str(file_path), gitignore_matcher)): # Gitignored files
                    src_files.append(str(file_path))

        return src_files

    def generate_map(self, chat_files=None, force_refresh=None):
        """Generates the repository map string."""
        chat_files_abs = [os.path.abspath(os.path.join(self.root, f)) for f in (chat_files or [])]
        # Filter chat files to ensure they exist and are files
        chat_files_abs = [f for f in chat_files_abs if Path(f).is_file()]

        if force_refresh is not None:
            self.repomap.force_refresh = force_refresh # Update underlying mapper if specified

        # Find all source files in the repository
        all_repo_files_abs = self._find_src_files(self.root)
        if not all_repo_files_abs:
            return "" # No source files found

        # Separate files into chat context vs. others
        chat_files_set = set(chat_files_abs)
        other_files_abs = [f for f in all_repo_files_abs if f not in chat_files_set]

        # Delegate map generation to the core RepoMap instance
        # Pass absolute paths for both lists
        return self.repomap.get_repo_map(
            chat_files=chat_files_abs,
            other_files=other_files_abs,
        )

    def get_related_files(self, target_files: List[str]) -> List[str]:
        """Finds files referencing identifiers defined in target_files."""
        # Resolve target files to absolute paths and filter for existing files
        target_files_abs = [os.path.abspath(os.path.join(self.root, f)) for f in target_files]
        target_files_abs = [f for f in target_files_abs if Path(f).is_file()]

        if not target_files_abs:
            return []

        # Delegate to the core RepoMap instance, passing absolute paths
        return self.repomap.get_related_files(target_fnames=target_files_abs)


    def render_cache(self):
        """Renders all cached tags (for debugging)."""
        cache_path = Path(self.root) / TAGS_CACHE_DIR
        if not cache_path.exists():
             print("Cache directory does not exist.", file=sys.stderr)
             return ""

        all_tags = []
        all_cached_rel_fnames = set()
        try:
            cache = Cache(str(cache_path))
            for key in cache.iterkeys(): # Key should be abs_fname
                try:
                    abs_fname = key
                    if not Path(abs_fname).is_file(): continue

                    rel_fname = get_rel_fname(abs_fname, self.root)
                    all_cached_rel_fnames.add(rel_fname)
                    cached_item = cache.get(key)
                    # Check if item is dict and has 'data' which is a list (of tags)
                    if isinstance(cached_item, dict) and isinstance(cached_item.get("data"), list):
                        all_tags.extend(cached_item["data"]) # Add the list of tags
                except Exception:
                    pass # Ignore errors reading individual cache items

            cache.close()

            if not all_tags and not all_cached_rel_fnames:
                print("Cache appears empty.", file=sys.stderr)
                return ""

            # Use a temporary RepoMap instance configured for rendering
            # Use existing tokenizer from the main repomap instance
            temp_mapper = RepoMap(
                root=self.root,
                map_tokens=1_000_000, # Unlimited tokens for dump
                verbose=self.verbose,
                tokenizer_name=self.repomap.tokenizer.name,
            )
            # Assign the collected tags to the temp mapper instance
            temp_mapper._all_tags = all_tags

            # Get relative filenames that had tags
            tagged_rel_fnames = {tag[0] for tag in all_tags} # rel_fname is index 0
            # Include files that were cached but might not have yielded tags
            files_to_render = sorted(list(all_cached_rel_fnames))

            # Call to_tree on the temp mapper with the list of all cached relative filenames
            return temp_mapper.to_tree(files_to_render)

        except Exception as e:
            print(f"Error rendering cache: {e}", file=sys.stderr)
            return ""


def main():
    """Command line interface for repomapper."""
    parser = argparse.ArgumentParser(description="Generate a repository map.")
    parser.add_argument("dir", help="Root directory of the repository.")
    parser.add_argument("--map-tokens", type=int, default=4096, help="Target token limit.")
    parser.add_argument("--tokenizer", default="cl100k_base", help="Tiktoken tokenizer.")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore and overwrite cache.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output.")
    parser.add_argument("--output", help="Output file path (stdout if omitted).")
    parser.add_argument("--render-cache", action="store_true", help="Render cached tags instead of map.")
    parser.add_argument("--chat-files", nargs='*', default=[], help="Relative paths of files in chat context.")

    args = parser.parse_args()
    root_path = Path(args.dir).resolve() # Resolve root path once

    try:
        mapper = RepoMapper(
            root_dir=str(root_path), # Pass resolved string path
            map_tokens=args.map_tokens,
            tokenizer=args.tokenizer,
            verbose=args.verbose,
            force_refresh=args.force_refresh,
        )

        content = ""
        if args.render_cache:
            print("Rendering cached data...", file=sys.stderr)
            content = mapper.render_cache()
            if not content:
                print("Cache rendering produced no output.", file=sys.stderr)
                return # Exit cleanly if cache is empty/unrenderable
        else:
            # Resolve chat file paths relative to the resolved root
            # chat_files from args are relative paths
            chat_files_abs = []
            for f in args.chat_files:
                 p = root_path / f
                 # Check if it exists within the root directory before adding
                 if p.is_file() and str(p.resolve()).startswith(str(root_path)):
                     chat_files_abs.append(str(p.resolve())) # Use absolute path for generate_map
                 elif args.verbose:
                     print(f"Warning: Skipping chat file (not found or outside root): {f}", file=sys.stderr)

            print("Generating repository map...", file=sys.stderr)
            # Pass absolute chat file paths to generate_map
            content = mapper.generate_map(chat_files=chat_files_abs)
            if not content:
                 print("Map generation produced no output.", file=sys.stderr)
                 # Don't exit, allow empty output to be written/printed

        # Output the result (either map or cache render)
        if args.output:
            try:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True) # Ensure dir exists
                output_path.write_text(content, encoding="utf-8")
                if args.verbose: print(f"Output written to: {args.output}", file=sys.stderr)
            except IOError as e:
                print(f"Error writing output to {args.output}: {e}", file=sys.stderr, end="")
                print(". Falling back to stdout.")
                print(content) # Print to stdout on write error
        else:
            print(content) # Print to stdout if no output file specified

    except (ImportError, ValueError, FileNotFoundError) as e:
         print(f"Error: {e}", file=sys.stderr)
         exit(1) # Exit on critical setup errors
    except Exception as e:
         print(f"An unexpected error occurred: {e}", file=sys.stderr)
         if args.verbose:
             import traceback
             traceback.print_exc(file=sys.stderr)
         exit(1) # Exit on unexpected errors


if __name__ == "__main__":
    main()
