#!/usr/bin/env bash
set -euo pipefail

# Gather the diff that represents what the user intends to commit.
# Priority: staged > unstaged > branch diff against main (what the PR proposes).
# Outputs structured sections so the reviewing agent can parse reliably.

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "HEAD")
BASE_BRANCH="main"

echo "=== DIFF_META ==="
echo "branch: ${BRANCH}"
echo "base: ${BASE_BRANCH}"
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
    MERGE_BASE=$(git merge-base "${BASE_BRANCH}" HEAD 2>/dev/null || true)
    if [[ -z "${MERGE_BASE}" ]]; then
        echo ""
        echo "=== DIFF_TYPE: none ==="
        echo "Could not determine merge-base with ${BASE_BRANCH}."
    elif BRANCH_DIFF=$(git diff "${MERGE_BASE}...HEAD" 2>&1); then
        if [[ -n "${BRANCH_DIFF}" ]]; then
            echo ""
            echo "=== DIFF_TYPE: branch ==="
            echo "${BRANCH_DIFF}"
        else
            echo ""
            echo "=== DIFF_TYPE: none ==="
            echo "No changes found between ${BASE_BRANCH} and HEAD."
        fi
    else
        echo ""
        echo "=== DIFF_TYPE: error ==="
        echo "git diff failed: ${BRANCH_DIFF}"
    fi
fi

UNTRACKED=$(git ls-files --others --exclude-standard)
if [[ -n "${UNTRACKED}" ]]; then
    echo ""
    echo "=== UNTRACKED ==="
    echo "${UNTRACKED}"
fi
