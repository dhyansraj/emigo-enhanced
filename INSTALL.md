# Installation Guide for Emigo Enhanced

## Standard Installation (For End Users)

### 1. Clone the Repository

```bash
cd ~/.emacs.d/
git clone https://github.com/YOUR-USERNAME/emigo-enhanced.git
```

### 2. Install Python Dependencies

```bash
cd ~/.emacs.d/emigo-enhanced
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure Emacs

Add to your Emacs configuration (e.g., `~/.emacs.d/init.el` or `~/.config/emacs/init.el`):

```elisp
(use-package emigo
  :load-path "~/.emacs.d/emigo-enhanced"
  :commands (emigo)
  :init
  ;; Point to the Python venv
  (setq emigo-python-command "~/.emacs.d/emigo-enhanced/.venv/bin/python3")
  
  :config
  (emigo-enable) ;; Start the background process
  
  :custom
  ;; Choose your LLM provider (pick ONE):
  
  ;; Option 1: OpenAI (recommended for beginners)
  (emigo-model "gpt-4o")
  (emigo-api-key (getenv "OPENAI_API_KEY"))
  
  ;; Option 2: Anthropic Claude
  ;; (emigo-model "claude-3-5-sonnet-20241022")
  ;; (emigo-api-key (getenv "ANTHROPIC_API_KEY"))
  
  ;; Option 3: Azure OpenAI (enterprise)
  ;; (emigo-model "azure/your-deployment-name")
  ;; (emigo-base-url "https://your-resource.openai.azure.com/")
  ;; (emigo-api-key (getenv "AZURE_OPENAI_API_KEY"))
  
  ;; Optional: Auto-approve command execution (default: nil)
  (emigo-auto-approve-commands nil)
  
  :bind
  ("C-c e" . emigo))
```

### 4. Set Your API Key

Add to your shell profile (`~/.bashrc`, `~/.zshrc`, etc.):

```bash
# For OpenAI
export OPENAI_API_KEY="sk-your-key-here"

# OR for Anthropic
export ANTHROPIC_API_KEY="sk-ant-your-key-here"
```

Reload your shell or restart Emacs.

### 5. Start Using Emigo

- Start: `M-x emigo` or `C-c e`
- Send prompts: `C-c C-c` or `RET`
- Open transient menu: `C-c C-t`
- Get help: `?` (in the transient menu)

---

## Development Setup (For Contributors)

If you're developing emigo-enhanced, add this hot-reload function:

```elisp
(defun emigo-dev-reload ()
  "Reload emigo from local dev clone without restarting Emacs."
  (interactive)
  (let ((emigo-dev-path "~/workspace/github/emigo-enhanced"))
    (when (and (boundp 'emigo-epc-process) 
               (emigo-epc-live-p emigo-epc-process))
      (emigo-kill-process))
    ;; Unload all modules
    (unload-feature 'emigo-visual t)
    (unload-feature 'emigo-transient t)
    (unload-feature 'emigo-window t)
    (unload-feature 'emigo t)
    ;; Reload
    (add-to-list 'load-path emigo-dev-path)
    (require 'emigo)
    (emigo-enable)))
```

---

## Features Included

✅ **Visual Enhancements**
- Claude Code-style tool call display
- Command preview with `$ command` syntax
- Animated spinner during responses
- Syntax-highlighted JSON arguments

✅ **Error Handling**
- Clear error messages with fix suggestions
- Command failures handled gracefully
- Authentication/API errors displayed prominently

✅ **Transient Menu**
- Interactive menu with `C-c C-t`
- Toggle settings on the fly
- Window management
- Help system

✅ **Auto-Approve Commands**
- Optional auto-execution of commands
- Toggle in transient menu

---

## Troubleshooting

### "No API key found"
Set your API key environment variable and restart Emacs.

### "Python process not starting"
Check that `emigo-python-command` points to the correct Python with dependencies installed.

### "Authentication failed"
Verify your API key is correct and has credits/access.

### Visual enhancements not showing
Make sure `emigo-visual.el` is loaded. It should load automatically via `emigo.el`.

---

## Differences from Upstream Emigo

This fork adds:
- Visual feedback improvements
- Better error handling
- Transient menu interface
- Auto-approve commands feature
- Response indicator
- Enhanced tool call display

All features are backward compatible with upstream emigo.
