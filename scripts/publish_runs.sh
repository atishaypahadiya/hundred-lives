#!/bin/bash
# Publish every finished run that viewer/publish.yaml selects on the project site's Runs page.
#
#   scripts/publish_runs.sh            # rebuild docs/runs/ (gitignored), print the scrub summary and size
#   scripts/publish_runs.sh --deploy   # then publish the site on gh-pages as one orphan commit
#
# The site is master's committed docs/ (the project page, replays/) plus the generated docs/runs/,
# at the branch root, with .nojekyll. gh-pages always holds a single commit, so run data never
# enters any branch's history; GitHub Pages serves gh-pages. --deploy refuses while docs/ has
# uncommitted changes, so what's published is what's on master. It needs `gh` logged in.
#
# How --deploy uploads: pushing an orphan commit makes git send every object (~6 MB) even when
# GitHub has them all, and large pushes to GitHub here failed about 40% of the time ("pack has bad
# object ... inflate returned -5"), over SSH and HTTPS alike, with or without compression or one
# packing thread. So: if gh-pages already has this exact tree, nothing is pushed. Otherwise the
# new tree goes up as a throwaway child of the published commit on <branch>-upload (git then sends
# only what changed), with up to 3 tries alternating SSH and HTTPS; GitHub's API makes the orphan
# commit from that tree and points gh-pages at it; the upload branch is deleted; and the result is
# checked (tree matches, single commit). PAGES_BRANCH=<name> deploys to another branch (for tests).
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

BR=${PAGES_BRANCH:-gh-pages}
REPO=$(git remote get-url origin | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')
gh auth status >/dev/null 2>&1 || { echo "--deploy needs gh logged in (gh auth login)" >&2; exit 1; }

# Build the site tree in a throwaway index: master's working tree and index are never touched.
gitdir=$(cd "$(git rev-parse --git-dir)" && pwd)
index=$(mktemp "${TMPDIR:-/tmp}/hl-pages-index.XXXXXX")
trap 'rm -f "$index"' EXIT
rm -f "$index"
export GIT_INDEX_FILE=$index
git read-tree "HEAD:docs"                                            # the committed page and replays
(cd docs && git --git-dir="$gitdir" --work-tree=. add -f -- runs)   # the generated runs, only
git update-index --add --cacheinfo "100644,$(git hash-object -w /dev/null),.nojekyll"
tree=$(git write-tree)
unset GIT_INDEX_FILE
n=$(python3 -c 'import json; print(len(json.load(open("docs/runs/runs.json"))["runs"]))')
msg="Site: project page and $n published lives, from master $(git rev-parse --short HEAD)"

push() {  # push <refspec>: up to 3 tries, SSH, HTTPS (gh's credentials, nothing on the command line), SSH
  local t
  for t in ssh https ssh; do
    if [ "$t" = ssh ]; then git push --force -q origin "$1" && return 0
    else git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push --force -q "https://github.com/$REPO.git" "$1" && return 0
    fi
    echo "push over $t failed; trying again" >&2
  done
  return 1
}

remote=$(git ls-remote origin "refs/heads/$BR" | cut -f1)
if [ -n "$remote" ] && [ "$(gh api "repos/$REPO/git/commits/$remote" --jq .tree.sha)" = "$tree" ]; then
  echo "$BR already has this site (tree ${tree:0:7}); nothing to push"
  exit 0
fi
if [ -z "$remote" ]; then  # first deploy: nothing to diff against, so the whole site goes up
  push "$(git commit-tree "$tree" -m "$msg"):refs/heads/$BR"
else
  git fetch -q origin "+refs/heads/$BR:refs/remotes/origin/$BR"   # the published commit, as the diff's base
  stage=$(git commit-tree "$tree" -p "$remote" -m "upload for: $msg")
  echo "uploading what changed: $(git rev-list --objects "$stage" --not "$remote" | wc -l | tr -d ' ') objects"
  push "$stage:refs/heads/$BR-upload"
  body=$(python3 -c 'import json, sys; print(json.dumps({"message": sys.argv[1], "tree": sys.argv[2], "parents": []}))' "$msg" "$tree")
  commit=$(gh api "repos/$REPO/git/commits" --input - --jq .sha <<< "$body")
  gh api -X PATCH "repos/$REPO/git/refs/heads/$BR" -f sha="$commit" -F force=true >/dev/null
  gh api -X DELETE "repos/$REPO/git/refs/heads/$BR-upload" >/dev/null
fi

# Check what GitHub now serves: the built tree, as a single commit.
remote=$(git ls-remote origin "refs/heads/$BR" | cut -f1)
got=$(gh api "repos/$REPO/git/commits/$remote" --jq '"\(.tree.sha) \(.parents | length)"')
[ "$got" = "$tree 0" ] || { echo "$BR is $remote with tree/parents '$got', expected '$tree 0'" >&2; exit 1; }
echo "published $BR ${remote:0:7}: tree ${tree:0:7}, one commit, $(git ls-tree -r --name-only "$tree" | wc -l | tr -d ' ') files"
