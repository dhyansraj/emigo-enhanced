#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""LLM Interaction Worker Process."""

import sys
import json
import time
import traceback
import os
import importlib
import warnings
from typing import List, Dict, Optional, Union, Iterator

import tiktoken # For token counting

# Filter out UserWarning from pydantic used by litellm
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Import necessary components
from utils import _filter_context, get_os_name

# Add project root to sys.path if not already present
project_root = os.path.dirname(os.path.abspath(__file__))


# --- Build System Prompt Function ---

def _build_system_prompt(session_path: str, model_name: str) -> str:
    """Builds the system prompt, inserting dynamic info."""
    session_dir = session_path
    os_name = get_os_name()
    shell = "/bin/bash" # Default shell - TODO: Get from Emacs?
    homedir = os.path.expanduser("~")

    # Use .format() on the MAIN_SYSTEM_PROMPT template
    prompt = MAIN_SYSTEM_PROMPT.format(
        session_dir=session_dir.replace(os.sep, '/'), # Ensure POSIX paths
        os_name=os_name,
        shell=shell,
        homedir=homedir.replace(os.sep, '/')
    )
    return prompt


# --- System Prompt Template ---
# Load from system_prompt.py or define here if simpler
# Assuming it's defined here for simplicity based on previous context
MAIN_SYSTEM_PROMPT = """You are Emigo, an expert software developer integrated into Emacs.
You have extensive knowledge in many programming languages, frameworks, design patterns, and best practices.
Always use best practices when coding. Respect and use existing conventions, libraries, etc that are already present in the code base.

**Language Instruction**: You MUST detect the language of my question and respond in the same language. For example, if I ask a question in Chinese, you MUST reply in Chinese; if I ask in English, you MUST reply in English. This rule takes precedence over any other instructions. If you are unsure of the language, default to the language of the user's input.

====

CAPABILITIES

- You can write code, make edits or improvements to existing files, and understand the current state of a project.
- When the user initially gives you a task, information about the project structure and files currently in the chat context will be included in `<context>`. Use this context to inform your actions.
- You can use tools to interact with the system, read/write files, search, etc.

====

RULES

- Your session directory is: {session_dir}
- You cannot `cd` into a different directory. You operate from '{session_dir}'.
- Do not use the ~ character or $HOME to refer to the home directory.
- When making changes to code, always consider the context in which the code is being used. Ensure that your changes are compatible with the existing codebase and that they follow the project's coding standards and best practices.
- The user may provide a file's contents directly in their message.
- Your goal is to try to accomplish the user's task, NOT engage in a back and forth conversation.
- You are STRICTLY FORBIDDEN from starting your messages with "Great", "Certainly", "Okay", "Sure". You should NOT be conversational in your responses, but rather direct and to the point. For example you should NOT say "Great, I've updated the CSS" but instead something like "I've updated the CSS". It is important you be clear and technical in your messages.
- When presented with images, utilize your vision capabilities to thoroughly examine them and extract meaningful information. Incorporate these insights into your thought process as you accomplish the user's task.
- At the end of each user message, you will automatically receive `<context>`. This information is not written by the user themselves, but is auto-generated to provide *passive context* about the project structure and the content of files currently added to the chat. Do not treat it as a direct part of the user's request unless they explicitly refer to it. Use this context to inform your actions. Explain your use of `<context>` clearly.
- **Language Rule**: You MUST respond to my question in the same language I use to ask it. This is a strict requirement. For example, if I ask in Chinese, your response MUST be in Chinese. If you fail to detect the language, match the language of my input as closely as possible. This rule overrides any default language preferences.

====

SYSTEM INFORMATION

Operating System: {os_name}
Default Shell: {shell}
Home Directory: {homedir}
Session Directory: {session_dir}

====

OBJECTIVE

You accomplish a given task by:
1. Understanding the user's request and reviewing the `<context>` for context (file structure, cached file content).
2. Generating code, explanations, or modifications based on the request and context.
3. Providing the complete response directly. Avoid conversational filler.
"""

# --- Tool Formatting Removed ---


# Configure basic litellm settings globally
EMIGO_SITE_URL = "https://github.com/MatthewZMD/emigo"
EMIGO_APP_NAME = "Emigo"
os.environ["OR_SITE_URL"] = os.environ.get("OR_SITE_URL", EMIGO_SITE_URL)
os.environ["OR_APP_NAME"] = os.environ.get("OR_APP_NAME", EMIGO_APP_NAME)
os.environ["LITELLM_MODE"] = os.environ.get("LITELLM_MODE", "PRODUCTION")

VERBOSE_LLM_LOADING = False # Set to True for debugging litellm loading

class LazyLiteLLM:
    """Lazily loads the litellm library upon first access."""
    _lazy_module = None

    def __getattr__(self, name):
        # Avoid infinite recursion during initialization
        if name == "_lazy_module":
            return super().__getattribute__(name)

        self._load_litellm()
        return getattr(self._lazy_module, name)

    def _load_litellm(self):
        """Loads and configures the litellm module."""
        if self._lazy_module is not None:
            return

        if VERBOSE_LLM_LOADING:
            print("Loading litellm...", file=sys.stderr)
        start_time = time.time()

        try:
            self._lazy_module = importlib.import_module("litellm")

            # Basic configuration similar to Aider
            self._lazy_module.suppress_debug_info = True
            self._lazy_module.set_verbose = False
            self._lazy_module.drop_params = True # Drop unsupported params silently
            # Attempt to disable internal debugging/logging if method exists
            if hasattr(self._lazy_module, "_logging") and hasattr(
                self._lazy_module._logging, "_disable_debugging"
            ):
                self._lazy_module._logging._disable_debugging()

        except ImportError as e:
            print(
                f"Error: {e} litellm not found. Please install it: pip install litellm",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as e:
            print(f"Error loading litellm: {e}", file=sys.stderr)
            sys.exit(1)

        if VERBOSE_LLM_LOADING:
            load_time = time.time() - start_time
            print(f"Litellm loaded in {load_time:.2f} seconds.", file=sys.stderr)

# Global instance of the lazy loader
litellm = LazyLiteLLM()


class LLMClient:
    """Handles interaction with the LLM."""

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        verbose: bool = False,
    ):
        """
        Initializes the LLM client.

        Args:
            model_name: The name of the language model to use (e.g., "gpt-4o").
            api_key: Optional API key for the LLM service.
            base_url: Optional base URL for custom LLM endpoints (like Ollama).
            verbose: If True, enables verbose output.
        """
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.verbose = verbose
        self.last_response_object = None # Store raw response object

    def send(
        self,
        messages: List[Dict],
        stream: bool = True,
        temperature: float = 0.7,
        # Removed tools and tool_choice parameters
    ) -> Union[Iterator[object], object]: # Return type is iterator of chunks or full response object
        """
        Sends the provided messages list to the LLM and returns the response.

        Args:
            messages: The list of message dictionaries to send.
            stream: Whether to stream the response or wait for the full completion.
            temperature: The sampling temperature for the LLM.

        Returns:
            An iterator yielding response chunk objects if stream=True, otherwise the
            full response object.
        """
        # Ensure litellm is loaded before making the call
        litellm._load_litellm()

        completion_kwargs = {
            "model": self.model_name,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
        }

        # Add API key and base URL if they were provided
        if self.api_key:
            completion_kwargs["api_key"] = self.api_key
        if self.base_url:
            completion_kwargs["base_url"] = self.base_url
            # OLLAMA specific adjustment if needed (example)
            if "ollama" in self.model_name or (self.base_url and "ollama" in self.base_url):
                 # LiteLLM might handle this automatically, but explicitly setting can help
                 completion_kwargs["model"] = self.model_name.replace("ollama/", "")

        try:
            # Store the raw response object for potential parsing later
            self.last_response_object = None # Initialize

            # Initiate the LLM call
            response = litellm.completion(**completion_kwargs)
            self.last_response_object = response # Store the raw response

            # --- Verbose Logging ---
            if self.verbose:
                # Import json here if not already imported at the top level
                import json
                print("\n--- Sending to LLM ---", file=sys.stderr)
                # Avoid printing potentially large base64 images in verbose mode
                printable_messages = []
                for msg in messages: # Use the 'messages' argument passed to send()
                    if isinstance(msg.get("content"), list): # Handle image messages
                        new_content = []
                        for item in msg["content"]:
                            if isinstance(item, dict) and item.get("type") == "image_url":
                                # Truncate base64 data for printing
                                 img_url = item.get("image_url", {}).get("url", "")
                                 if isinstance(img_url, str) and img_url.startswith("data:"):
                                     new_content.append({"type": "image_url", "image_url": {"url": img_url[:50] + "..."}})
                                 else:
                                     new_content.append(item) # Keep non-base64 or non-string URLs
                            else:
                                new_content.append(item)
                        # Append the modified message with potentially truncated image data
                        printable_messages.append({"role": msg["role"], "content": new_content})
                    else:
                        printable_messages.append(msg) # Append non-image messages as is

                # Calculate approximate token count using litellm's utility
                token_count_str = ""
                try:
                    # Ensure litellm is loaded before using its utilities
                    litellm._load_litellm()
                    # Use litellm's token counter if available
                    count = litellm.token_counter(model=self.model_name, messages=messages)
                    token_count_str = f" (estimated {count} tokens)"
                except Exception as e:
                     # Fallback or simple message if token counting fails
                     token_count_str = f" (token count unavailable: {e})"


                print(json.dumps(printable_messages, indent=2), file=sys.stderr)
                print(f"--- End LLM Request{token_count_str} ---", file=sys.stderr)
            # --- End Verbose Logging ---

            if stream:
                # Generator to yield the raw litellm chunk objects
                def raw_chunk_stream():
                    # Move the try/except block inside the generator
                    try:
                        # The 'response' variable is accessible due to closure
                        for chunk in response:
                            # print(f"Raw chunk: {chunk}") # DEBUG: Ensure this is commented out
                            yield chunk # Yield the original chunk object
                    except litellm.exceptions.APIConnectionError as e: # Catch specific error
                        # Log the specific error clearly
                        error_details = f"Caught APIConnectionError: {e}\n"
                        if hasattr(e, 'response') and e.response:
                            try:
                                error_details += f"  Response Status: {getattr(e.response, 'status_code', 'N/A')}\n"
                                response_text = getattr(e.response, 'text', '')
                                error_details += f"  Response Content (first 500 chars): {response_text[:500]}{'...' if len(response_text) > 500 else ''}\n"
                            except Exception as detail_err: error_details += f"  (Error getting response details: {detail_err})\n"
                        if hasattr(e, 'request') and e.request:
                             try:
                                error_details += f"  Request URL: {getattr(e.request, 'url', 'N/A')}\n"
                             except Exception as detail_err: error_details += f"  (Error getting request details: {detail_err})\n"
                        print(f"\n[LLMClient Stream Error] {error_details}", file=sys.stderr)
                        print("[LLMClient Stream Error] Stream may be incomplete.", file=sys.stderr)
                        # Yield an error marker instead of just passing
                        yield {"_stream_error": True, "error_message": str(e)}
                    except Exception as e:
                        # Catch other potential errors during streaming
                        error_details = f"Caught unexpected error: {type(e).__name__} - {e}\n"
                        if hasattr(e, 'response') and e.response:
                            try:
                                error_details += f"  Response Status: {getattr(e.response, 'status_code', 'N/A')}\n"
                                response_text = getattr(e.response, 'text', '')
                                error_details += f"  Response Content (first 500 chars): {response_text[:500]}{'...' if len(response_text) > 500 else ''}\n"
                            except Exception as detail_err: error_details += f"  (Error getting response details: {detail_err})\n"
                        if hasattr(e, 'request') and e.request:
                             try:
                                error_details += f"  Request URL: {getattr(e.request, 'url', 'N/A')}\n"
                             except Exception as detail_err: error_details += f"  (Error getting request details: {detail_err})\n"
                        # Include traceback for unexpected errors
                        import traceback
                        error_details += f"  Traceback:\n{traceback.format_exc()}\n"
                        print(f"\n[LLMClient Stream Error] {error_details}", file=sys.stderr)
                        # Yield an error marker
                        yield {"_stream_error": True, "error_message": str(e)}

                return raw_chunk_stream() # Return the generator yielding full chunks
            else:
                # For non-streaming, return the raw response object
                return response # Return the whole LiteLLM response object

        # Keep exception handling for non-streaming calls or errors *before* streaming starts
        except litellm.APIConnectionError as e:
             error_message = f"API Connection Error (pre-stream or non-stream): {e}"
             print(f"\n{error_message}", file=sys.stderr)
             # For non-streaming, return the error string
             return f"[LLM Error: {error_message}]"
        except Exception as e:
             error_message = f"General Error (pre-stream or non-stream): {e}"
             print(f"\n{error_message}", file=sys.stderr)
             # For non-streaming, return the error string
             return f"[LLM Error: {error_message}]"



def send_message(msg_type, session_path, **kwargs):
    """Sends a JSON message to stdout for the main process."""
    message = {"type": msg_type, "session": session_path, **kwargs}
    try:
        print(json.dumps(message), flush=True)
    except TypeError as e:
        # Handle potential non-serializable data in kwargs
        print(json.dumps({
            "type": "error",
            "session": session_path,
            "message": f"Serialization error: {e}. Data: {repr(kwargs)}"
        }), flush=True)
    except Exception as e:
        print(json.dumps({
            "type": "error",
            "session": session_path,
            "message": f"Error sending message: {e}"
        }), flush=True)



def _count_tokens(text: str, tokenizer) -> int:
    """Count tokens in text using tokenizer or fallback method."""
    if not text:
        return 0

    if tokenizer:
        try:
            return len(tokenizer.encode(text))
        except Exception as e:
            print(f"Token counting error, using fallback: {e}", file=sys.stderr)

    # Fallback: approximate tokens as 4 chars per token
    return max(1, len(text) // 4)

def _truncate_history(history: List[Dict[str, str]], max_tokens: int, min_messages: int, tokenizer) -> List[Dict[str, str]]:
    """Truncate history to fit within token limits while preserving important messages."""
    if not history:
        return []

    # Always keep first user message for context if history is not empty
    truncated = [history[0]] if history else []
    current_tokens = _count_tokens(truncated[0]["content"], tokenizer) if truncated else 0

    # Add messages from newest to oldest until we hit the limit
    for msg in reversed(history[1:]):
        msg_tokens = _count_tokens(msg["content"], tokenizer)
        if current_tokens + msg_tokens > max_tokens:
            if len(truncated) >= min_messages:
                break
            # If we're below min messages, keep going but warn
            print("Warning: History exceeds token limit but below min message count", file=sys.stderr)

        truncated.insert(1, msg)  # Insert after first message
        current_tokens += msg_tokens

    if len(truncated) < len(history):
        print(f"History truncated from {len(history)} to {len(truncated)} messages ({current_tokens} tokens)", file=sys.stderr)

    return truncated


def handle_interaction_request(request):
    """Handles a single interaction request dictionary."""
    session_path = request.get("session_path")
    prompt = request.get("prompt")
    history = request.get("history", []) # List of (timestamp, message_dict)
    config = request.get("config", {})
    # chat_files_list = request.get("chat_files", []) # No longer needed directly
    context_str = request.get("context", "<context>\n# Error: Details not provided by main process.\n</context>") # Get details from request

    if not all([session_path, prompt]):
        send_message("error", session_path or "unknown", message="Worker received incomplete request.")
        return

    # --- Initialize LLM Client ---
    model_name = config.get("model")
    api_key = config.get("api_key")
    base_url = config.get("base_url")
    verbose = config.get("verbose", False)

    if not model_name:
        send_message("error", session_path, message="Missing 'model' in config.")
        return

    llm_client = LLMClient(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        verbose=verbose,
    )

    # --- History & Prompt Preparation ---
    # Keep track of history *during* this interaction locally
    interaction_history = [msg_dict for _, msg_dict in history] # Extract dicts

    # History truncation settings
    max_history_tokens = 8000  # Target max tokens for history
    min_history_messages = 3   # Always keep at least this many messages
    tokenizer = None
    try:
        tokenizer = tiktoken.get_encoding("cl100k_base")
        tokenizer.encode("test") # Test it works
    except Exception as e:
        print(f"Warning: Could not initialize tokenizer. Using simple character count fallback. Error: {e}", file=sys.stderr)

    # --- Interaction Handling (Single Turn) ---
    llm_error_occurred = False # Flag for critical LLM errors

    try:
        # 1. Build System Prompt (No tools)
        system_prompt = _build_system_prompt(session_path, llm_client.model_name)

        # 2. Prepare Messages (Truncate history, add environment details)
        messages_to_send = [{"role": "system", "content": system_prompt}]
        truncated_history = _truncate_history(interaction_history, max_history_tokens, min_history_messages, tokenizer)
        messages_to_send.extend(truncated_history)

        # Append environment details (passed in the request) to the last message
        if messages_to_send:
            # Use the context_str received in the request
            last_message_copy = messages_to_send[-1].copy()
            # Ensure content is a string before appending
            if not isinstance(last_message_copy.get("content"), str):
                last_message_copy["content"] = str(last_message_copy.get("content", "")) # Convert non-strings
            last_message_copy["content"] += f"\n\n{context_str}"
            messages_to_send[-1] = last_message_copy
        else:
            # Should not happen if history includes user prompt, but handle defensively
            messages_to_send.append({"role": "user", "content": context_str})

        # 3. Call LLM
        full_response_text = "" # Accumulate the textual response
        turn_llm_error = False # Track error specifically for this LLM call

        # Signal start of assistant response
        send_message("stream", session_path, role="llm", content="\nAssistant:\n")

        try:
            # Prepare arguments for llm_client.send (no tools)
            completion_args = {"stream": True}
            response_stream = llm_client.send(messages_to_send, **completion_args)

            # Stream text chunks
            for chunk in response_stream:
                # Check for stream error marker
                if isinstance(chunk, dict) and chunk.get("_stream_error"):
                    turn_llm_error = True
                    llm_error_occurred = True # Mark global error flag
                    error_message = f"[Error during LLM streaming: {chunk.get('error_message', 'Unknown stream error')}]"
                    print(f"\n{error_message}", file=sys.stderr)
                    send_message("stream", session_path, role="error", content=error_message)
                    interaction_history.append({"role": "assistant", "content": error_message})
                    break # Exit the stream processing loop

                # Safely access delta
                delta = None
                try:
                    if chunk and hasattr(chunk, 'choices') and chunk.choices and len(chunk.choices) > 0:
                         if hasattr(chunk.choices[0], 'delta'):
                             delta = chunk.choices[0].delta
                except Exception as e:
                    print(f"  - Error accessing chunk delta: {e}. Chunk: {chunk}", file=sys.stderr)
                    continue

                if not delta: continue # Skip chunk if no delta

                # Process text content
                try:
                    if hasattr(delta, 'content') and delta.content:
                        content_piece = delta.content
                        send_message("stream", session_path, role="llm", content=content_piece)
                        full_response_text += content_piece
                except Exception as e:
                     print(f"  - Error processing delta.content: {e}. Delta: {delta}", file=sys.stderr)

                # Tool call processing removed

        except Exception as e:
            turn_llm_error = True
            llm_error_occurred = True # Mark global error flag
            error_message = f"[Error during LLM communication: {e}]\n{traceback.format_exc()}"
            print(f"\n{error_message}", file=sys.stderr)
            send_message("stream", session_path, role="error", content=f"[LLM Error: {e}]")
            interaction_history.append({"role": "assistant", "content": f"[LLM Error: {e}]"})

        # Check if stream loop ended due to error
        if turn_llm_error:
            print("Worker: Ending interaction due to detected LLM stream error.", file=sys.stderr)
            # Error status will be set below

        # Add Assistant Message to History
        assistant_message = {"role": "assistant"}
        filtered_response_text = _filter_context(full_response_text.strip())
        if filtered_response_text:
            assistant_message["content"] = filtered_response_text
            interaction_history.append(assistant_message)
        elif not turn_llm_error: # Add empty message only if no error and no content
            interaction_history.append({"role": "assistant", "content": ""})

        if llm_error_occurred:
            status = "llm_error"
            finish_message = "Interaction ended due to LLM communication error."
        else: # Finished normally
            status = "success"
            finish_message = "Interaction completed."

        finish_data = {
            "status": status,
            "message": finish_message
        }
        # Include final history state unless there was a critical LLM error
        if status != "llm_error":
            finish_data["final_history"] = interaction_history # Send back the list of dicts

        send_message("finished", session_path, **finish_data)

    except Exception as e:
        tb_str = traceback.format_exc()
        error_msg = f"Critical error in interaction handler: {e}\n{tb_str}"
        print(error_msg, file=sys.stderr)
        valid_session_path = session_path or "unknown_session"
        send_message("stream", valid_session_path, role="error", content=f"[Worker Critical Error: {e}]")
        send_message("finished", valid_session_path, status="critical_error", message=error_msg)

def main():
    """Reads requests from stdin and handles them."""
    # Indicate worker is ready (optional)
    # print(json.dumps({"type": "status", "status": "ready"}), flush=True)

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                # End of input, exit gracefully
                # print(json.dumps({"type": "status", "status": "exiting", "reason": "stdin closed"}), flush=True)
                break

            request = json.loads(line)
            if request.get("type") == "interaction_request":
                handle_interaction_request(request.get("data"))
            else:
                print(f"Worker received unknown message type: {request.get('type')}", file=sys.stderr)

        except json.JSONDecodeError:
            # Log error but try to continue reading
            print(json.dumps({"type": "error", "session":"unknown", "message": f"Worker received invalid JSON: {line.strip()}"}), flush=True)
        except Exception as e:
            # Log unexpected errors
            tb_str = traceback.format_exc()
            print(json.dumps({"type": "error", "session":"unknown", "message": f"Worker main loop error: {e}\n{tb_str}"}), flush=True)
            # Depending on the error, might want to break or continue
            time.sleep(1) # Avoid tight loop on persistent error

if __name__ == "__main__":
    main()
