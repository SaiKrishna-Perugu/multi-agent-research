#!/usr/bin/env bash
# Source this at the beginning of each Git Bash tool call.
export PATH="/usr/bin:/bin:$PATH"
_gstack_script_dir=$(cd -- "${BASH_SOURCE[0]%/*}" && pwd -P)
_gstack_project=$(cd -- "$_gstack_script_dir/.." && pwd -P)
cd -- "$_gstack_project"
export GSTACK_ROOT="$_gstack_project/.gstack/source"
export GSTACK_HOME=".gstack/state"
export GSTACK_STATE_ROOT="$GSTACK_HOME"
export GSTACK_BIN="$GSTACK_ROOT/bin"
export GSTACK_BROWSE="$GSTACK_ROOT/browse/dist"
export GSTACK_DESIGN="$GSTACK_ROOT/design/dist"
export GSTACK_SKILLS="$_gstack_project/.agents/skills"
if [ ! -f "$GSTACK_SKILLS/gstack/SKILL.md" ]; then
  export GSTACK_SKILLS="$_gstack_project/.gstack/skills"
fi
export BROWSE_STATE_FILE="$(cygpath -m "$_gstack_project/.gstack/browse.json")"
export BROWSE_START_TIMEOUT="${BROWSE_START_TIMEOUT:-45000}"
if [ -z "${PLAYWRIGHT_BROWSERS_PATH:-}" ] && [ -n "${LOCALAPPDATA:-}" ]; then
  _gstack_browser_cache="$(cygpath -m "$LOCALAPPDATA/ms-playwright")"
  [ ! -d "$_gstack_browser_cache" ] || export PLAYWRIGHT_BROWSERS_PATH="$_gstack_browser_cache"
fi
unset _gstack_script_dir _gstack_project _gstack_browser_cache
