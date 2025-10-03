# Priority 1: Window Management - Implementation Summary

## Overview
Implemented flexible window management system for EmigoE with support for multiple display modes and intelligent session switching.

## Features Implemented

### 1. Flexible Window Display Modes

#### New Customization Variable
- **`emigo-use-side-window`** (default: `t`)
  - When `t`: Displays Emigo in a dedicated side window (original behavior)
  - When `nil`: Displays Emigo like a normal buffer

#### Toggle Function
- **`emigo-toggle-window-mode`** - Bound to `C-c w`
  - Switches between side-window and main-buffer modes
  - Preserves window visibility state when toggling
  - Shows current mode in message area

#### Behavior Changes
- **Side-window mode**:
  - Dedicated window on the right
  - Fixed width enforcement (via timer)
  - Window is marked as dedicated
  - Skipped by `other-window` cycling
  
- **Main-buffer mode**:
  - Behaves like a regular Emacs buffer
  - No width enforcement
  - Not marked as dedicated
  - Participates in normal window management

### 2. Smart Session Switching (Hybrid Approach)

#### Automatic Switching
- **`emigo--auto-switch-session`**
  - Automatically switches to the correct session when:
    - Emigo window is visible
    - You switch to a file in a different project
    - A session exists for that project
  - Enabled by default via `emigo-mode-hook`

#### Manual Switching
- **`emigo-switch-to-current-project`** - Bound to `C-c s`
  - Manually switch to the session for current buffer's project
  - Useful when Emigo window is hidden
  - Creates new session if none exists (prompts user)

#### Project Detection
- Uses `vc-git-root` (primary method)
- Falls back to `projectile-project-root` if projectile is available
- Falls back to current directory if no project detected

#### Control Functions
- **`emigo-enable-auto-switch`** - Enable automatic switching
- **`emigo-disable-auto-switch`** - Disable automatic switching

### 3. Multi-Session Support

#### Automatic Session Creation
- **`emigo-auto-create-sessions`** (default: `t`)
  - When enabled, automatically creates new sessions when switching to files in new projects
  - Works like treemacs - each project gets its own session automatically
  - When disabled, only switches between existing sessions

#### Session Tracking
- Each session is tracked by `emigo-session-path`
- Buffer naming: `*emigo:/path/to/project*`
- All sessions tracked in `emigo-project-buffers` list

#### Session Isolation
- Each session maintains its own:
  - Chat history
  - File context
  - Header line (shows project path and file count)
  - Prompt history

#### How It Works
1. Open Emigo in project A → Creates session for project A
2. Switch to a file in project B → Automatically creates and switches to session for project B
3. Switch back to project A file → Automatically switches to existing project A session

## New Keybindings

| Key     | Command                         | Description                    |
|---------|---------------------------------|--------------------------------|
| `C-c w` | `emigo-toggle-window-mode`      | Toggle window display mode     |
| `C-c s` | `emigo-switch-to-current-project` | Switch to current project session |

## Usage Examples

### Example 1: Toggle Window Mode
```elisp
;; In an Emigo buffer, press C-c w to toggle between modes
;; Side-window → Main-buffer → Side-window
```

### Example 2: Working with Multiple Projects
```elisp
;; 1. Open project A
(find-file "~/projects/project-a/file.py")
M-x emigo  ; Creates session for project-a

;; 2. Open project B
(find-file "~/projects/project-b/file.js")
M-x emigo  ; Creates session for project-b

;; 3. Switch back to project A file
(find-file "~/projects/project-a/another.py")
;; Emigo window automatically shows project-a session!
```

### Example 3: Manual Session Switching
```elisp
;; If auto-switch is disabled or window is hidden
;; Press C-c s to manually switch to current project's session
```

## Configuration

### Disable Side Window by Default
```elisp
(setq emigo-use-side-window nil)
```

### Disable Auto-Switching
```elisp
(remove-hook 'emigo-mode-hook #'emigo-enable-auto-switch)
;; Or call interactively: M-x emigo-disable-auto-switch
```

### Customize Window Width (Side-Window Mode Only)
```elisp
(setq emigo-window-width 60)  ; Default is 50
```

## Technical Details

### Modified Functions
- `emigo-create-window` - Now respects `emigo-use-side-window`
- `emigo-pop-window` - Conditional display logic
- `emigo-ensure-window-width` - Only enforces in side-window mode
- `emigo-select-window` - Conditional dedication

### New Functions
- `emigo-toggle-window-mode` - Toggle display mode
- `emigo-find-session-for-path` - Find session by path
- `emigo-switch-to-current-project` - Manual session switch
- `emigo--auto-switch-session` - Auto-switch logic
- `emigo-enable-auto-switch` / `emigo-disable-auto-switch` - Control auto-switching

### New Variables
- `emigo-use-side-window` - Display mode flag
- `emigo--auto-switch-enabled` - Auto-switch control flag

## Testing Checklist

- [ ] Test side-window mode with multiple sessions
- [ ] Test main-buffer mode with multiple sessions
- [ ] Test toggling between modes while window is visible
- [ ] Test toggling between modes while window is hidden
- [ ] Test auto-switching when changing files between projects
- [ ] Test manual switching with `C-c s`
- [ ] Test with projectile installed
- [ ] Test without projectile installed
- [ ] Test width enforcement only applies to side-window mode
- [ ] Test multiple concurrent sessions maintain separate contexts

## Next Steps

See ROADMAP.org for Priority 2: Magit-style Interface
- Transient menu for settings
- Allow changing `emigo-use-side-window` via menu
- Quick access to common operations
