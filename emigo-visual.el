;;; emigo-visual.el --- Visual enhancements for EmigoE  -*- lexical-binding: t -*-

;; Copyright (C) 2025, EmigoE, all rights reserved.


;; Author: dhyansraj
;; Keywords: visual feedback ui
;; Package-Requires: ((emacs "26.1"))

;;; Commentary:

;; Visual enhancements for EmigoE including:
;; - Claude Code-style tool call display
;; - Streaming indicators
;; - Status display
;; - Progress feedback

;;; Code:

;;; Custom Faces

(defface emigo-tool-call-header
  '((t :inherit font-lock-function-name-face :weight bold))
  "Face for tool call headers."
  :group 'emigo)

(defface emigo-tool-call-border
  '((t :inherit font-lock-comment-face))
  "Face for tool call borders."
  :group 'emigo)

(defface emigo-tool-call-args
  '((t :inherit font-lock-variable-name-face))
  "Face for tool call argument names."
  :group 'emigo)

(defface emigo-tool-call-values
  '((t :inherit font-lock-string-face))
  "Face for tool call argument values."
  :group 'emigo)

(defface emigo-thinking-indicator
  '((t :inherit font-lock-comment-face :slant italic))
  "Face for thinking indicator."
  :group 'emigo)

(defface emigo-status-info
  '((t :inherit font-lock-constant-face))
  "Face for status information."
  :group 'emigo)

;;; Customization

(defcustom emigo-use-fancy-tool-display t
  "Whether to use Claude Code-style tool call display.
When non-nil, tool calls are displayed with nice formatting and colors.
When nil, uses simple text format."
  :type 'boolean
  :group 'emigo)

(defcustom emigo-tool-call-box-char "│"
  "Character to use for tool call box borders."
  :type 'string
  :group 'emigo)

(defcustom emigo-show-thinking-indicator t
  "Whether to show a thinking indicator when AI is processing."
  :type 'boolean
  :group 'emigo)

;;; Tool Call Display

(defun emigo-visual--display-tool-param (session-path tool-name param-key placeholder-text prefix-text param-face)
  "Display a tool parameter value in the buffer.
SESSION-PATH: The session path to find the buffer.
TOOL-NAME: The name of the tool being called.
PARAM-KEY: The JSON key to extract (e.g., 'command', 'path').
PLACEHOLDER-TEXT: Text to show initially (e.g., 'Executing command...').
PREFIX-TEXT: Prefix to show before the value (e.g., '$ ', '📄 ').
PARAM-FACE: Face properties for the parameter value."
  (when (and (string-match-p (format "\"%s\"" param-key) emigo--tool-json-block)
             (string-suffix-p "}" emigo--tool-json-block)) ;; Wait for complete JSON
    (condition-case nil
        (let* ((json-data (json-parse-string emigo--tool-json-block :object-type 'alist))
               (param-value (alist-get (intern param-key) json-data)))
          (when param-value
            (let ((buffer (get-buffer (format "*emigo:%s*" session-path))))
              (when buffer
                (with-current-buffer buffer
                  (save-excursion
                    (let ((inhibit-read-only t))
                      (goto-char (point-max))
                      (when (search-backward placeholder-text nil t)
                        (beginning-of-line)
                        (kill-line)
                        (insert (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border))
                        (insert "  ")
                        (insert (propertize prefix-text 'face 'emigo-tool-call-border))
                        (insert (propertize param-value 'face param-face))))))))))
      (error nil))))

(defun emigo-visual--format-tool-call-header (tool-name)
  "Format a Claude Code-style header for TOOL-NAME."
  (concat
   "\n"
   (propertize "┌─ " 'face 'emigo-tool-call-border)
   (propertize (format "Tool Call: %s" tool-name) 'face 'emigo-tool-call-header)
   (propertize " " 'face 'emigo-tool-call-border)
   "\n"))

(defun emigo-visual--format-tool-call-footer ()
  "Format a Claude Code-style footer for tool calls."
  (concat
   (propertize "└" 'face 'emigo-tool-call-border)
   (propertize (make-string 50 ?─) 'face 'emigo-tool-call-border)
   "\n"))

(defun emigo-visual--format-json-args (json-string &optional tool-name)
  "Format JSON-STRING with syntax highlighting for tool arguments.
TOOL-NAME is used to show the command for execute_command tools.
Returns a formatted string with proper indentation and colors."
  (condition-case nil
      (let* ((json-data (json-parse-string json-string :object-type 'alist))
             (formatted-lines '())
             (command-value nil))
        ;; First pass: build formatted lines and extract command if present
        (dolist (pair json-data)
          (let* ((key (symbol-name (car pair)))
                 (value (cdr pair))
                 (value-str (cond
                            ((stringp value) 
                             ;; Save command value for execute_command
                             (when (and tool-name 
                                       (string= tool-name "execute_command")
                                       (string= key "command"))
                               (setq command-value value))
                             (format "\"%s\"" value))
                            ((numberp value) (number-to-string value))
                            ((eq value t) "true")
                            ((eq value :false) "false")
                            ((null value) "null")
                            (t (format "%S" value))))
                 (line (concat
                       (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border)
                       "  "
                       (propertize key 'face 'emigo-tool-call-args)
                       (propertize ": " 'face 'emigo-tool-call-border)
                       (propertize value-str 'face 'emigo-tool-call-values))))
            (push line formatted-lines)))
        
        ;; Reverse to get correct order
        (setq formatted-lines (nreverse formatted-lines))
        
        ;; If this is execute_command and we found a command, prepend it at the TOP
        (when (and tool-name (string= tool-name "execute_command") command-value)
          (setq formatted-lines
                (cons (concat
                       (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border)
                       "  "
                       (propertize "$ " 'face 'emigo-tool-call-border)
                       (propertize command-value 'face '(:foreground "cyan" :weight bold)))
                      formatted-lines)))
        
        (mapconcat 'identity formatted-lines "\n"))
    (error
     ;; If JSON parsing fails, return the raw string with basic formatting
     (concat
      (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border)
      "  "
      (propertize json-string 'face 'emigo-tool-call-values)))))

(defun emigo-visual--insert-tool-call (tool-name json-args)
  "Insert a formatted tool call display for TOOL-NAME with JSON-ARGS."
  (if emigo-use-fancy-tool-display
      (progn
        ;; Insert header
        (insert (emigo-visual--format-tool-call-header tool-name))
        ;; Insert formatted arguments
        (insert (emigo-visual--format-json-args json-args))
        (insert "\n")
        ;; Insert footer
        (insert (emigo-visual--format-tool-call-footer)))
    ;; Simple format fallback
    (insert (propertize (format "\n--- Tool Call: %s ---\n" tool-name) 'face 'font-lock-comment-face))
    (insert json-args)
    (insert (propertize "\n--- End Tool Call ---\n" 'face 'font-lock-comment-face))))

;;; Thinking Indicator

(defvar-local emigo-visual--thinking-timer nil
  "Timer for animating the thinking indicator.")

(defvar-local emigo-visual--thinking-marker nil
  "Marker for the thinking indicator position.")

(defvar-local emigo-visual--thinking-dots 0
  "Current number of dots in thinking indicator.")

(defun emigo-visual--update-thinking-indicator ()
  "Update the thinking indicator animation."
  (when (and emigo-visual--thinking-marker
             (marker-buffer emigo-visual--thinking-marker))
    (with-current-buffer (marker-buffer emigo-visual--thinking-marker)
      (save-excursion
        (let ((inhibit-read-only t))
          (goto-char emigo-visual--thinking-marker)
          ;; Clear previous indicator
          (when (looking-at ".*\n")
            (delete-region (point) (line-end-position)))
          ;; Insert new indicator
          (setq emigo-visual--thinking-dots (1+ (mod emigo-visual--thinking-dots 4)))
          (insert (propertize
                   (format "Thinking%s" (make-string emigo-visual--thinking-dots ?.))
                   'face 'emigo-thinking-indicator)))))))

(defun emigo-visual-start-thinking-indicator ()
  "Start the thinking indicator animation."
  (interactive)
  (when emigo-show-thinking-indicator
    (setq emigo-visual--thinking-dots 0)
    (setq emigo-visual--thinking-marker (point-marker))
    (insert "\n")
    (emigo-visual--update-thinking-indicator)
    (setq emigo-visual--thinking-timer
          (run-with-timer 0.5 0.5 #'emigo-visual--update-thinking-indicator))))

(defun emigo-visual-stop-thinking-indicator ()
  "Stop and remove the thinking indicator."
  (interactive)
  (when emigo-visual--thinking-timer
    (cancel-timer emigo-visual--thinking-timer)
    (setq emigo-visual--thinking-timer nil))
  (when (and emigo-visual--thinking-marker
             (marker-buffer emigo-visual--thinking-marker))
    (with-current-buffer (marker-buffer emigo-visual--thinking-marker)
      (save-excursion
        (let ((inhibit-read-only t))
          (goto-char emigo-visual--thinking-marker)
          (when (looking-at ".*\n")
            (delete-region (point) (1+ (line-end-position)))))))
    (setq emigo-visual--thinking-marker nil)))

;;; Status Display

(defun emigo-visual--format-status (turn-info token-info model-info)
  "Format status information for display.
TURN-INFO: string like \"Turn 2/10\"
TOKEN-INFO: string like \"1234 tokens\"
MODEL-INFO: string like \"gpt-4\""
  (concat
   (when turn-info
     (propertize (format "[%s] " turn-info) 'face 'emigo-status-info))
   (when token-info
     (propertize (format "[%s] " token-info) 'face 'emigo-status-info))
   (when model-info
     (propertize (format "[%s]" model-info) 'face 'emigo-status-info))))

(defun emigo-visual-update-status (turn-info token-info model-info)
  "Update the status display with TURN-INFO, TOKEN-INFO, and MODEL-INFO."
  ;; This will be integrated with the header line or mode line
  (message "%s" (emigo-visual--format-status turn-info token-info model-info)))

;;; Integration Functions

;; Forward declarations
(defvar emigo--tool-json-block)
(defvar emigo-prompt-symbol)
(declare-function emigo--execute-command-sync "emigo")

;; Track the current tool name across calls
(defvar-local emigo--current-tool-name nil
  "The name of the tool currently being called.")

(defun emigo-visual--flush-buffer-advice (orig-fun session-path content &optional role tool-id tool-name)
  "Advice for emigo--flush-buffer to add visual enhancements.
Intercepts tool calls to apply fancy formatting instead of plain text."
  ;; Save tool-name when we get it (in tool_json), use it for subsequent calls
  (when (and (equal role "tool_json") tool-name)
    (setq emigo--current-tool-name tool-name))
  
  ;; Use saved tool-name if current one is nil
  (let ((effective-tool-name (or tool-name emigo--current-tool-name)))
    ;; For tool calls, intercept and replace with fancy formatting
    ;; ALWAYS intercept attempt_completion to prevent JSON display
    (if (member role '("tool_json" "tool_json_args" "tool_json_end"))
        ;; Handle tool calls with fancy formatting
        (cond
         ((equal role "tool_json")
          (setq emigo--tool-json-block content)
          ;; Skip display for attempt_completion
          (if (string= effective-tool-name "attempt_completion")
              nil
            (let ((buffer (get-buffer (format "*emigo:%s*" session-path))))
            (when buffer
              (with-current-buffer buffer
                (save-excursion
                  (let ((inhibit-read-only t))
                    (goto-char (point-max))
                    (when (search-backward-regexp (concat "^" (regexp-quote emigo-prompt-symbol)) nil t)
                      (forward-line -2)
                      (goto-char (line-end-position)))
                    ;; Insert header
                    (insert (emigo-visual--format-tool-call-header (or effective-tool-name "(unknown)")))
                    ;; For execute_command, show placeholder
                    (when (string= effective-tool-name "execute_command")
                      (insert (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border))
                      (insert "  ")
                      (insert (propertize "Executing command..." 'face '(:foreground "cyan" :weight bold)))
                      (insert "\n"))
                    ;; For read_file, show placeholder
                    (when (string= effective-tool-name "read_file")
                      (insert (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border))
                      (insert "  ")
                      (insert (propertize "Reading file..." 'face '(:foreground "green" :weight bold)))
                      (insert "\n"))
                    ;; For write_to_file, show placeholder
                    (when (string= effective-tool-name "write_to_file")
                      (insert (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border))
                      (insert "  ")
                      (insert (propertize "Writing file..." 'face '(:foreground "blue" :weight bold)))
                      (insert "\n"))
                    ;; For replace_in_file, show placeholder
                    (when (string= effective-tool-name "replace_in_file")
                      (insert (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border))
                      (insert "  ")
                      (insert (propertize "Editing file..." 'face '(:foreground "yellow" :weight bold)))
                      (insert "\n"))
                    ;; For search_files, show placeholder
                    (when (string= effective-tool-name "search_files")
                      (insert (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border))
                      (insert "  ")
                      (insert (propertize "Searching..." 'face '(:foreground "magenta" :weight bold)))
                      (insert "\n"))
                    ;; For list_repomap, show placeholder
                    (when (string= effective-tool-name "list_repomap")
                      (insert (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border))
                      (insert "  ")
                      (insert (propertize "Analyzing codebase..." 'face '(:foreground "orange" :weight bold)))
                      (insert "\n"))
                    ;; For list_files, show placeholder
                    (when (string= effective-tool-name "list_files")
                      (insert (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border))
                      (insert "  ")
                      (insert (propertize "Listing directory..." 'face '(:foreground "purple" :weight bold)))
                      (insert "\n"))
                    ;; For read_image, show placeholder
                    (when (string= effective-tool-name "read_image")
                      (insert (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border))
                      (insert "  ")
                      (insert (propertize "Analyzing image..." 'face '(:foreground "cyan" :weight bold)))
                      (insert "\n")))))))
            nil))
       
       ((equal role "tool_json_args")
        (setq emigo--tool-json-block (concat emigo--tool-json-block content))
        
        ;; Skip display for attempt_completion
        (if (string= effective-tool-name "attempt_completion")
            nil
          ;; Display parameters for different tools using the utility function
          (cond
         ((string= effective-tool-name "execute_command")
          (emigo-visual--display-tool-param 
           session-path effective-tool-name "command" 
           "Executing command..." "$ " 
           '(:foreground "cyan" :weight bold)))
         
         ((string= effective-tool-name "read_file")
          (emigo-visual--display-tool-param 
           session-path effective-tool-name "path" 
           "Reading file..." "📄 " 
           '(:foreground "green" :weight bold)))
         
         ((string= effective-tool-name "write_to_file")
          (emigo-visual--display-tool-param 
           session-path effective-tool-name "path" 
           "Writing file..." "✏️ " 
           '(:foreground "blue" :weight bold)))
         
         ((string= effective-tool-name "replace_in_file")
          (emigo-visual--display-tool-param 
           session-path effective-tool-name "path" 
           "Editing file..." "🔧 " 
           '(:foreground "yellow" :weight bold)))
         
         ((string= effective-tool-name "search_files")
          (emigo-visual--display-tool-param 
           session-path effective-tool-name "pattern" 
           "Searching..." "🔍 " 
           '(:foreground "magenta" :weight bold)))
         
         ((string= effective-tool-name "list_repomap")
          (emigo-visual--display-tool-param 
           session-path effective-tool-name "path" 
           "Analyzing codebase..." "🗺️ " 
           '(:foreground "orange" :weight bold)))
         
         ((string= effective-tool-name "list_files")
          (emigo-visual--display-tool-param 
           session-path effective-tool-name "path" 
           "Listing directory..." "📁 " 
           '(:foreground "purple" :weight bold)))
         
         ((string= effective-tool-name "read_image")
          (emigo-visual--display-tool-param 
           session-path effective-tool-name "path" 
           "Analyzing image..." "🖼️ " 
           '(:foreground "cyan" :weight bold)))))
        nil)
       
       ((equal role "tool_json_end")
        ;; If we have accumulated attempt_completion JSON, parse and display it
        (when (and emigo--current-tool-name
                   (string= emigo--current-tool-name "attempt_completion")
                   (not (string-empty-p emigo--tool-json-block)))
          (condition-case err
              (let* ((json-data (json-parse-string emigo--tool-json-block :object-type 'alist))
                     (result-from-json (alist-get 'result json-data)))
                (when result-from-json
                  (let ((buffer (get-buffer (format "*emigo:%s*" session-path))))
                    (when buffer
                      (with-current-buffer buffer
                        (let ((inhibit-read-only t))
                          (goto-char (point-max))
                          (when (search-backward-regexp (concat "^" (regexp-quote emigo-prompt-symbol)) nil t)
                            (forward-line -2)
                            (goto-char (line-end-position)))
                          (insert "\n")
                          (insert (propertize result-from-json 'face '(:foreground "white" :weight bold)))
                          (insert "\n")))))))
            (error nil)))
        
        ;; Skip attempt_completion - don't show it
        (unless (string= effective-tool-name "attempt_completion")
          (let ((buffer (get-buffer (format "*emigo:%s*" session-path))))
            (when buffer
              (with-current-buffer buffer
                (save-excursion
                  (let ((inhibit-read-only t))
                    (goto-char (point-max))
                    (when (search-backward-regexp (concat "^" (regexp-quote emigo-prompt-symbol)) nil t)
                      (forward-line -2)
                      (goto-char (line-end-position)))
                    ;; Insert formatted JSON args (pass effective-tool-name for execute_command)
                    (let ((formatted (emigo-visual--format-json-args emigo--tool-json-block effective-tool-name)))
                      (insert formatted))
                    (insert "\n")
                    ;; Insert footer
                    (insert (emigo-visual--format-tool-call-footer))
                    (setq emigo--tool-json-block ""))))))
        ;; Clear the block and tool name even if we skipped display
        (setq emigo--tool-json-block "")
        (setq emigo--current-tool-name nil)
        nil)))
      ;; For all other roles (user, llm, etc.), call original
      (funcall orig-fun session-path content role tool-id tool-name))))

(defun emigo-visual--agent-finished-advice (orig-fun session-path)
  "Advice for emigo--agent-finished to display accumulated attempt_completion result."
  ;; Call original function
  (funcall orig-fun session-path))

(defun emigo-visual--signal-completion-advice (orig-fun session-path result-text)
  "Advice for emigo--signal-completion to style completion text.
Makes completion text bright white and bold without box decorations."
  (let ((buffer (get-buffer (format "*emigo:%s*" session-path))))
    (when buffer
      (with-current-buffer buffer
        (let ((inhibit-read-only t))
          (goto-char (point-max))
          (if (search-backward-regexp (concat "^" (regexp-quote emigo-prompt-symbol)) nil t)
              ;; Insert before the prompt with bright white bold styling
              (progn
                (insert "\n")
                (insert (propertize result-text 'face '(:foreground "white" :weight bold)))
                (insert "\n\n"))
            ;; If no prompt found, insert at end
            (goto-char (point-max))
            (insert "\n")
            (insert (propertize result-text 'face '(:foreground "white" :weight bold)))
            (insert "\n")))))
    ;; Call original to handle message
    (message "[Emigo] Task completed by agent for session: %s" session-path)))

;; Function to apply/reapply advice
(defun emigo-visual--apply-advice ()
  "Apply visual enhancements advice to emigo functions."
  (advice-add 'emigo--flush-buffer :around #'emigo-visual--flush-buffer-advice)
  (advice-add 'emigo--signal-completion :around #'emigo-visual--signal-completion-advice)
  (advice-add 'emigo--agent-finished :around #'emigo-visual--agent-finished-advice))

(defun emigo-visual--remove-advice ()
  "Remove visual enhancements advice from emigo functions."
  (interactive)
  (advice-remove 'emigo--flush-buffer #'emigo-visual--flush-buffer-advice)
  (advice-remove 'emigo--signal-completion #'emigo-visual--signal-completion-advice)
  (advice-remove 'emigo--agent-finished #'emigo-visual--agent-finished-advice)
  (message "[Emigo Visual] Advice removed"))

;; Apply advice after emigo is loaded
(with-eval-after-load 'emigo
  (emigo-visual--apply-advice))

;; Also apply on emigo-mode-hook to ensure it's there after reload
(add-hook 'emigo-mode-hook #'emigo-visual--apply-advice)

(provide 'emigo-visual)
;;; emigo-visual.el ends here
