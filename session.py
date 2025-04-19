#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Manages state for a single Emigo chat session."""

import sys
import os
import time
import tiktoken
from typing import Dict, List, Optional, Tuple, Callable # Added Callable

from repomapper import RepoMapper
from utils import (
     eval_in_emacs, _filter_context, read_file_content
 )
# Import the new context generation function
from context import generate_context_string

class Session:
    """Encapsulates the state and operations for a single Emigo session."""

    def __init__(self, session_path: str, verbose: bool = False):
        self.session_path = session_path
        self.verbose = verbose
        self.history: List[Tuple[float, Dict]] = [] # List of (timestamp, message_dict)
        self.chat_files: List[str] = [] # List of relative file paths
        self.caches: Dict[str, any] = {'mtimes': {}, 'contents': {}}
        # RepoMapper instance specific to this session
        # TODO: Get map_tokens and tokenizer from config?
        self.repo_mapper = RepoMapper(root_dir=self.session_path, verbose=self.verbose)

        self.tokenizer = tiktoken.get_encoding("cl100k_base")
        print(f"Initialized Session for path: {self.session_path}", file=sys.stderr)

    def get_history(self) -> List[Tuple[float, Dict]]:
        """Returns the chat history for this session."""
        return list(self.history) # Return a copy

    def append_history(self, message: Dict):
        """Appends a message with a timestamp to the history."""
        if "role" not in message or "content" not in message:
            print(f"Warning: Attempted to add invalid message to history: {message}", file=sys.stderr)
            return
        # Filter content before appending
        filtered_message = dict(message) # Create a copy
        filtered_message["content"] = _filter_context(filtered_message["content"])
        self.history.append((time.time(), filtered_message)) # Store filtered copy

    def clear_history(self):
        """Clears the chat history for this session."""
        self.history = []
        # Note: Clearing the Emacs buffer is handled separately by the main process calling Elisp

    def get_chat_files(self) -> List[str]:
        """Returns the list of files currently in the chat context."""
        return list(self.chat_files)

    def add_file_to_context(self, filename: str) -> Tuple[bool, str]:
        """
        Adds a file to the chat context. Ensures it's relative and exists.
        Returns (success: bool, message: str).
        """
        try:
            # Expand user directory)
            filename = os.path.expanduser(filename)
            # Ensure filename is relative to session_path for consistency
            rel_filename = os.path.relpath(filename, self.session_path)
            # Check if file exists and is within session path
            abs_path = os.path.abspath(os.path.join(self.session_path, rel_filename))

            if not os.path.isfile(abs_path):
                 return False, f"File not found: {rel_filename}"
            if not abs_path.startswith(os.path.abspath(self.session_path)):
                 return False, f"File is outside session directory: {rel_filename}"

            # Add to context if not already present
            if rel_filename not in self.chat_files:
                self.chat_files.append(rel_filename)

                # Update chat files information to Emacs.
                self._update_chat_files_info()

                # Read initial content into cache
                self._update_file_cache(rel_filename)
                return True, f"Added '{rel_filename}' to context."
            else:
                return False, f"File '{rel_filename}' already in context."

        except ValueError:
            return False, f"Cannot add file from different drive: {filename}"
        except Exception as e:
            return False, f"Error adding file '{filename}': {e}"

    def remove_file_from_context(self, filename: str) -> Tuple[bool, str]:
        """
        Removes a file from the chat context.
        Returns (success: bool, message: str).
        """
        # Ensure filename is relative for comparison
        if os.path.isabs(filename):
            try:
                rel_filename = os.path.relpath(filename, self.session_path)
            except ValueError: # filename might be on a different drive on Windows
                return False, f"Cannot remove file from different drive: {filename}"
        else:
            rel_filename = filename # Assume it's already relative

        if rel_filename in self.chat_files:
            self.chat_files.remove(rel_filename)

            # Update chat files information to Emacs.
            self._update_chat_files_info()

            # Clean up cache for the removed file
            if rel_filename in self.caches['mtimes']:
                del self.caches['mtimes'][rel_filename]
            if rel_filename in self.caches['contents']:
                del self.caches['contents'][rel_filename]
            return True, f"Removed '{rel_filename}' from context."
        else:
            return False, f"File '{rel_filename}' not found in context."

    def _update_chat_files_info(self):
        """Updates the cached info for all files in the chat context.

        This ensures we have the latest content for all files in the chat context.
        Also counts and prints the token count for each file.
        """
        file_number = 0
        tokens = 0
        for rel_path in self.chat_files:
            abs_path = os.path.join(self.session_path, rel_path)
            if os.path.exists(abs_path):
                text = read_file_content(abs_path)
                token_count = len(self.tokenizer.encode(text))
                file_number += 1
                tokens += token_count

        if file_number > 1:
            chat_file_info = f"{file_number} files [{tokens} tokens]"
        else:
            chat_file_info = f"{file_number} file [{tokens} tokens]"

        eval_in_emacs("emigo-update-chat-files-info", self.session_path, chat_file_info)

    def _update_file_cache(self, rel_path: str, content: Optional[str] = None) -> bool:
        """Updates the cache (mtime, content) for a given relative file path."""
        abs_path = os.path.abspath(os.path.join(self.session_path, rel_path))
        try:
            current_mtime = self.repo_mapper.repo_mapper.get_mtime(abs_path) # Access inner RepoMap
            if current_mtime is None: # File deleted or inaccessible
                if rel_path in self.caches['mtimes']:
                    del self.caches['mtimes'][rel_path]
                if rel_path in self.caches['contents']:
                    del self.caches['contents'][rel_path]
                return False

            # If content is provided (e.g., after write/replace), use it. Otherwise, read.
            if content is None:
                # Read only if mtime changed or not cached
                last_mtime = self.caches['mtimes'].get(rel_path)
                if last_mtime is None or current_mtime != last_mtime:
                    if self.verbose:
                        print(f"Cache miss/stale for {rel_path}, reading file.", file=sys.stderr)
                    content = read_file_content(abs_path)
                else:
                    # Content is up-to-date, no need to update cache content again
                    return True # Indicate cache was already fresh

            # Update cache
            self.caches['mtimes'][rel_path] = current_mtime
            self.caches['contents'][rel_path] = content

            return True

        except Exception as e:
            print(f"Error updating cache for '{rel_path}': {e}", file=sys.stderr)
            # Invalidate cache on error
            if rel_path in self.caches['mtimes']:
                del self.caches['mtimes'][rel_path]
            if rel_path in self.caches['contents']:
                del self.caches['contents'][rel_path]
            return False

    def get_cached_content(self, rel_path: str) -> Optional[str]:
        """Gets content from cache, updating if stale."""
        if self._update_file_cache(rel_path): # This reads if necessary
            return self.caches['contents'].get(rel_path)
        return None # Return None if update failed (e.g., file deleted)

    def get_context_string(self) -> str:
        """
        Generates the full context string (<context>) for the LLM,
        including file structure, chat file content, and the repository map.
        """
        # Clean up session cache for files no longer in chat_files list before generating
        current_chat_files_set = set(self.chat_files)
        for rel_path in list(self.caches['mtimes'].keys()):
            if rel_path not in current_chat_files_set:
                if rel_path in self.caches['mtimes']:
                    del self.caches['mtimes'][rel_path]
                if rel_path in self.caches['contents']:
                    del self.caches['contents'][rel_path]

        # Call the centralized context generation function
        return generate_context_string(
            session_path=self.session_path,
            chat_files=self.chat_files,
            repo_mapper=self.repo_mapper,
            file_cache_getter=self.get_cached_content, # Pass method to get cached content
            verbose=self.verbose
        )

    def invalidate_cache(self, rel_path: Optional[str] = None):
        """Invalidates cache for a specific file or the entire session."""
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
                print(f"Invalidated all caches for session {self.session_path}", file=sys.stderr)

    def set_history(self, history_dicts: List[Dict]):
        """Replaces the current history with the provided list of message dictionaries."""
        self.history = [] # Clear existing history
        for msg_dict in history_dicts:
            if "role" in msg_dict and "content" in msg_dict:
                # Filter content before appending
                filtered_message = dict(msg_dict) # Create a copy
                filtered_message["content"] = _filter_context(filtered_message["content"])
                 # Add with current timestamp, store filtered copy
                self.history.append((time.time(), filtered_message))
            else:
                print(f"Warning: Skipping invalid message dict during set_history: {msg_dict}", file=sys.stderr)
