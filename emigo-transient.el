;;; emigo-transient.el --- Transient menu interface for EmigoE  -*- lexical-binding: t -*-

;; Copyright (C) 2025, EmigoE, all rights reserved.

;; Author: dhyansraj
;; Keywords: transient menu
;; Package-Requires: ((emacs "26.1") (transient "0.3.0"))

;;; Commentary:

;; Magit-style transient menu interface for EmigoE providing:
;; - Quick access to common operations
;; - File context management
;; - Session management
;; - Window configuration
;; - Settings and model selection

;;; Code:

(require 'transient)

;; Forward declarations for variables from other modules
(defvar emigo-window)
(defvar emigo-buffer)
(defvar emigo-use-side-window)
(defvar emigo-window-width)
(defvar emigo-auto-create-sessions)
(defvar emigo-prevent-window-split)

;; Forward declarations for functions
(declare-function emigo-add-file-to-context "emigo")
(declare-function emigo-drop-file-from-context "emigo")
(declare-function emigo-ls-files-in-context "emigo")
(declare-function emigo-clear-history "emigo")
(declare-function emigo-show-history "emigo")
(declare-function emigo-restart-process "emigo")
(declare-function emigo-toggle-window-mode "emigo-window")
(declare-function emigo-switch-to-current-project "emigo-window")
(declare-function emigo--is-emigo-buffer-p "emigo-window")

;;; Helper Functions

(defun emigo-transient--get-window-mode ()
  "Get current window mode as a string."
  (if emigo-use-side-window "side-window" "main-buffer"))

(defun emigo-transient--get-window-width ()
  "Get current window width as a string."
  (format "%d" emigo-window-width))

(defun emigo-transient--get-auto-create-status ()
  "Get auto-create sessions status."
  (if emigo-auto-create-sessions "enabled" "disabled"))

(defun emigo-transient--get-prevent-split-status ()
  "Get prevent window split status."
  (if emigo-prevent-window-split "enabled" "disabled"))

;;; Window Width Adjustment

(defun emigo-transient-increase-width ()
  "Increase emigo window width by 10."
  (interactive)
  (setq emigo-window-width (+ emigo-window-width 10))
  (message "[Emigo] Window width: %d" emigo-window-width))

(defun emigo-transient-decrease-width ()
  "Decrease emigo window width by 10."
  (interactive)
  (setq emigo-window-width (max 20 (- emigo-window-width 10)))
  (message "[Emigo] Window width: %d" emigo-window-width))

(defun emigo-transient-set-width ()
  "Set emigo window width interactively."
  (interactive)
  (let ((width (read-number "Window width: " emigo-window-width)))
    (setq emigo-window-width (max 20 width))
    (message "[Emigo] Window width set to: %d" emigo-window-width)))

;;; Settings Toggle Functions

(defun emigo-transient-toggle-auto-create ()
  "Toggle auto-create sessions setting."
  (interactive)
  (setq emigo-auto-create-sessions (not emigo-auto-create-sessions))
  (message "[Emigo] Auto-create sessions: %s" 
           (if emigo-auto-create-sessions "enabled" "disabled")))

(defun emigo-transient-toggle-prevent-split ()
  "Toggle prevent window split setting."
  (interactive)
  (setq emigo-prevent-window-split (not emigo-prevent-window-split))
  (message "[Emigo] Prevent window split: %s"
           (if emigo-prevent-window-split "enabled" "disabled")))

;;; Main Transient Menu

;;;###autoload (autoload 'emigo-transient-menu "emigo-transient" nil t)
(transient-define-prefix emigo-transient-menu ()
  "Main transient menu for EmigoE."
  [:description
   (lambda ()
     (concat
      (propertize "EmigoE Menu" 'face 'transient-heading)
      (propertize (format "  [Mode: %s]" (emigo-transient--get-window-mode))
                  'face 'transient-value)))
   ["Files"
    ("f" "Add file to context" emigo-add-file-to-context)
    ("j" "Remove file from context" emigo-drop-file-from-context)
    ("l" "List files in context" emigo-ls-files-in-context)]
   ["Session"
    ("c" "Clear history" emigo-clear-history)
    ("h" "Show history" emigo-show-history)
    ("r" "Restart process" emigo-restart-process)
    ("s" "Switch to project" emigo-switch-to-current-project)]
   ["Window"
    ("w" "Toggle window mode" emigo-toggle-window-mode :transient t)
    ("+" "Increase width" emigo-transient-increase-width :transient t)
    ("-" "Decrease width" emigo-transient-decrease-width :transient t)
    ("=" "Set width" emigo-transient-set-width :transient t)]
   ["Settings"
    :description
    (lambda ()
      (format "Settings  [Auto-create: %s | Prevent-split: %s]"
              (propertize (emigo-transient--get-auto-create-status) 'face 'transient-value)
              (propertize (emigo-transient--get-prevent-split-status) 'face 'transient-value)))
    ("a" "Toggle auto-create sessions" emigo-transient-toggle-auto-create :transient t)
    ("p" "Toggle prevent split" emigo-transient-toggle-prevent-split :transient t)]
   ["Actions"
    ("q" "Quit" transient-quit-one)
    ("?" "Help" emigo-transient-help)]])

;;; Help Function

(defun emigo-transient-help ()
  "Show help for EmigoE transient menu."
  (interactive)
  (with-help-window "*EmigoE Help*"
    (princ "EmigoE Transient Menu Help\n")
    (princ "==========================\n\n")
    (princ "Files:\n")
    (princ "  f - Add file to context: Add a file to the current session's context\n")
    (princ "  j - Remove file: Remove a file from the current session's context\n")
    (princ "  l - List files: Show all files in the current session's context\n\n")
    (princ "Session:\n")
    (princ "  c - Clear history: Clear the chat history for the current session\n")
    (princ "  h - Show history: Display the full chat history in an org buffer\n")
    (princ "  r - Restart process: Restart the Emigo Python process\n")
    (princ "  s - Switch to project: Switch to the session for the current buffer's project\n\n")
    (princ "Window:\n")
    (princ "  w - Toggle window mode: Switch between side-window and main-buffer modes\n")
    (princ "  + - Increase width: Increase window width by 10 columns\n")
    (princ "  - - Decrease width: Decrease window width by 10 columns\n")
    (princ "  = - Set width: Set window width to a specific value\n\n")
    (princ "Settings:\n")
    (princ "  a - Toggle auto-create: Enable/disable automatic session creation\n")
    (princ "  p - Toggle prevent split: Enable/disable window split prevention\n\n")
    (princ "Actions:\n")
    (princ "  q - Quit: Close the transient menu\n")
    (princ "  ? - Help: Show this help buffer\n\n")
    (princ "Note: Actions marked with :transient keep the menu open after execution.\n")))

;;; Keybinding Setup

;;;###autoload
(defun emigo-transient-setup-keys ()
  "Setup keybindings for emigo transient menu.
This should be called after emigo-mode is loaded."
  (with-eval-after-load 'emigo
    (when (boundp 'emigo-mode-map)
      (define-key emigo-mode-map (kbd "C-c e ?") #'emigo-transient-menu)
      (define-key emigo-mode-map (kbd "C-c e m") #'emigo-transient-menu))))

;; Auto-setup keybindings when loaded
(emigo-transient-setup-keys)

(provide 'emigo-transient)
;;; emigo-transient.el ends here
