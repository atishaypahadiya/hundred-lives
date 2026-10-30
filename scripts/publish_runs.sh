#!/bin/bash
# Publish every finished run that viewer/publish.yaml selects on the project site's Runs page.
#
#   scripts/publish_runs.sh            # rebuild docs/runs/ (gitignored), print the scrub summary and size
#   scripts/publish_runs.sh --deploy   # then force-push the site to gh-pages as one orphan commit
#
# The site is master's committed docs/ (the project page, replays/) plus the generated docs/runs/,
# at the branch root, with .nojekyll. gh-pages always holds a single commit, so run data never
# enters any branch's history; GitHub Pages serves gh-pages. --deploy refuses while docs/ has
# uncommitted changes, so what's published is what's on master.
#
# Reads the runs dirs below that exist (main tree, part A's worktree, part B's worktree); a run id
# found in two is taken from the first. real-names.txt (gitignored, one real name per line), if
# present, is the deny list: any of those names anywhere refuses the whole export.
# To publish a new experiment: add its group to viewer/publish.yaml (and its runs dir here).
set -euo pipefail
cd "$(dirname "$0")/.."

RUNS_DIRS=(runs ../hundred-lives-viewer/runs ../hundred-lives-exp02b/runs)
deploy=0
for a in "$@"; do
  case "$a" in
    --deploy) deploy=1 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done

if [ "$deploy" = 1 ] && ! git diff --quiet HEAD -- docs; then
  echo "docs/ has uncommitted changes; commit them on master first" >&2; exit 1
fi

args=()
for d in "${RUNS_DIRS[@]}"; do [ -d "$d" ] && args+=(--runs-dir "$d"); done
if [ -f real-names.txt ]; then args+=(--deny real-names.txt); else echo "note: no real-names.txt, so no deny list" >&2; fi

uv run python -m viewer.export --all "${args[@]}" --out docs/runs
echo "docs/runs: $(du -sh docs/runs | cut -f1) on disk, $(find docs/runs -type f | wc -l | tr -d ' ') files"
[ "$deploy" = 1 ] || exit 0

# Build the gh-pages tree in a throwaway index: master's working tree and index are never touched.
gitdir=$(cd "$(git rev-parse --git-dir)" && pwd)
export GIT_INDEX_FILE=$(mktemp "${TMPDIR:-/tmp}/hl-pages-index.XXXXXX")
trap 'rm -f "$GIT_INDEX_FILE"' EXIT
rm -f "$GIT_INDEX_FILE"
git read-tree "HEAD:docs"                                            # the committed page and replays
(cd docs && git --git-dir="$gitdir" --work-tree=. add -f -- runs)   # the generated runs, only
git update-index --add --cacheinfo "100644,$(git hash-object -w /dev/null),.nojekyll"
tree=$(git write-tree)
n=$(python3 -c 'import json; print(len(json.load(open("docs/runs/runs.json"))["runs"]))')
commit=$(git commit-tree "$tree" -m "Site: project page and $n published lives, from master $(git rev-parse --short HEAD)")
git update-ref refs/heads/gh-pages "$commit"
git push --force origin gh-pages
echo "pushed gh-pages $(git rev-parse --short "$commit"): $(git ls-tree -r --name-only "$commit" | wc -l | tr -d ' ') files"
