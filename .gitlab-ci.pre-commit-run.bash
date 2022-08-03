# Find a suitable commit for determining changed files
#
#
# Copyright 2022 Dom Sekotill <dom.sekotill@kodo.org.uk>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


pre_commit_run() (
	set -eu
	declare -a PRE_COMMIT_ARGS

	find_lca() {
		local repo=$CI_REPOSITORY_URL
		local current_branch=$1 other_branch=$2

		# See https://stackoverflow.com/questions/63878612/git-fatal-error-in-object-unshallow-sha-1
		# and https://stackoverflow.com/questions/4698759/converting-git-repository-to-shallow/53245223#53245223
		# for background on what `git repack -d` is doing here.
		git repack -qd

		git fetch -q $repo --shallow-exclude=$other_branch $current_branch
		git fetch -q $repo --deepen=1 $current_branch

		FROM_REF=$(git rev-parse -q --revs-only --verify shallow) || unset FROM_REF
	}

	fetch_ref() {
		git fetch -q $CI_REPOSITORY_URL --depth=1 $1
		FROM_REF=$1
	}

	if [[ -v CI_COMMIT_BEFORE_SHA ]] && [[ ! $CI_COMMIT_BEFORE_SHA =~ ^0{40}$ ]]; then
		fetch_ref $CI_COMMIT_BEFORE_SHA
	elif [[ -v CI_MERGE_REQUEST_TARGET_BRANCH_NAME ]]; then
		find_lca $CI_MERGE_REQUEST_SOURCE_BRANCH_NAME $CI_MERGE_REQUEST_TARGET_BRANCH_NAME
	elif [[ -v CI_COMMIT_BRANCH ]] && [[ $CI_COMMIT_BRANCH != $CI_DEFAULT_BRANCH ]]; then
		find_lca $CI_COMMIT_BRANCH $CI_DEFAULT_BRANCH
	fi

	if [[ -v FROM_REF ]]; then
		PRE_COMMIT_ARGS=( --from-ref=$FROM_REF --to-ref=$CI_COMMIT_SHA )
	else
		PRE_COMMIT_ARGS=( --all-files )
	fi

	pre-commit run "$@" "${PRE_COMMIT_ARGS[@]}"
)
