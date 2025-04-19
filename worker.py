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

# Filter out UserWarning from pydantic used by litellm
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Import necessary components
from utils import _filter_context, get_os_name
from llm import LLMClient # Import the LLMClient

# Add project root to sys.path if not already present
project_root = os.path.dirname(os.path.abspath(__file__))

# --- System Prompt Template ---
# Load from system_prompt.py or define here if simpler
# Assuming it's defined here for simplicity based on previous context
MAIN_SYSTEM_PROMPT = """You are Emigo, an expert software developer integrated into Emacs.
You have extensive knowledge in many programming languages, frameworks, design patterns, and everything in between.
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


def handle_interaction_request(request_data):
    """Handles a single interaction request dictionary."""
    session_path = request_data.get("session_path")
    user_prompt_dict = request_data.get("user_prompt") # The original user prompt dict
    history = request_data.get("history", []) # List of message dicts (already truncated)
    config = request_data.get("config", {})
    # chat_files_list = request_data.get("chat_files", []) # Still available if needed
    context_str = request_data.get("context", "<context>\n# Error: Details not provided by main process.\n</context>")

    if not all([session_path, user_prompt_dict, isinstance(history, list)]):
        send_message("error", session_path or "unknown", message=f"Worker received incomplete request data: {request_data}")
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

    # --- Interaction Handling (Single Turn) ---
    llm_error_occurred = False # Flag for critical LLM errors

    try:
        # 1. Build System Prompt
        system_prompt = _build_system_prompt(session_path, llm_client.model_name)

        # 2. Prepare messages for LLM
        # Start with system prompt, add the received (truncated) history.
        messages_to_send = [{"role": "system", "content": system_prompt}]
        messages_to_send.extend(history) # Add the truncated history

        # Create the final user message including the original prompt and the context string
        final_user_content = user_prompt_dict.get("content", "") + f"\n\n{context_str}"
        messages_to_send.append({"role": "user", "content": final_user_content})

        # 3. Call LLM
        full_response_text = "" # Accumulate the textual response
        turn_llm_error = False # Track error specifically for this LLM call

        # Signal start of assistant response stream
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
                    # Don't modify history here, error status will be sent in 'finished'
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
            # Don't modify history here, error status will be sent in 'finished'

        # --- Finalize Interaction ---
        if turn_llm_error:
            status = "llm_error"
            finish_message = "Interaction ended due to LLM communication error."
            finish_data = {
                "status": status,
                "message": finish_message
            }
        else: # Finished normally
            status = "success"
            finish_message = "Interaction completed."
            # Prepare the assistant response dictionary
            filtered_response_text = _filter_context(full_response_text.strip())
            assistant_response_dict = {"role": "assistant", "content": filtered_response_text}
            finish_data = {
                "status": status,
                "message": finish_message,
                "original_user_prompt": user_prompt_dict, # Send back the original user prompt
                "assistant_response": assistant_response_dict # Send back the assistant response
            }

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

            message = json.loads(line)
            if message.get("type") == "interaction_request":
                handle_interaction_request(message.get("data"))
            else:
                print(f"Worker received unknown message type: {message.get('type')}", file=sys.stderr)

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
