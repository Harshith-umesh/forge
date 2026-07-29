#!/usr/bin/env bash
set -euo pipefail

# Gather the diff that represents what the user intends to commit.
# Priority: staged > unstaged > branch diff against remote.
# Outputs structured sections so the reviewing agent can parse reliably.

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "HEAD")
REMOTE_BRANCH="origin/${BRANCH}"

echo "=== DIFF_META ==="
echo "branch: ${BRANCH}"
echo "remote: ${REMOTE_BRANCH}"
echo "timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo ""
echo "=== STATUS ==="
git status --short

STAGED=$(git diff --cached)
UNSTAGED=$(git diff)

if [[ -n "${STAGED}" ]]; then
    echo ""
    echo "=== DIFF_TYPE: staged ==="
    echo "${STAGED}"

    if [[ -n "${UNSTAGED}" ]]; then
        echo ""
        echo "=== UNSTAGED_WARNING ==="
        echo "There are also unstaged changes that will NOT be part of this commit:"
        git diff --stat
    fi
elif [[ -n "${UNSTAGED}" ]]; then
    echo ""
    echo "=== DIFF_TYPE: unstaged ==="
    echo "${UNSTAGED}"
else
    HAS_REMOTE=$(git ls-remote --heads origin "${BRANCH}" 2>/dev/null | head -1)
    if [[ -n "${HAS_REMOTE}" ]]; then
        BRANCH_DIFF=$(git diff "${REMOTE_BRANCH}...HEAD" 2>/dev/null || true)
        if [[ -n "${BRANCH_DIFF}" ]]; then
            echo ""
            echo "=== DIFF_TYPE: branch ==="
            echo "${BRANCH_DIFF}"
        else
            echo ""
            echo "=== DIFF_TYPE: none ==="
            echo "No changes found between local and remote."
        fi
    else
        echo ""
        echo "=== DIFF_TYPE: none ==="
        echo "No remote branch and no local changes."
    fi
fi

UNTRACKED=$(git ls-files --others --exclude-standard)
if [[ -n "${UNTRACKED}" ]]; then
    echo ""
    echo "=== UNTRACKED ==="
    echo "${UNTRACKED}"
fi
