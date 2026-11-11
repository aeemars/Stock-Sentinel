#!/usr/bin/env bash
# Fails if tracked files contain the "hidden payload" pattern that was found in this repo
# (obfuscated code pushed past the visible edge of a line with long whitespace padding),
# or editor/auto-run files that execute code when a folder is opened.
set -u
cd "$(git rev-parse --show-toplevel)"
fail=0
self="scripts/check-no-hidden-code.sh"

# 1) very long lines in source/config files (real code in this repo never exceeds ~300 chars)
while IFS= read -r f; do
  [ "$f" = "$self" ] && continue
  case "$f" in *.png|*.ico|*.jpg|*.woff|*.woff2|*.ttf|*.eot|*.svg|*.webmanifest|package-lock.json) continue;; esac
  n=$(awk 'length($0)>1000{c++} END{print c+0}' "$f" 2>/dev/null)
  if [ "${n:-0}" -gt 0 ]; then echo "FAIL: $f has $n line(s) longer than 1000 chars"; fail=1; fi
done < <(git ls-files)

# 2) trailing whitespace padding followed by code on the same line (the hiding trick)
if git grep -nIE '[;}{)]\s{40,}\S' -- . ":!$self" >/dev/null 2>&1; then
  echo "FAIL: code hidden after 40+ whitespace characters:"; git grep -nIE '[;}{)]\s{40,}\S' -- . ":!$self" | cut -c1-120; fail=1
fi

# 3) auto-run / loader patterns
for pat in 'createRequire' 'folderOpen' 'allowAutomaticTasks'; do
  if git grep -nI "$pat" -- . ":!$self" >/dev/null 2>&1; then
    echo "FAIL: forbidden pattern '$pat':"; git grep -nI "$pat" -- . ":!$self" | cut -c1-120; fail=1
  fi
done

# 4) files that should not exist
for f in .vscode/tasks.json frontend/init; do [ -e "$f" ] && { echo "FAIL: $f must not exist"; fail=1; }; done
if git ls-files | grep -E '\.lfk$' >/dev/null; then echo "FAIL: .lfk file tracked"; fail=1; fi

[ $fail -eq 0 ] && echo "OK: no hidden-code patterns found" || exit 1
