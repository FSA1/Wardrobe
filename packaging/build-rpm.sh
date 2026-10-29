#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
version="$(sed -n "s/^[[:space:]]*version: '\\([^']*\\)'.*/\\1/p" "$repo_root/meson.build" | head -n 1)"
if [[ -z "$version" ]]; then
    printf 'Could not read the Meson project version.\n' >&2
    exit 1
fi
spec_version="$(sed -n 's/^Version:[[:space:]]*//p' "$repo_root/packaging/wardrobe.spec")"
if [[ "$version" != "$spec_version" ]]; then
    printf 'RPM spec version (%s) does not match Meson version (%s).\n' \
        "$spec_version" "$version" >&2
    exit 1
fi

if ! command -v rpmbuild >/dev/null 2>&1; then
    printf 'rpmbuild is required. Install Fedora package rpm-build first.\n' >&2
    exit 1
fi

topdir="${RPM_TOPDIR:-$HOME/rpmbuild}"
mkdir -p "$topdir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
list_file="$(mktemp)"
trap 'rm -f "$list_file"' EXIT

git -C "$repo_root" ls-files --cached -z > "$list_file"
git -C "$repo_root" ls-files --others --exclude-standard -z -- \
    packaging screenshots >> "$list_file"
tar \
    --directory="$repo_root" \
    --null \
    --files-from="$list_file" \
    --transform="s,^,wardrobe-${version}/," \
    --create \
    --gzip \
    --file="$topdir/SOURCES/wardrobe-${version}.tar.gz"

cp "$repo_root/packaging/wardrobe.spec" "$topdir/SPECS/wardrobe.spec"
rpmbuild --define "_topdir $topdir" -ba "$topdir/SPECS/wardrobe.spec"

printf '\nRPM package(s) created under %s/RPMS\n' "$topdir"
