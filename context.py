#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Generates the context string (<context>) for Emigo sessions."""

import os
import sys
from typing import List, Callable, Optional

from repomapper import RepoMapper

def generate_context_string(
    session_path: str,
    chat_files: List[str],
    repo_mapper: Optional[RepoMapper],
    file_cache_getter: Callable[[str], Optional[str]],
    verbose: bool = False
) -> str:
    """
    Generates the <context> string including file structure,
    cached file content, and the repository map.

    Args:
        session_path: The absolute path to the session's root directory.
        chat_files: A list of relative paths (from session_path) of files in the chat.
        repo_mapper: An instance of RepoMapper for the session.
        file_cache_getter: A function that takes a relative path and returns its cached content.
        verbose: Flag for verbose logging.

    Returns:
        The formatted <context> string.
    """
    details = "<context>\n"
    details += f"# Session Directory\n{session_path.replace(os.sep, '/')}\n\n" # Use POSIX path

    # --- File/Directory Listing ---
    details += "# File/Directory Structure (Source Files Only)\n"
    try:
        if repo_mapper:
            # Use RepoMapper's file finding logic for consistency
            # Note: _find_src_files returns absolute paths
            all_files_abs = repo_mapper._find_src_files(session_path) # Find files respecting ignores
            tree_lines = []
            processed_dirs = set()
            for abs_file in sorted(all_files_abs):
                try:
                    rel_file = os.path.relpath(abs_file, session_path).replace(os.sep, '/')
                    parts = rel_file.split('/')
                    current_path_prefix = ""
                    for i, part in enumerate(parts[:-1]): # Iterate through directories
                        current_path_prefix = f"{current_path_prefix}{part}/"
                        if current_path_prefix not in processed_dirs:
                            indent = '  ' * i
                            tree_lines.append(f"{indent}- {part}/")
                            processed_dirs.add(current_path_prefix)
                    # Add the file
                    indent = '  ' * (len(parts) - 1)
                    tree_lines.append(f"{indent}- {parts[-1]}")
                except ValueError:
                    # Handle cases where relpath fails (e.g., different drives on Windows)
                    if verbose:
                        print(f"Warning: Could not get relative path for {abs_file} relative to {session_path}", file=sys.stderr)
                    continue # Skip this file

            if tree_lines:
                details += "```\n" + "\n".join(tree_lines) + "\n```\n\n"
            else:
                details += "(No relevant source files or directories found)\n\n"
        else:
            details += "(RepoMapper not available for file listing)\n\n"
    except Exception as e:
        details += f"# Error listing files/directories: {str(e)}\n\n"

    # --- List Added Files and Content ---
    if chat_files:
        details += "# Files Currently in Chat Context\n"
        for rel_path in sorted(chat_files): # Sort for consistent order
            posix_rel_path = rel_path.replace(os.sep, '/')
            try:
                # Get content using the provided cache getter function
                content = file_cache_getter(rel_path)
                if content is None:
                    # Attempt to read directly if cache missed and getter allows it
                    # This depends on the implementation of file_cache_getter
                    # For simplicity, we'll just report the cache miss here.
                    content = f"# Error: Could not get cached content for {posix_rel_path}\n"
                    if verbose:
                         print(f"Cache miss reported by getter for {rel_path}", file=sys.stderr)

                # Use markdown code block for file content
                details += f"## File: {posix_rel_path}\n```\n{content}\n```\n\n"

            except Exception as e:
                details += f"## File: {posix_rel_path}\n# Error accessing file content: {e}\n\n"

    # --- Repository Map ---
    details += "# Repository Map (Ranked by Relevance, Excludes Chat Files)\n"
    try:
        if repo_mapper:
            # Convert relative chat file paths to absolute for RepoMapper
            chat_files_abs = [os.path.abspath(os.path.join(session_path, f)) for f in chat_files]
            # Generate the map - RepoMapper handles finding other files and ranking
            # Pass only chat_files; RepoMapper finds 'other_files' itself.
            repo_map_content = repo_mapper.generate_map(chat_files=chat_files_abs)
            if repo_map_content:
                details += repo_map_content # Add the generated map
            else:
                details += "(No relevant files found for repository map or map is empty)\n"
        else:
            details += "(RepoMapper not available for map generation)\n"
    except Exception as e:
        details += f"# Error generating repository map: {str(e)}\n"
        # Optionally include traceback if verbose
        if verbose:
            import traceback
            details += f"# Traceback:\n{traceback.format_exc()}\n"

    details += "\n</context>"
    return details
