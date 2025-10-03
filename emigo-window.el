;;; emigo-window.el --- Window management for EmigoE  -*- lexical-binding: t -*-

;; Copyright (C) 2025, EmigoE, all rights reserved.

;; Author: dhyansraj
;; Keywords: window management
;; Package-Requires: ((emacs "26.1"))

;;; Commentary:

;; Enhanced window management for EmigoE including:
;; - Flexible display modes (side-window and main-buffer)
;; - Smart session switching
;; - Multi-session support
;; - Window state management

;;; Code:

;; Forward declarations for variables from emigo.el
(defvar emigo-window)
(defvar emigo-buffer)
(defvar emigo-epc-process)
(defvar emigo-project-buffers)
(defvar emigo-prompt-symbol)

;; Forward declarations for functions from emigo.el
(declare-function emigo-close "emigo")
(declare-function emigo-mode "emigo")
(declare-function emigo-update-header-line "emigo")
(declare-function emigo-start-process "emigo")
(declare-function emigo-epc-live-p "emigo-epc")

(require 'vc-git)

;;; Utility Functions

(defun emigo--is-emigo-buffer-p (&optional buffer)
  "Return non-nil if BUFFER (defaults to current) is an Emigo buffer."
  (with-current-buffer (or buffer (current-buffer))
    (string-match-p "^\\*emigo:.*\\*$" (buffer-name))))

(defun emigo-window-exist-p (window)
  "Return non-nil if WINDOW exists and is live."
  (and window (window-live-p window)))

(defun emigo-buffer-exist-p (buffer)
  "Return non-nil if BUFFER exists and is live."
  (and buffer (buffer-live-p buffer)))

(defun emigo-exist-p ()
  "Return non-nil if both emigo buffer and window exist."
  (and (emigo-buffer-exist-p emigo-buffer)
       (emigo-window-exist-p emigo-window)))

;;; Customization

(defcustom emigo-use-side-window t
  "Whether to display Emigo in a dedicated side window.
When non-nil, Emigo buffers are displayed in a side window on the right.
When nil, Emigo buffers are displayed like normal buffers."
  :type 'boolean
  :group 'emigo)

(defcustom emigo-window-width 50
  "The width of `emigo' dedicated side window.
Only applies when `emigo-use-side-window' is non-nil."
  :type 'integer
  :group 'emigo)

(defcustom emigo-auto-create-sessions t
  "Whether to automatically create new sessions when switching projects.
When non-nil, switching to a file in a new project will automatically
create a new Emigo session for that project. When nil, only switches
between existing sessions."
  :type 'boolean
  :group 'emigo)

(defcustom emigo-prevent-window-split t
  "Whether to prevent window splitting when Emigo side window is visible.
When non-nil and in side-window mode, opening files will reuse the main
window instead of creating splits."
  :type 'boolean
  :group 'emigo)

;;; Variables

(defvar emigo-window-resize-timer nil
  "Timer to periodically enforce the dedicated window width.")

(defvar emigo--last-working-buffer nil
  "The last non-Emigo buffer that was being worked on.
Used to restore the main window when toggling back to side-window mode.")

(defvar emigo--auto-switch-enabled t
  "Whether automatic session switching is enabled.
When non-nil and an Emigo window is visible, automatically switch
to the session matching the current buffer's project.")

;;; Window Width Enforcement

(defun emigo-ensure-window-width ()
  "Restore the saved width of emigo dedicated window.
Only enforces width when `emigo-use-side-window' is non-nil."
  (when (and
         emigo-use-side-window  ;; Only enforce width in side-window mode
         emigo-window-width
         (emigo-exist-p)
         (window-live-p emigo-window)
         (not (= (window-width emigo-window) emigo-window-width)))
    (window-resize emigo-window
                   (- emigo-window-width (window-width emigo-window))
                   t)))

(defun emigo--start-dedicated-window-timer ()
  "Start the timer to enforce the dedicated window width."
  (emigo--cancel-dedicated-window-timer) ;; Cancel existing timer first
  (setq emigo-window-resize-timer
        (run-with-timer 1 1 #'emigo-ensure-window-width))) ;; Check every 1 second

(defun emigo--cancel-dedicated-window-timer ()
  "Cancel the timer that enforces the dedicated window width."
  (when (timerp emigo-window-resize-timer)
    (cancel-timer emigo-window-resize-timer))
  (setq emigo-window-resize-timer nil))

;;; Window Display Functions

(defun emigo-pop-window ()
  "Pop emigo window according to `emigo-use-side-window' setting."
  (if emigo-use-side-window
      ;; Side window mode
      (progn
        (setq emigo-window (display-buffer (current-buffer) 
                                           `(display-buffer-in-side-window 
                                             (side . right) 
                                             (window-width . ,emigo-window-width))))
        (select-window emigo-window)
        (set-window-buffer emigo-window emigo-buffer)
        (set-window-dedicated-p (selected-window) t)
        ;; Start the enforcement timer
        (emigo--start-dedicated-window-timer))
    ;; Main buffer mode - display like a normal buffer
    (progn
      (setq emigo-window (display-buffer (current-buffer)))
      (select-window emigo-window)
      (set-window-buffer emigo-window emigo-buffer))))

(defun emigo-create-window (buffer)
  "Display BUFFER in the Emigo window according to `emigo-use-side-window' setting."
  (unless (bufferp buffer)
    (error "[Emigo] Invalid buffer provided to emigo-create-window: %s" buffer))
  (setq emigo-buffer buffer)
  (unless (emigo-window-exist-p emigo-window)
    (setq emigo-window
          (if emigo-use-side-window
              ;; Side window mode
              (display-buffer buffer
                              `(display-buffer-in-side-window
                                (side . right)
                                (window-width . ,emigo-window-width)))
            ;; Main buffer mode
            (display-buffer buffer))))
  (select-window emigo-window)
  (set-window-buffer emigo-window emigo-buffer)
  ;; Only set dedicated in side-window mode
  (when emigo-use-side-window
    (set-window-dedicated-p (selected-window) t)
    ;; Start the enforcement timer
    (emigo--start-dedicated-window-timer)))

;;; Window Mode Toggle

(defun emigo--track-working-buffer ()
  "Track the current buffer if it's not an Emigo buffer.
This helps restore the correct buffer when toggling window modes."
  (when (and (not (emigo--is-emigo-buffer-p))
             (not (minibufferp))
             (buffer-file-name))  ;; Only track file buffers
    (setq emigo--last-working-buffer (current-buffer))))

;; Track working buffer when switching buffers
(add-hook 'buffer-list-update-hook #'emigo--track-working-buffer)

(defun emigo-toggle-window-mode ()
  "Toggle between side-window and main-buffer display modes.
When switching from side-window to main-buffer mode, the window
becomes a regular buffer. When switching to side-window mode,
it becomes a dedicated side window and restores the last working buffer."
  (interactive)
  (let ((was-visible (emigo-window-exist-p emigo-window))
        (current-buffer emigo-buffer)
        (other-window nil))
    ;; Toggle the setting
    (setq emigo-use-side-window (not emigo-use-side-window))
    
    (when was-visible
      ;; If switching to side-window mode and Emigo is currently the only/selected window,
      ;; we need to create another window first to avoid "sole ordinary window" error
      (when (and emigo-use-side-window
                 (or (= (length (window-list)) 1)  ;; Only one window
                     (eq (selected-window) emigo-window)))  ;; Or emigo is selected
        ;; Find a non-emigo buffer to display - prefer last working buffer
        (let ((target-buffer (or (and emigo--last-working-buffer
                                      (buffer-live-p emigo--last-working-buffer)
                                      emigo--last-working-buffer)
                                 ;; Find a file buffer
                                 (catch 'found
                                   (dolist (buf (buffer-list))
                                     (when (and (buffer-file-name buf)
                                               (not (emigo--is-emigo-buffer-p buf)))
                                       (throw 'found buf))))
                                 ;; Fallback to other-buffer or scratch
                                 (other-buffer emigo-buffer t)
                                 (get-buffer "*scratch*"))))
          (when target-buffer
            ;; Split window and show another buffer
            (select-window emigo-window)
            (split-window-right)
            (other-window 1)
            (switch-to-buffer target-buffer)
            (setq other-window (selected-window)))))
      
      ;; Now close the emigo window
      (when (window-live-p emigo-window)
        (emigo-close))
      
      ;; Reopen in the new mode
      (when current-buffer
        (setq emigo-buffer current-buffer)
        (emigo-pop-window))
      
      ;; If we created a helper window and we're now in side-window mode,
      ;; select the other window
      (when (and other-window (window-live-p other-window))
        (select-window other-window)))
    
    (message "[Emigo] Window mode: %s" 
             (if emigo-use-side-window "side-window" "main-buffer"))))

;;; Smart Session Switching

(defun emigo-find-session-for-path (path)
  "Find the Emigo buffer/session that matches PATH.
Returns the buffer name if found, nil otherwise."
  (let* ((target-session-path (file-truename (or (vc-git-root path)
                                                  (when (and (featurep 'projectile)
                                                            (projectile-project-p))
                                                    (projectile-project-root))
                                                  path)))
         (target-buffer-name (format "*emigo:%s*" target-session-path)))
    (when (get-buffer target-buffer-name)
      target-buffer-name)))

(defun emigo-switch-to-current-project (&optional auto-create)
  "Switch the Emigo window to the session for the current buffer's project.
If AUTO-CREATE is non-nil (or `emigo-auto-create-sessions' is non-nil),
creates a new session if one doesn't exist for this project."
  (interactive)
  (let* ((current-path (or buffer-file-name default-directory))
         (session-buffer (emigo-find-session-for-path current-path)))
    (if session-buffer
        (progn
          ;; Switch to existing session
          (setq emigo-buffer (get-buffer session-buffer))
          (when (emigo-window-exist-p emigo-window)
            ;; Temporarily un-dedicate window to allow buffer change
            (set-window-dedicated-p emigo-window nil)
            (set-window-buffer emigo-window emigo-buffer)
            ;; Re-dedicate if in side-window mode
            (when emigo-use-side-window
              (set-window-dedicated-p emigo-window t)))
          (message "[Emigo] Switched to session: %s" session-buffer))
      ;; No session exists
      (if (or auto-create emigo-auto-create-sessions)
          (progn
            ;; Create new session
            (let* ((session-path (file-truename (or (vc-git-root current-path)
                                                     (when (and (featurep 'projectile)
                                                               (projectile-project-p))
                                                       (projectile-project-root))
                                                     current-path)))
                   (buffer-name (format "*emigo:%s*" session-path))
                   (buffer (get-buffer-create buffer-name)))
              ;; Ensure EPC process is running
              (unless (emigo-epc-live-p emigo-epc-process)
                (emigo-start-process))
              
              ;; Set buffer-local session path
              (with-current-buffer buffer
                (emigo-mode)
                (emigo-update-header-line session-path)
                (setq-local emigo-session-path session-path)
                ;; Insert initial prompt
                (let ((inhibit-read-only t))
                  (erase-buffer)
                  (insert (propertize (concat "\n\n" emigo-prompt-symbol) 'face font-lock-keyword-face))))
              
              ;; Add buffer to tracked list
              (add-to-list 'emigo-project-buffers buffer t)
              
              ;; Switch to the new session
              (setq emigo-buffer buffer)
              (when (emigo-window-exist-p emigo-window)
                ;; Temporarily un-dedicate window to allow buffer change
                (set-window-dedicated-p emigo-window nil)
                (set-window-buffer emigo-window emigo-buffer)
                ;; Re-dedicate if in side-window mode
                (when emigo-use-side-window
                  (set-window-dedicated-p emigo-window t)))
              
              (message "[Emigo] Created new session: %s" buffer-name)))
        ;; Don't auto-create, just inform user
        (message "[Emigo] No session found for current project. Use `emigo' to create one.")))))

(defun emigo--auto-switch-session ()
  "Automatically switch to the appropriate session if conditions are met.
Only switches when:
- Auto-switching is enabled
- An Emigo window is visible
- Current buffer is not an Emigo buffer
- A matching session exists (or auto-create is enabled)"
  (when (and emigo--auto-switch-enabled
             (emigo-window-exist-p emigo-window)
             (not (emigo--is-emigo-buffer-p)))
    (let* ((current-path (or buffer-file-name default-directory))
           (session-buffer (emigo-find-session-for-path current-path)))
      (if session-buffer
          ;; Session exists, switch to it if different
          (when (not (eq (get-buffer session-buffer) emigo-buffer))
            (setq emigo-buffer (get-buffer session-buffer))
            ;; Temporarily un-dedicate window to allow buffer change
            (set-window-dedicated-p emigo-window nil)
            (set-window-buffer emigo-window emigo-buffer)
            ;; Re-dedicate if in side-window mode
            (when emigo-use-side-window
              (set-window-dedicated-p emigo-window t)))
        ;; No session exists, create one if auto-create is enabled
        (when emigo-auto-create-sessions
          (emigo-switch-to-current-project t))))))

(defun emigo-enable-auto-switch ()
  "Enable automatic session switching when changing buffers."
  (interactive)
  (setq emigo--auto-switch-enabled t)
  (add-hook 'buffer-list-update-hook #'emigo--auto-switch-session)
  (message "[Emigo] Auto-switch enabled"))

(defun emigo-disable-auto-switch ()
  "Disable automatic session switching."
  (interactive)
  (setq emigo--auto-switch-enabled nil)
  (remove-hook 'buffer-list-update-hook #'emigo--auto-switch-session)
  (message "[Emigo] Auto-switch disabled"))

;; Enable auto-switch by default
(add-hook 'emigo-mode-hook #'emigo-enable-auto-switch)

;;; Display Buffer Configuration

(defun emigo--switch-to-buffer-advice (orig-fun buffer-or-name &optional norecord force-same-window)
  "Advice for `switch-to-buffer' to use main window when Emigo is visible.
Prevents splitting by reusing the main window when in side-window mode."
  (if (and emigo-prevent-window-split
           emigo-use-side-window
           (emigo-window-exist-p emigo-window)
           (not (eq (selected-window) emigo-window))
           (not (string-match-p "^\\*emigo:.*\\*$" (if (bufferp buffer-or-name)
                                                        (buffer-name buffer-or-name)
                                                      buffer-or-name))))
      ;; Find and select the main window, then switch buffer
      (let ((main-window (catch 'found
                           (dolist (win (window-list))
                             (when (and (window-live-p win)
                                       (not (eq win emigo-window))
                                       (not (window-parameter win 'window-side)))
                               (throw 'found win))))))
        (if main-window
            (progn
              (select-window main-window)
              (funcall orig-fun buffer-or-name norecord force-same-window))
          ;; No main window found, use original behavior
          (funcall orig-fun buffer-or-name norecord force-same-window)))
    ;; Not applicable, use original behavior
    (funcall orig-fun buffer-or-name norecord force-same-window)))

;; Apply the advice
(advice-add 'switch-to-buffer :around #'emigo--switch-to-buffer-advice)

(provide 'emigo-window)
;;; emigo-window.el ends here
