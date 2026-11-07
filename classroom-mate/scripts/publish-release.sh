#!/usr/bin/env bash
set -euo pipefail

installer=ClassroomMate-Setup-win-x64.exe
tag="classroom-mate-build-$GITHUB_RUN_NUMBER"
origin=https://claw.dickhead.top/downloads/classroom-mate
assets=("$installer" "$installer.sha256" release.json)
: "${MIRROR_HOST:?Missing release server}" "${MIRROR_HOST_KEY:?Missing pinned SSH host key}" "${MIRROR_KEY:?Missing upload key}"

if draft=$(gh release view "$tag" --json isDraft --jq '.isDraft' 2>/dev/null); then
    # A rerun always mirrors the original release bytes.
    gh release download "$tag" --clobber --pattern "$installer" --pattern "$installer.sha256" --pattern release.json
else
    draft=true
    version=$(jq -er '.version' release.json)
    gh release create "$tag" "${assets[@]}" --draft --target "$GITHUB_SHA" \
        --title "Classroom Mate $version · Windows x64" \
        --notes "Windows x64 安装程序。源代码提交：$GITHUB_SHA。构建与安装验证：$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
fi

sha256sum --check "$installer.sha256"
version=$(jq -er '.version' release.json)
digest=$(jq -er '.sha256' release.json)
test "${version##*.}" = "$GITHUB_RUN_NUMBER"
printf '%s  %s\n' "$digest" "$installer" | sha256sum --check

ssh_files=$(mktemp -d)
trap 'rm -r "$ssh_files"' EXIT
chmod 700 "$ssh_files"
printf '%s\n' "$MIRROR_KEY" > "$ssh_files/key"
chmod 600 "$ssh_files/key"
printf '%s\n' "$MIRROR_HOST_KEY" > "$ssh_files/known_hosts"
cat release.json "$installer" | ssh -T -o BatchMode=yes -o ConnectTimeout=15 -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$ssh_files/known_hosts" \
    -i "$ssh_files/key" "classroom-mate-release@$MIRROR_HOST"

fetch() {
    curl --fail --silent --show-error --retry 3 --max-time 180 --proto '=https' "$1"
}

latest_version=$(fetch "$origin/latest/release.json" | jq -er '.version')
test "$(printf '%s\n' "$version" "$latest_version" | sort -V | tail -n1)" = "$latest_version"
latest=false
url="$origin/build-$GITHUB_RUN_NUMBER/$installer"
if [[ "$latest_version" == "$version" ]]; then
    latest=true
    url="$origin/latest/$installer"
fi
curl --fail --silent --show-error --retry 3 --max-time 180 --proto '=https' --output "$ssh_files/served.exe" "$url"
actual=$(sha256sum "$ssh_files/served.exe")
test "${actual%% *}" = "$digest"

if [[ "$draft" == true ]]; then
    gh release edit "$tag" --draft=false --latest="$latest"
fi
gh release view "$tag" --json url --jq '.url'
