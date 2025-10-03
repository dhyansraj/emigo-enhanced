;;; emigo-visual.el --- Visual enhancements for EmigoE  -*- lexical-binding: t -*-

;; Copyright (C) 2025, EmigoE, all rights reserved.

;; DEBUG: Confirm file is being loaded
(message "[Emigo Visual] Loading emigo-visual.el...")

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
        (dolist (pair json-data)
          (let* ((key (symbol-name (car pair)))
                 (value (cdr pair))
                 (value-str (cond
                            ((stringp value) 
                             ;; Save command value for execute_command
                             (when (and (string= tool-name "execute_command")
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
        ;; If this is execute_command, prepend the command being executed
        (when (and (string= tool-name "execute_command") command-value)
          (push (concat
                 (propertize emigo-tool-call-box-char 'face 'emigo-tool-call-border)
                 "  "
                 (propertize "$ " 'face 'emigo-tool-call-border)
                 (propertize command-value 'face '(:foreground "cyan" :weight bold)))
                formatted-lines))
        (mapconcat 'identity (nreverse formatted-lines) "\n"))
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

(defun emigo-visual--flush-buffer-advice (orig-fun session-path content &optional role tool-id tool-name)
  "Advice for emigo--flush-buffer to add visual enhancements.
Intercepts tool calls to apply fancy formatting instead of plain text."
  ;; For tool calls, intercept and replace with fancy formatting
  (if (and (member role '("tool_json" "tool_json_args" "tool_json_end"))
           (not (string= tool-name "attempt_completion")))
      ;; Handle tool calls with fancy formatting
      (cond
       ((equal role "tool_json")
        (setq emigo--tool-json-block content)
        (let ((buffer (get-buffer (format "*emigo:%s*" session-path))))
          (when buffer
            (with-current-buffer buffer
              (save-excursion
                (let ((inhibit-read-only t))
                  (goto-char (point-max))
                  (when (search-backward-regexp (concat "^" (regexp-quote emigo-prompt-symbol)) nil t)
                    (forward-line -2)
                    (goto-char (line-end-position)))
                  (insert (emigo-visual--format-tool-call-header (or tool-name "(unknown)")))))))))
       
       ((equal role "tool_json_args")
        (setq emigo--tool-json-block (concat emigo--tool-json-block content)))
       
       ((equal role "tool_json_end")
        (let ((buffer (get-buffer (format "*emigo:%s*" session-path))))
          (when buffer
            (with-current-buffer buffer
              (save-excursion
                (let ((inhibit-read-only t))
                  (goto-char (point-max))
                  (when (search-backward-regexp (concat "^" (regexp-quote emigo-prompt-symbol)) nil t)
                    (forward-line -2)
                    (goto-char (line-end-position)))
                  (insert (emigo-visual--format-json-args emigo--tool-json-block tool-name))
                  (insert "\n")
                  (insert (emigo-visual--format-tool-call-footer))
                  (setq emigo--tool-json-block ""))))))
        (setq emigo--tool-json-block "")))
    ;; For all other roles (user, llm, etc.) OR attempt_completion, call original
    (funcall orig-fun session-path content role tool-id tool-name)))

(defun emigo-visual--signal-completion-advice (orig-fun session-path result-text command-string)
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
    ;; Still call original to handle message and command prompt
    (message "[Emigo] Task completed by agent for session: %s" session-path)
    (when (and command-string (not (string-empty-p command-string)))
      (if (y-or-n-p (format "Run demonstration command? `%s`" command-string))
          (emigo--execute-command-sync session-path command-string)))))

;; Function to apply/reapply advice
(defun emigo-visual--apply-advice ()
  "Apply visual enhancements advice to emigo functions."
  (advice-add 'emigo--flush-buffer :around #'emigo-visual--flush-buffer-advice)
  (advice-add 'emigo--signal-completion :around #'emigo-visual--signal-completion-advice)
  (message "[Emigo Visual] Advice applied to emigo--flush-buffer and emigo--signal-completion"))

(defun emigo-visual--remove-advice ()
  "Remove visual enhancements advice from emigo functions."
  (interactive)
  (advice-remove 'emigo--flush-buffer #'emigo-visual--flush-buffer-advice)
  (advice-remove 'emigo--signal-completion #'emigo-visual--signal-completion-advice)
  (message "[Emigo Visual] Advice removed"))

;; Apply advice after emigo is loaded
(with-eval-after-load 'emigo
  (emigo-visual--apply-advice))

;; Also apply on emigo-mode-hook to ensure it's there after reload
(add-hook 'emigo-mode-hook #'emigo-visual--apply-advice)

(provide 'emigo-visual)
;;; emigo-visual.el ends here
