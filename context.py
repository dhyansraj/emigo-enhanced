# -*- coding: utf-8 -*-

"""
Manages the state and context generation for a single Emigo chat session.

This includes chat history, files added to the context, file caching,
repository mapping, and generating the <context> string for the LLM.
"""

import os
import sys
import time
import re
import tiktoken
from typing import List, Dict, Optional, Tuple

from repomapper import RepoMapper
from utils import (
    eval_in_emacs, _filter_context, read_file_content, message_emacs
)


class Context:
    """Encapsulates the state and context generation for a single Emigo session."""

    def __init__(self, session_path: str, verbose: bool = False):
        """
        Initializes the Context for a given session path.

        Args:
            session_path: The absolute path to the root directory of the session.
            verbose: Flag for enabling verbose output.
        """
        self.session_path = session_path
        self.verbose = verbose
        self.history: List[Tuple[float, Dict]] = [] # List of (timestamp, message_dict)
        self.chat_files: List[str] = [] # List of relative file paths
        self.caches: Dict[str, any] = {'mtimes': {}, 'contents': {}}
        self.repo_mapper = RepoMapper(root_dir=self.session_path, verbose=self.verbose)

        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            print(f"Warning: Could not initialize tokenizer. Token counts may be inaccurate. Error: {e}", file=sys.stderr)
            self.tokenizer = None # Handle cases where tiktoken might fail

        print(f"Initialized Context for path: {self.session_path}", file=sys.stderr)

    # --- History Management ---

    def get_history(self) -> List[Tuple[float, Dict]]:
        """Returns a copy of the chat history for this session."""
        return list(self.history)

    def append_history(self, message: Dict):
        """Appends a message with a timestamp to the history, filtering content."""
        if not isinstance(message, dict) or "role" not in message or "content" not in message:
            print(f"Warning: Attempted to add invalid message to history: {message}", file=sys.stderr)
            return
        # Filter content before appending
        filtered_message = dict(message) # Create a copy
        # Ensure content is string before filtering
        if not isinstance(filtered_message.get("content"), str):
             filtered_message["content"] = str(filtered_message.get("content", ""))
        filtered_message["content"] = _filter_context(filtered_message["content"])
        self.history.append((time.time(), filtered_message)) # Store filtered copy

    def clear_history(self):
        """Clears the chat history for this session."""
        self.history = []
        print(f"Cleared history for context: {self.session_path}", file=sys.stderr)

    def set_history(self, history_dicts: List[Dict]):
        """Replaces the current history with the provided list of message dictionaries."""
        self.history = [] # Clear existing history
        for msg_dict in history_dicts:
            if isinstance(msg_dict, dict) and "role" in msg_dict and "content" in msg_dict:
                # Append using the standard method to ensure filtering and timestamping
                self.append_history(msg_dict)
            else:
                print(f"Warning: Skipping invalid message dict during set_history: {msg_dict}", file=sys.stderr)
        print(f"Set history for context {self.session_path} with {len(self.history)} messages.", file=sys.stderr)

    # --- Chat File Management ---

    def get_chat_files(self) -> List[str]:
        """Returns a copy of the list of files currently in the chat context."""
        return list(self.chat_files)

    def add_file_to_context(self, filename: str) -> Tuple[bool, str]:
        """
        Adds a file to the chat context. Ensures it's relative and exists.
        Updates caches and Emacs UI.
        Returns (success: bool, message: str).
        """
        try:
            # Expand user directory
            filename = os.path.expanduser(filename)
            # Ensure filename is relative to session_path for consistency
            # Check if already relative first
            if not os.path.isabs(filename):
                rel_filename = filename
            else:
                # Check if the file is within the session path before making relative
                abs_path_input = os.path.abspath(filename)
                if not abs_path_input.startswith(self.session_path):
                     # Check if it's *outside* but maybe contains the session path (e.g. symlink target)
                     # This logic might need refinement depending on desired symlink behavior.
                     # For now, strictly enforce containment.
                     return False, f"File is outside session directory: {filename}"
                rel_filename = os.path.relpath(abs_path_input, self.session_path)

            # Get the absolute path based on the relative path and session root
            abs_path = os.path.abspath(os.path.join(self.session_path, rel_filename))

            # Final checks
            if not os.path.isfile(abs_path):
                 return False, f"File not found or not a regular file: {rel_filename}"
            # Double-check containment after resolving potential symlinks in join/abspath
            if not abs_path.startswith(self.session_path):
                 return False, f"Resolved file path is outside session directory: {rel_filename}"

            # Add to context if not already present
            if rel_filename not in self.chat_files:
                self.chat_files.append(rel_filename)
                # Read initial content into cache
                self._update_file_cache(rel_filename)
                # Update chat files information in Emacs *after* successful add and cache update
                self._update_chat_files_info_in_emacs()
                return True, f"Added '{rel_filename}' to context."
            else:
                return False, f"File '{rel_filename}' already in context."

        except ValueError as e: # Handles relpath errors (e.g., different drives on Windows)
            return False, f"Cannot add file '{filename}': {e}"
        except Exception as e:
            print(f"Error adding file '{filename}': {e}\n{traceback.format_exc()}", file=sys.stderr)
            return False, f"Error adding file '{filename}': {e}"

    def remove_file_from_context(self, filename: str) -> Tuple[bool, str]:
        """
        Removes a file from the chat context. Cleans up caches and updates Emacs UI.
        Returns (success: bool, message: str).
        """
        # Ensure filename is relative for comparison
        if os.path.isabs(filename):
            try:
                # Check if the file is within the session path before making relative
                abs_path_input = os.path.abspath(filename)
                if not abs_path_input.startswith(self.session_path):
                    # If it's absolute but outside, it couldn't have been added legally.
                    # We still might want to allow removing it if its *relative* form exists.
                    # Check relative path directly.
                    rel_filename_check = os.path.relpath(abs_path_input, self.session_path)
                    if rel_filename_check not in self.chat_files:
                        return False, f"File '{filename}' (relative: '{rel_filename_check}') not found in context."
                    rel_filename = rel_filename_check # Use the relative form found
                else:
                     rel_filename = os.path.relpath(abs_path_input, self.session_path)
            except ValueError: # filename might be on a different drive on Windows
                # If it's on a different drive, it couldn't be in chat_files by relative path.
                # Check if the raw absolute path string was somehow added (shouldn't happen).
                if filename in self.chat_files:
                     rel_filename = filename # Treat the absolute path as the key if found
                else:
                    return False, f"Cannot remove file from different drive/location: {filename}"
        else:
            rel_filename = filename # Assume it's already relative

        if rel_filename in self.chat_files:
            self.chat_files.remove(rel_filename)
            # Clean up cache for the removed file
            self.invalidate_cache(rel_filename) # Use invalidate for cleanup
            # Update chat files information in Emacs *after* successful removal
            self._update_chat_files_info_in_emacs()
            return True, f"Removed '{rel_filename}' from context."
        else:
            return False, f"File '{rel_filename}' not found in context."

    def _update_chat_files_info_in_emacs(self):
        """Calculates token count for chat files and updates Emacs header line."""
        file_number = 0
        tokens = 0
        if not self.tokenizer:
            print("Warning: Tokenizer not available, cannot update token count in Emacs.", file=sys.stderr)
            chat_file_info = f"{len(self.chat_files)} file(s) [token count unavailable]"
        else:
            for rel_path in self.chat_files:
                content = self.get_cached_content(rel_path) # Use cached content
                if content is not None:
                    try:
                        token_count = len(self.tokenizer.encode(content))
                        file_number += 1
                        tokens += token_count
                    except Exception as e:
                        print(f"Error encoding content for token count ({rel_path}): {e}", file=sys.stderr)
                        # Skip token count for this file if encoding fails
                else:
                    # File might have been deleted or is inaccessible
                    print(f"Warning: Could not get content for {rel_path} to count tokens.", file=sys.stderr)

            if file_number != len(self.chat_files):
                 # Indicate if some files couldn't be counted
                 chat_file_info = f"{len(self.chat_files)} file(s) [{tokens} tokens from {file_number} files]"
            elif file_number == 1:
                 chat_file_info = f"1 file [{tokens} tokens]"
            else:
                 chat_file_info = f"{file_number} files [{tokens} tokens]"

        try:
            eval_in_emacs("emigo-update-chat-files-info", self.session_path, chat_file_info)
        except Exception as e:
            print(f"Error calling emigo-update-chat-files-info: {e}", file=sys.stderr)

    # --- File Caching ---

    def _update_file_cache(self, rel_path: str, content: Optional[str] = None) -> bool:
        """Updates the cache (mtime, content) for a given relative file path."""
        abs_path = os.path.abspath(os.path.join(self.session_path, rel_path))
        try:
            # Use RepoMapper's mtime getter if available
            current_mtime = self.repo_mapper.repo_mapper.get_mtime(abs_path) if self.repo_mapper else None
            if current_mtime is None: # File deleted, inaccessible, or RepoMapper failed
                # Fallback to os.path.getmtime if RepoMapper didn't provide it
                if os.path.isfile(abs_path):
                    current_mtime = os.path.getmtime(abs_path)
                else:
                    # File truly gone or inaccessible
                    self.invalidate_cache(rel_path) # Ensure cache is cleared
                    if self.verbose:
                        print(f"File {rel_path} not found or inaccessible, cache cleared.", file=sys.stderr)
                    return False

            # If content is provided (e.g., after write/replace), use it. Otherwise, read.
            if content is None:
                # Read only if mtime changed or not cached
                last_mtime = self.caches['mtimes'].get(rel_path)
                if last_mtime is None or current_mtime != last_mtime or rel_path not in self.caches['contents']:
                    if self.verbose:
                        print(f"Cache miss/stale for {rel_path}, reading file.", file=sys.stderr)
                    content = read_file_content(abs_path) # Reads the file
                    if content is None: # Handle read failure
                         print(f"Error reading file content for {rel_path}", file=sys.stderr)
                         self.invalidate_cache(rel_path)
                         return False
                else:
                    # Content is up-to-date, no need to update cache content again
                    return True # Indicate cache was already fresh

            # Update cache
            self.caches['mtimes'][rel_path] = current_mtime
            self.caches['contents'][rel_path] = content
            return True

        except Exception as e:
            print(f"Error updating cache for '{rel_path}': {e}", file=sys.stderr)
            self.invalidate_cache(rel_path) # Invalidate cache on error
            return False

    def get_cached_content(self, rel_path: str) -> Optional[str]:
        """Gets content from cache, updating if stale or reading if necessary."""
        # Ensure the cache is up-to-date first. _update_file_cache reads if needed.
        if self._update_file_cache(rel_path):
            return self.caches['contents'].get(rel_path)
        # Return None if update failed (e.g., file deleted, read error)
        return None

    def invalidate_cache(self, rel_path: Optional[str] = None):
        """Invalidates cache for a specific file or the entire context."""
        if rel_path:
            if rel_path in self.caches['mtimes']:
                del self.caches['mtimes'][rel_path]
            if rel_path in self.caches['contents']:
                del self.caches['contents'][rel_path]
            if self.verbose:
                print(f"Invalidated cache for {rel_path}", file=sys.stderr)
        else:
            self.caches['mtimes'].clear()
            self.caches['contents'].clear()
            if self.verbose:
                print(f"Invalidated all caches for context {self.session_path}", file=sys.stderr)

    # --- Context String Generation ---

    def _handle_file_mentions(self, prompt: str):
        """Finds @file mentions in the prompt and adds them to context."""
        mention_pattern = r'@(\S+)'
        # Avoid matching email addresses by requiring path-like characters or quotes
        # A simpler pattern might be sufficient if complex filenames aren't common.
        # mention_pattern = r'@([\w./\\"-]+)' # Example allowing more chars

        mentioned_files_in_prompt = re.findall(mention_pattern, prompt)
        if mentioned_files_in_prompt:
            print(f"Found file mentions in prompt: {mentioned_files_in_prompt}", file=sys.stderr)
            added_files = []
            failed_files = []
            for file_mention in mentioned_files_in_prompt:
                # Attempt to add the mentioned file
                success, msg = self.add_file_to_context(file_mention)
                if success:
                    added_files.append(file_mention)
                    # message_emacs(msg) # Notify Emacs immediately (optional)
                elif "already in context" not in msg: # Don't report failure if already added
                    failed_files.append(f"{file_mention} ({msg})")

            # Report summary to Emacs after processing all mentions
            report_msg = ""
            if added_files:
                report_msg += f"Auto-added mentioned files: {', '.join(added_files)}. "
            if failed_files:
                report_msg += f"Failed to add mentioned files: {', '.join(failed_files)}."

            if report_msg:
                 message_emacs(f"[Emigo] {report_msg.strip()}")


    def generate_context_string(self, current_prompt: Optional[str] = None) -> str:
        """
        Generates the full context string (<context>) for the LLM,
        including file structure, chat file content, and the repository map.
        Handles @file mentions in the current_prompt if provided.

        Args:
            current_prompt: The user's latest prompt (optional). If provided,
                            @file mentions within it will be processed.

        Returns:
            The formatted <context> string.
        """
        # Handle @file mentions if a prompt is provided
        if current_prompt:
            self._handle_file_mentions(current_prompt)

        # Clean up session cache for files no longer in chat_files list before generating
        # (This might be redundant if remove_file_from_context handles it, but safe to keep)
        current_chat_files_set = set(self.chat_files)
        for rel_path in list(self.caches['mtimes'].keys()):
            if rel_path not in current_chat_files_set:
                self.invalidate_cache(rel_path)

        # --- Start Building Context String ---
        details = "<context>\n"
        details += f"# Session Directory\n{self.session_path.replace(os.sep, '/')}\n\n" # Use POSIX path

        # --- File/Directory Listing ---
        details += "# File/Directory Structure (Source Files Only)\n"
        try:
            if self.repo_mapper:
                # Use RepoMapper's file finding logic for consistency
                all_files_abs = self.repo_mapper._find_src_files(self.session_path) # Find files respecting ignores
                tree_lines = []
                processed_dirs = set()
                for abs_file in sorted(all_files_abs):
                    try:
                        # Ensure the file is actually within the session path directory tree
                        if not abs_file.startswith(self.session_path):
                            continue # Skip files outside the session path (e.g., symlinks pointing out)

                        rel_file = os.path.relpath(abs_file, self.session_path).replace(os.sep, '/')
                        parts = rel_file.split('/')
                        current_path_prefix = ""
                        for i, part in enumerate(parts[:-1]): # Iterate through directories
                            dir_indent_level = i
                            current_path_prefix = f"{current_path_prefix}{part}/"
                            if current_path_prefix not in processed_dirs:
                                indent = '  ' * dir_indent_level
                                tree_lines.append(f"{indent}- {part}/")
                                processed_dirs.add(current_path_prefix)
                        # Add the file
                        file_indent_level = len(parts) - 1
                        indent = '  ' * file_indent_level
                        tree_lines.append(f"{indent}- {parts[-1]}")
                    except ValueError:
                        if self.verbose:
                            print(f"Warning: Could not get relative path for {abs_file} relative to {self.session_path}", file=sys.stderr)
                        continue # Skip this file

                if tree_lines:
                    details += "```\n" + "\n".join(tree_lines) + "\n```\n\n"
                else:
                    details += "(No relevant source files or directories found)\n\n"
            else:
                details += "(RepoMapper not available for file listing)\n\n"
        except Exception as e:
            details += f"# Error listing files/directories: {str(e)}\n\n"
            if self.verbose:
                import traceback
                details += f"# Traceback:\n{traceback.format_exc()}\n"


        # --- List Added Files and Content ---
        if self.chat_files:
            details += "# Files Currently in Chat Context\n"
            for rel_path in sorted(self.chat_files): # Sort for consistent order
                posix_rel_path = rel_path.replace(os.sep, '/')
                try:
                    # Get content using the internal cache getter method
                    content = self.get_cached_content(rel_path)
                    if content is None:
                        content = f"# Error: Could not get content for {posix_rel_path}\n"
                        if self.verbose:
                             print(f"Content retrieval failed for {rel_path} during context generation.", file=sys.stderr)

                    # Use markdown code block for file content
                    details += f"## File: {posix_rel_path}\n```\n{content}\n```\n\n"

                except Exception as e:
                    details += f"## File: {posix_rel_path}\n# Error accessing file content: {e}\n\n"
        else:
             details += "# Files Currently in Chat Context\n(None)\n\n"


        # --- Repository Map ---
        details += "# Repository Map (Ranked by Relevance, Excludes Chat Files)\n"
        try:
            if self.repo_mapper:
                # Convert relative chat file paths to absolute for RepoMapper
                chat_files_abs = [os.path.abspath(os.path.join(self.session_path, f)) for f in self.chat_files]
                # Generate the map - RepoMapper handles finding other files and ranking
                repo_map_content = self.repo_mapper.generate_map(chat_files=chat_files_abs)
                if repo_map_content and repo_map_content.strip():
                    details += repo_map_content # Add the generated map
                else:
                    details += "(No relevant files found for repository map or map is empty)\n"
            else:
                details += "(RepoMapper not available for map generation)\n"
        except Exception as e:
            details += f"# Error generating repository map: {str(e)}\n"
            # Optionally include traceback if verbose
            if self.verbose:
                import traceback
                details += f"# Traceback:\n{traceback.format_exc()}\n"

        details += "\n</context>"
        return details
