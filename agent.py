#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Defines the Agent class responsible for the core logic of interacting with the LLM.

This module contains the `Agent` class which encapsulates the agentic loop behavior.
It constructs prompts, processes LLM responses, and determines when to use tools.
"""

import json # Keep for parsing LLM responses if needed
import os
import sys
from typing import List, Dict, Optional

from llm import LLMClient
from repomapper import RepoMapper # Keep for agent's internal use if needed (e.g., environment details)
# Import tool definitions and provider formatting
from tool_definitions import get_all_tools
from llm_providers import get_formatted_tools
# Import only the base system prompt template
from system_prompt import MAIN_SYSTEM_PROMPT
import tiktoken # For token counting

from utils import (
    get_os_name,
    eval_in_emacs
)

class Agent:
    """
    Manages the agentic interaction loop for a given session.

    This class is instantiated by `llm_worker.py` for each interaction. It takes the
    current session state (prompt, history, context) and orchestrates the
    conversation with the LLM.

    Key Responsibilities:
    - Building the system prompt, incorporating dynamic information like the
      current working directory and OS details.
    - Preparing the full message list for the LLM, including the system prompt,
      truncated history, and environment details (provided by the worker).
    - Managing history truncation logic to stay within token limits.
    - Parsing LLM responses to identify tool usage requests using XML-like tags.
    - Determining the next step in the interaction (e.g., call tool, respond directly,
      finish).

    Note: History itself is managed by the `Session` object in the main `emigo.py`
    process and passed to the worker for each interaction. Tool implementations
    reside in `tools.py` and are executed via the main `emigo.py` process.
    """

    def __init__(self, session_path: str, llm_client: LLMClient, chat_files_ref: Dict[str, List[str]], verbose: bool = False):
        self.session_path = session_path # This is the root directory for the session
        self.llm_client = llm_client
        self.chat_files_ref = chat_files_ref # Reference to Emigo's chat_files dict
        self.environment_details_str = "" # Initialize, will be updated by worker loop
        self.verbose = verbose
        # Keep RepoMapper instance, but usage is restricted
        self.repo_mapper = RepoMapper(root_dir=self.session_path, verbose=self.verbose)
        # History truncation settings
        self.max_history_tokens = 8000  # Target max tokens for history
        self.min_history_messages = 3   # Always keep at least this many messages
        # Tokenizer for history management
        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
            # Test the tokenizer works
            test_tokens = self.tokenizer.encode("test")
            if not test_tokens:
                raise ValueError("Tokenizer returned empty tokens")
        except Exception as e:
            print(f"Warning: Could not initialize tokenizer. Using simple character count fallback. Error: {e}", file=sys.stderr)
            self.tokenizer = None

    # --- Prompt Building ---

    def _build_system_prompt(self) -> str:
        """Builds the system prompt, inserting dynamic info and formatted tool list."""
        session_dir = self.session_path
        os_name = get_os_name()
        shell = "/bin/bash" # Default shell - TODO: Get from Emacs?
        homedir = os.path.expanduser("~")

        # Get all tool definitions
        available_tools = get_all_tools()
        # Format tools for the specific LLM provider (e.g., OpenAI)
        # Assumes llm_client has model_name attribute
        formatted_tools = get_formatted_tools(available_tools, self.llm_client.model_name)
        # Convert the formatted list to a JSON string for insertion
        tools_json_string = json.dumps(formatted_tools, indent=2)

        # Use .format() on the MAIN_SYSTEM_PROMPT template
        prompt = MAIN_SYSTEM_PROMPT.format(
            session_dir=session_dir.replace(os.sep, '/'), # Ensure POSIX paths
            os_name=os_name,
            shell=shell,
            homedir=homedir.replace(os.sep, '/'),
            tools_json=tools_json_string # Insert the formatted tool definitions
        )
        return prompt

    # --- LLM Prompt Preparation & History Management ---
    # _parse_tool_use (XML parser) is removed. Parsing now happens in llm_worker.py

    def _prepare_llm_prompt(self, system_prompt: str, current_interaction_history: List[Dict]) -> List[Dict]:
        """Prepares the list of messages for the LLM, including history truncation and environment details.
        Uses the provided current_interaction_history list (list of dicts).
        Environment details are stored in self.environment_details_str."""
        # Always include system prompt
        messages_to_send = [{"role": "system", "content": system_prompt}]

        # --- History Truncation: Keep messages within token limit ---
        # Truncate the provided history list (already dicts)
        messages_to_send.extend(self._truncate_history(current_interaction_history))

        # --- Append Environment Details (Stored in self.environment_details_str) ---
        # Use copy() to avoid modifying the history object directly
        last_message_copy = messages_to_send[-1].copy()
        last_message_copy["content"] += f"\n\n{self.environment_details_str}" # Append stored details
        messages_to_send[-1] = last_message_copy # Replace the last message

        return messages_to_send

    def _call_llm_and_stream_response(self, messages_to_send: List[Dict]) -> Optional[str]:
        """Calls the LLM, streams the response, and returns the full response text."""
        full_response = ""
        eval_in_emacs("emigo--flush-buffer", self.session_path, "\nAssistant:\n", "llm") # Signal start
        try:
            # Send the temporary list with context included
            response_stream = self.llm_client.send(messages_to_send, stream=True)
            for chunk in response_stream:
                # Ensure chunk is a string, default to empty string if None
                content_to_flush = chunk or ""
                eval_in_emacs("emigo--flush-buffer", self.session_path, content_to_flush, "llm")
                if chunk: # Only append non-None chunks to full_response
                    full_response += chunk
            return full_response
        except Exception as e:
            error_message = f"[Error during LLM communication: {e}]"
            print(f"\n{error_message}", file=sys.stderr)
            eval_in_emacs("emigo--flush-buffer", self.session_path, str(error_message), "error")
            # Add error to persistent history (handled in main loop now)
            # self.llm_client.append_history({"role": "assistant", "content": error_message})
            return None # Indicate error

    # --- History Truncation & Token Counting ---

    def _truncate_history(self, history: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Truncate history to fit within token limits while preserving important messages."""
        if not history:
            return []

        # Always keep first user message for context
        truncated = [history[0]]
        current_tokens = self._count_tokens(truncated[0]["content"])

        # Add messages from newest to oldest until we hit the limit
        for msg in reversed(history[1:]):
            msg_tokens = self._count_tokens(msg["content"])
            if current_tokens + msg_tokens > self.max_history_tokens:
                if len(truncated) >= self.min_history_messages:
                    break
                # If we're below min messages, keep going but warn
                print("Warning: History exceeds token limit but below min message count", file=sys.stderr)

            truncated.insert(1, msg)  # Insert after first message
            current_tokens += msg_tokens

        if self.verbose and len(truncated) < len(history):
            print(f"History truncated from {len(history)} to {len(truncated)} messages ({current_tokens} tokens)", file=sys.stderr)

        return truncated

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text using tokenizer or fallback method."""
        if not text:
            return 0

        if self.tokenizer:
            try:
                return len(self.tokenizer.encode(text))
            except Exception as e:
                print(f"Token counting error, using fallback: {e}", file=sys.stderr)

        # Fallback: approximate tokens as 4 chars per token
        return max(1, len(text) // 4)
