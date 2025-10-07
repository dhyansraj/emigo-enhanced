#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Tool Implementations for the Emigo Agent.

This module defines the concrete Python functions that correspond to the tools
the LLM agent can request (as defined in `system_prompt.py`). These functions
are dispatched by the main `emigo.py` process after receiving a tool request
from the `llm_worker.py` and potentially obtaining user approval via Emacs.

Each tool function receives the relevant `Session` object (providing access to
session state like the root path and caches) and a dictionary of parameters
extracted from the LLM's request.

Tools interact with the user's environment primarily by:
- Calling back to Emacs functions via `utils.py` (e.g., for executing commands,
  replacing text in buffers, asking questions).
- Interacting with the file system within the session's directory.
- Modifying the session state (e.g., adding files to context, updating caches).

Each tool function returns a string result formatted for the LLM, indicating
success (often with output) or failure (with an error message).
"""

import os
import sys
import json
import re
import traceback
import difflib
from typing import Dict, List, Tuple, Optional, Any # Add Any

# Import Session class for type hinting and accessing session state
from session import Session
# Import utilities for calling Emacs and file reading
from utils import get_emacs_func_result, eval_in_emacs, read_file_content
# Import system prompt constants for standard messages/prefixes
from config import (
    TOOL_RESULT_SUCCESS, TOOL_RESULT_OUTPUT_PREFIX,
    TOOL_DENIED, TOOL_ERROR_PREFIX, TOOL_ERROR_SUFFIX
)

# --- Helper Functions ---

def _format_tool_result(result_content: str) -> str:
    """Formats a successful tool result."""
    # Simple format for now
    return f"{TOOL_RESULT_SUCCESS}\n{result_content}"

def _format_tool_error(error_message: str) -> str:
    """Formats a tool error message using standard prefixes/suffixes."""
    return f"{TOOL_ERROR_PREFIX}{error_message}{TOOL_ERROR_SUFFIX}"

def _resolve_path(session_path: str, rel_path: str) -> str:
    """Resolves a relative path within the session path."""
    return os.path.abspath(os.path.join(session_path, rel_path))

def _posix_path(path: str) -> str:
    """Converts a path to use POSIX separators."""
    return path.replace(os.sep, '/')

# --- Tool Implementations ---

def execute_command(session: Session, parameters: Dict[str, Any]) -> str:
    """Executes a shell command via Emacs."""
    command = parameters.get("command")
    if not command:
        return _format_tool_error("Missing required parameter 'command'")

    try:
        print(f"Executing command: {command} in {session.session_path}", file=sys.stderr)
        # Use synchronous call to Emacs to run command and get result
        output = get_emacs_func_result("execute-command-sync", session.session_path, command)
        return _format_tool_result(f"{TOOL_RESULT_OUTPUT_PREFIX}{output}")
    except Exception as e:
        print(f"Error executing command '{command}' via Emacs: {e}", file=sys.stderr)
        return _format_tool_error(f"Error executing command: {e}")

def read_file(session: Session, parameters: Dict[str, Any]) -> str:
    """Reads a file, adds it to context, and updates the session cache."""
    rel_path = parameters.get("path")
    if not rel_path:
        return _format_tool_error("Missing required parameter 'path'")

    abs_path = _resolve_path(session.session_path, rel_path)
    posix_rel_path = _posix_path(rel_path)

    try:
        if not os.path.isfile(abs_path):
             return _format_tool_error(f"File not found: {posix_rel_path}")

        # Add file to context list (Session class handles duplicates)
        added, add_msg = session.add_file_to_context(abs_path) # Use abs_path here
        if added:
            print(add_msg, file=sys.stderr)
            eval_in_emacs("message", f"[Emigo] {add_msg}") # Notify Emacs

        # Session._update_file_cache (called by add_file_to_context or get_cached_content)
        # handles reading and caching. We just need to ensure it's in context.
        # Force a cache update/read if it wasn't already added.
        if not added:
            session._update_file_cache(rel_path)

        # Return success message; content is now cached for environment details
        return _format_tool_result(f"File '{posix_rel_path}' read and added to context.")
    except Exception as e:
        print(f"Error reading file '{rel_path}': {e}", file=sys.stderr)
        session.invalidate_cache(rel_path) # Invalidate cache on error
        return _format_tool_error(f"Error reading file: {e}")

def write_to_file(session: Session, parameters: Dict[str, Any]) -> str:
    """Writes content to a file and updates the session cache."""
    rel_path = parameters.get("path")
    content = parameters.get("content") # Use get for content as well
    if not rel_path:
        return _format_tool_error("Missing required parameter 'path'")
    if content is None: # Check if content is None (missing)
        return _format_tool_error("Missing required parameter 'content'")

    abs_path = _resolve_path(session.session_path, rel_path)
    posix_rel_path = _posix_path(rel_path)

    try:
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        # Write the file directly
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Written content to {abs_path}", file=sys.stderr)

        # Inform Emacs about the change so it can prompt user to revert if needed
        eval_in_emacs("emigo--file-written-externally", abs_path)

        # Update session cache with the written content
        session._update_file_cache(rel_path, content=content)

        return _format_tool_result(f"File '{posix_rel_path}' written successfully.")

    except Exception as e:
        print(f"Error writing file '{rel_path}': {e}", file=sys.stderr)
        session.invalidate_cache(rel_path) # Invalidate cache on error
        return _format_tool_error(f"Error writing file: {e}")

def _parse_search_replace_blocks(diff_str: str) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    """Parses *all* SEARCH/REPLACE blocks from a diff string.

    Args:
        diff_str: The string containing one or more SEARCH/REPLACE blocks.

    Returns:
        A tuple containing:
        - A list of (search_text, replace_text) tuples for each valid block found.
        - An error message string if parsing fails, otherwise None.
    """
    search_marker = "<<<<<<< SEARCH\n"
    divider_marker = "\n=======\n"
    replace_marker = "\n>>>>>>> REPLACE"
    blocks = []
    # Use regex to find all blocks non-greedily
    pattern = re.compile(
        re.escape(search_marker) +
        '(.*?)' +  # Capture search text (non-greedy)
        re.escape(divider_marker) +
        '(.*?)' +  # Capture replace text (non-greedy)
        re.escape(replace_marker),
        re.DOTALL  # Allow '.' to match newlines
    )

    found_blocks_raw = pattern.findall(diff_str)

    if not found_blocks_raw:
        # Check for common markdown fence if no blocks found
        if "```" in diff_str and search_marker not in diff_str:
            return [], "Diff content seems to be a markdown code block, not a SEARCH/REPLACE block."
        return [], "No valid SEARCH/REPLACE blocks found in the provided diff."

    for search_text, replace_text in found_blocks_raw:
        # Basic validation: ensure markers are not nested within text itself in unexpected ways
        # This check is basic and might not catch all complex nesting scenarios.
        if search_marker in search_text or divider_marker in search_text or replace_marker in search_text or \
           search_marker in replace_text or divider_marker in replace_text or replace_marker in replace_text:
            return [], f"Detected malformed or nested SEARCH/REPLACE markers within a block's content:\nSearch:\n{search_text}\nReplace:\n{replace_text}"

        # Optional: Remove trailing newline from replace_text if needed,
        # but generally keep content as-is from the LLM.
        # if replace_text.endswith('\n'):
        #     replace_text = replace_text[:-1]

        blocks.append((search_text, replace_text))

    return blocks, None

def _get_line_number(text: str, char_index: int) -> int:
    """Calculates the 1-based line number for a given character index."""
    return text.count('\n', 0, char_index) + 1

def replace_in_file(session: Session, parameters: Dict[str, str]) -> str:
    """Replaces content in a file using SEARCH/REPLACE blocks via Emacs."""
    rel_path = parameters.get("path")
    diff_str = parameters.get("diff")
    similarity_threshold = 0.85 # Configurable threshold (85%)

    abs_path = os.path.abspath(os.path.join(session.session_path, rel_path))
    posix_rel_path = rel_path.replace(os.sep, '/')

    try:
        if not os.path.isfile(abs_path):
            return _format_tool_error(f"File not found: {rel_path}. Please ensure it's added to the chat first.")

        # --- Get File Content ---
        # Use the session's method to get cached content (updates if stale)
        file_content = session.get_cached_content(rel_path)
        if file_content is None:
            # If get_cached_content returns None, it means the file likely doesn't exist
            # or couldn't be read/cached previously.
            return _format_tool_error(f"Could not get content for file: {posix_rel_path}. It might not exist or be readable.")

        # Note: session.get_cached_content already handles reading if necessary.
        # The check below is redundant if get_cached_content works correctly,
        # but we keep it as a safeguard against potential error strings stored in cache.
        if file_content.startswith("# Error"): # Check if cached content is an error message
             return _format_tool_error(f"Cannot perform replacement. Cached content indicates a previous error for: {posix_rel_path}. Please use read_file again.")

        # --- Parse *All* Diff Blocks ---
        parsed_blocks, parse_error = _parse_search_replace_blocks(diff_str)
        print("Block", parsed_blocks, "Error", parse_error)
        if parse_error:
            return _format_tool_error(parse_error)
        if not parsed_blocks:
            return _format_tool_error("No valid SEARCH/REPLACE blocks found in the diff.")

        # --- Sequential Line-by-Line Matching Logic ---
        file_lines = file_content.splitlines(keepends=True) # Keep endings for accurate line numbers
        replacements_to_apply = [] # List of (start_line, elisp_end_line, replace_text)
        errors = []
        already_matched_file_line_indices = set() # Track file lines used in successful matches

        def _compare_stripped_lines(line1: str, line2: str) -> float:
            """Compares two lines after stripping whitespace and returns similarity ratio."""
            stripped1 = line1.strip()
            stripped2 = line2.strip()
            if not stripped1 and not stripped2: # Both are whitespace/empty
                return 1.0
            if not stripped1 or not stripped2: # One is whitespace/empty, the other isn't
                return 0.0
            # Use SequenceMatcher for similarity ratio on stripped lines
            return difflib.SequenceMatcher(None, stripped1, stripped2).ratio()

        # Iterate through each SEARCH/REPLACE block provided
        for block_index, (search_text, replace_text) in enumerate(parsed_blocks):
            search_lines = search_text.splitlines(keepends=True)
            if not search_lines or not search_text.strip():
                errors.append(f"Block {block_index+1}: SEARCH block is empty or contains only whitespace.")
                continue

            found_match_for_block = False
            # Iterate through each line of the actual file content as a potential start
            # Use range(len(file_lines)) to avoid issues if file_lines is modified (it shouldn't be here)
            for file_start_index in range(len(file_lines)):
                # Check if this starting line is already part of a previous successful match
                if file_start_index in already_matched_file_line_indices:
                    continue # Skip this starting line if it's already consumed

                # --- Attempt to match the *entire* search block starting here ---
                current_match_len = 0
                potential_match_indices = set() # Track indices for this *potential* match
                all_search_lines_matched_sequentially = True

                for search_line_index in range(len(search_lines)):
                    current_file_index = file_start_index + search_line_index

                    # Check bounds and if the *current* file line is already consumed
                    if current_file_index >= len(file_lines) or current_file_index in already_matched_file_line_indices:
                        all_search_lines_matched_sequentially = False
                        # print(f"  Debug: Match failed at search line {search_line_index+1}: File index {current_file_index} out of bounds or already matched.", file=sys.stderr)
                        break # Cannot match further from this file_start_index

                    # Compare current search line with corresponding file line (stripped)
                    match_ratio = _compare_stripped_lines(search_lines[search_line_index], file_lines[current_file_index])

                    if match_ratio < similarity_threshold:
                        all_search_lines_matched_sequentially = False
                        # print(f"  Debug: Match failed at search line {search_line_index+1}: Similarity {match_ratio:.2f} < {similarity_threshold} for file index {current_file_index}.", file=sys.stderr)
                        break # Mismatch found, abandon this sequence attempt for this file_start_index

                    # Line matches, record index for this potential block match
                    potential_match_indices.add(current_file_index)
                    current_match_len += 1

                # --- Check if the *entire block* matched sequentially ---
                if all_search_lines_matched_sequentially:
                    # --- Match Found for this block ---
                    start_line_num = file_start_index + 1 # 1-based line number
                    # End line is the start line + number of matched lines
                    end_line_num_inclusive = start_line_num + current_match_len - 1
                    # Elisp needs the line number *after* the last line to delete
                    elisp_end_line_num = end_line_num_inclusive + 1

                    replacements_to_apply.append((start_line_num, elisp_end_line_num, replace_text))
                    found_match_for_block = True

                    # Mark the file lines used by this *confirmed* match as consumed
                    already_matched_file_line_indices.update(potential_match_indices)

                    print(f"Block {block_index+1}: Found sequential match for lines {start_line_num}-{end_line_num_inclusive} (Elisp end: {elisp_end_line_num}) in '{posix_rel_path}'", file=sys.stderr)

                    # Stop searching for *this specific block* once a match is found
                    break # Exit the inner loop (file_start_index loop) and move to the next block in parsed_blocks

            # If no match was found for this block after checking all possible start lines
            if not found_match_for_block:
                 errors.append(
                    f"Block {block_index+1}: Could not find a sequential match for the SEARCH text in '{posix_rel_path}'.\n"
                    f"SEARCH block start:\n```\n{''.join(search_lines[:5])}{'...' if len(search_lines) > 5 else ''}\n```" # Show start of block
                 )

        # --- Handle Errors or Proceed ---
        if errors:
            error_header = f"Failed to apply replacements to '{posix_rel_path}' due to {len(errors)} error(s):\n"
            error_details = "\n\n".join(errors)
            # Suggest reading the file again
            error_footer = "\nPlease use read_file to get the exact current content and try again with updated SEARCH blocks."
            return _format_tool_error(error_header + error_details + error_footer)

        if not replacements_to_apply:
             return _format_tool_error("No replacements could be applied (all blocks failed matching or were empty).")


        # --- Call Elisp to Perform Multiple Replacements ---
        try:
            # Serialize the list of replacements to JSON for Elisp
            # Convert Python list to JSON array string that Elisp can parse
            replacements_json = json.dumps(replacements_to_apply)
            print(f"Requesting {len(replacements_to_apply)} replacements in '{posix_rel_path}' via Elisp.", file=sys.stderr)

            result = get_emacs_func_result("replace-regions-sync", abs_path, replacements_json)

            # --- Process Elisp Result ---
            if result is True or str(result).lower() == 't': # Check for elisp t
                print(f"Elisp successfully applied {len(replacements_to_apply)} replacements to '{rel_path}'.", file=sys.stderr)
                # Success: Re-read content from Emacs and update session cache
                try:
                    updated_content = read_file_content(abs_path)
                    # Use session's method to update cache with new content
                    session._update_file_cache(rel_path, content=updated_content)
                    print(f"Updated session cache for '{rel_path}' after successful replacement.", file=sys.stderr)
                except Exception as read_err:
                    print(f"Warning: Failed to re-read file '{rel_path}' after replacement to update cache: {read_err}", file=sys.stderr)
                    # Invalidate cache entry on read error using session method
                    session.invalidate_cache(rel_path)
                    # Return success, but mention the cache issue
                    return _format_tool_result(f"{TOOL_RESULT_SUCCESS}\nFile '{posix_rel_path}' modified successfully by applying {len(replacements_to_apply)} block(s).\n(Warning: Could not update session cache after modification.)")

                return _format_tool_result(f"{TOOL_RESULT_SUCCESS}\nFile '{posix_rel_path}' modified successfully by applying {len(replacements_to_apply)} block(s).")
            else:
                # Elisp returned an error
                error_detail = str(result) if result else "Unknown error during multi-replacement in Emacs."
                print(f"Error applying multi-replacement via Elisp to '{rel_path}': {error_detail}", file=sys.stderr)
                return _format_tool_error(
                    f"Error applying replacements in Emacs: {error_detail}\n\n"
                    f"File: {posix_rel_path}\n"
                    f"Please check the Emacs *Messages* buffer for details."
                )
        except Exception as elisp_call_err:
             print(f"Error calling Elisp function 'replace-regions-sync' for '{rel_path}': {elisp_call_err}\n{traceback.format_exc()}", file=sys.stderr)
             return _format_tool_error(f"Error communicating with Emacs for replacement: {elisp_call_err}")

    except Exception as e:
        print(f"Error during replace_in_file for '{rel_path}': {e}\n{traceback.format_exc()}", file=sys.stderr)
        return _format_tool_error(f"Error processing replacement for {posix_rel_path}: {e}")


def ask_followup_question(session: Session, parameters: Dict[str, Any]) -> str:
    """Asks the user a question via Emacs."""
    question = parameters.get("question")
    # Options should be a list of strings from the parsed JSON parameters
    options_list = parameters.get("options")

    if not question:
        return _format_tool_error("Missing required parameter 'question'")

    try:
        # Validate options_list and convert to JSON string for Elisp
        options_json_str = "[]"
        if isinstance(options_list, list) and all(isinstance(opt, str) for opt in options_list):
            # Ensure 2-5 options as per original prompt description (optional check)
            if 2 <= len(options_list) <= 5:
                 options_json_str = json.dumps(options_list)
            else:
                 print(f"Warning: Received {len(options_list)} options, expected 2-5. Sending empty options.", file=sys.stderr)
        elif options_list is not None: # If options provided but not a list of strings
             print(f"Warning: Invalid format for options, expected list of strings: {options_list}. Sending empty options.", file=sys.stderr)

        # Ask Emacs to present the question and get the user's answer (synchronous)
        answer = get_emacs_func_result("ask-user-sync", session.session_path, question, options_json_str)

        if answer is None or answer == "": # Check for nil or empty string from Emacs
            # User likely cancelled or provided no input
            print("User cancelled or provided no answer to followup question.", file=sys.stderr)
            return TOOL_DENIED # Use standard denial message
        else:
            # Wrap answer for clarity in the LLM prompt
            return _format_tool_result(f"<answer>\n{answer}\n</answer>")
    except Exception as e:
        print(f"Error asking followup question via Emacs: {e}", file=sys.stderr)
        return _format_tool_error(f"Error asking question: {e}")

def attempt_completion(session: Session, parameters: Dict[str, Any]) -> str:
    """Signals completion to Emacs."""
    result_text = parameters.get("result")

    if result_text is None: # Check if result is missing
        return _format_tool_error("Missing required parameter 'result'")

    try:
        # Signal completion to Emacs (no command parameter)
        eval_in_emacs("emigo--signal-completion", session.session_path, result_text)
        # This tool use itself doesn't return content to the LLM, it ends the loop.
        # Return a special marker that the main process/worker can check.
        return "COMPLETION_SIGNALLED"
    except Exception as e:
        print(f"Error signalling completion to Emacs: {e}", file=sys.stderr)
        return _format_tool_error(f"Error signalling completion: {e}")

def list_repomap(session: Session, parameters: Dict[str, Any]) -> str:
    """Generates and caches the repository map, potentially focusing on a path."""
    # Get the optional path parameter, default to session root '.'
    rel_path = parameters.get("path", ".")
    abs_path = _resolve_path(session.session_path, rel_path)
    posix_rel_path = _posix_path(rel_path)

    try:
        # Validate the path
        if not os.path.isdir(abs_path):
            return _format_tool_error(f"Path is not a valid directory: {posix_rel_path}")

        chat_files = session.get_chat_files()
        print(f"Generating repomap for {session.session_path}, focusing on '{posix_rel_path}' with chat files: {chat_files}", file=sys.stderr)

        # --- TODO: Enhance RepoMapper ---
        # Currently, session.repo_mapper.generate_map likely maps the whole root.
        # Ideally, generate_map would accept abs_path or rel_path to focus the analysis.
        # For now, we proceed but the map might be broader than the requested path.
        # repo_map_content = session.repo_mapper.generate_map(chat_files=chat_files, target_path=abs_path) # Example of future call
        repo_map_content = session.repo_mapper.generate_map(chat_files=chat_files) # Current call

        if not repo_map_content:
            repo_map_content = "(No map content generated)"

        # Store the generated map content in the session cache
        session.set_last_repomap(repo_map_content)

        # Update success message to reflect the requested focus path
        return _format_tool_result(f"Repository map generated, focusing analysis around '{posix_rel_path}'.")

    except Exception as e:
        print(f"Error generating repomap for path '{posix_rel_path}': {e}\n{traceback.format_exc()}", file=sys.stderr)
        session.set_last_repomap(None) # Clear stored map on error
        return _format_tool_error(f"Error generating repository map for '{posix_rel_path}': {e}")

def list_files(session: Session, parameters: Dict[str, Any]) -> str:
    """Lists files in a directory via Emacs."""
    rel_path = parameters.get("path", ".") # Default to session path root
    recursive = parameters.get("recursive", False) # Default to False if missing or not bool

    # Ensure recursive is boolean
    if not isinstance(recursive, bool):
        recursive = str(recursive).lower() == "true"

    abs_path = _resolve_path(session.session_path, rel_path)
    posix_rel_path = _posix_path(rel_path)
    try:
        # Use Emacs function to list files respecting ignores etc.
        files_str = get_emacs_func_result("list-files-sync", abs_path, recursive)
        # Elisp function should return a newline-separated string of relative paths

        return _format_tool_result(
            f"Files in '{posix_rel_path}' ({'recursive' if recursive else 'non-recursive'}):\n{files_str}"
        )
    except Exception as e:
        print(f"Error listing files via Emacs: {e}", file=sys.stderr)
        return _format_tool_error(f"Error listing files: {e}")

def search_files(session: Session, parameters: Dict[str, Any]) -> str:
    """Searches files using Emacs's capabilities."""
    rel_path = parameters.get("path", ".")
    pattern = parameters.get("pattern")
    case_sensitive = parameters.get("case_sensitive", False) # Default to False
    max_matches_arg = parameters.get("max_matches", 50) # Default to 50

    if not pattern:
        return _format_tool_error("Missing required parameter 'pattern'")

    # Validate/sanitize max_matches
    try:
        max_matches = min(200, int(max_matches_arg)) # Cap at 200
        if max_matches <= 0:
            max_matches = 50 # Ensure positive, default 50
    except (ValueError, TypeError):
        max_matches = 50 # Default if conversion fails

    # Ensure case_sensitive is boolean
    if not isinstance(case_sensitive, bool):
        case_sensitive = str(case_sensitive).lower() == "true"

    abs_path = _resolve_path(session.session_path, rel_path)
    posix_rel_path = _posix_path(rel_path)
    search_scope_path = abs_path
    search_scope_desc = posix_rel_path

    try:
        # Check if the provided path is a file; if so, search its directory
        if os.path.isfile(abs_path):
            search_scope_path = os.path.dirname(abs_path)
            search_scope_desc = _posix_path(os.path.relpath(search_scope_path, session.session_path))
            print(f"Note: '{posix_rel_path}' is a file. Searching its directory: '{search_scope_desc}'", file=sys.stderr)
        elif not os.path.isdir(search_scope_path):
            return _format_tool_error(f"Path not found or is not a directory/file: {posix_rel_path}")

        # Call Emacs function to perform the search in the determined scope
        search_results = get_emacs_func_result(
            "search-files-sync", search_scope_path, pattern, case_sensitive, max_matches
        )

        if not search_results or search_results.strip() == "":
             return _format_tool_result(f"No matches found for pattern: {pattern} in '{search_scope_desc}'")

        result = f"Found matches for pattern '{pattern}' in '{search_scope_desc}':\n{search_results}"
        # Elisp function should ideally handle truncation notes if applicable

        return _format_tool_result(result)

    except Exception as e:
        print(f"Error searching files via Emacs: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return _format_tool_error(f"Error searching files: {e}")
