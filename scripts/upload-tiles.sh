#!/usr/bin/env bash
# Upload the pyramids that `globepoch prepare --upload-dir _tiles` staged to the Spaces
# bucket, with the same settings as the deploy workflow's upload-tiles job.
#
# Needs the AWS CLI and a Spaces key in ~/.config/globepoch/spaces.env (or $SPACES_ENV):
#   SPACES_ACCESS_KEY_ID=...
#   SPACES_SECRET_ACCESS_KEY=...
set -euo pipefail

dir=${1:-_tiles}
env_file=${SPACES_ENV:-$HOME/.config/globepoch/spaces.env}
bucket=${SPACES_BUCKET:-globepoch}
endpoint=${SPACES_ENDPOINT:-https://sfo2.digitaloceanspaces.com}

# shellcheck source=/dev/null
source "$env_file"
export AWS_ACCESS_KEY_ID=$SPACES_ACCESS_KEY_ID
export AWS_SECRET_ACCESS_KEY=$SPACES_SECRET_ACCESS_KEY
export AWS_DEFAULT_REGION=us-east-1 # required by the CLI; Spaces ignores it
# Spaces rejects the checksums newer AWS CLIs send by default.
export AWS_REQUEST_CHECKSUM_CALCULATION=when_required
export AWS_RESPONSE_CHECKSUM_VALIDATION=when_required

if [ -z "$(find "$dir" -name '*.dzi' 2>/dev/null)" ]; then
  echo "nothing to upload in $dir"
  exit 0
fi

# The .dzi descriptors go last because the build treats a pyramid with a .dzi as fully
# uploaded.
opts=(--endpoint-url "$endpoint" --recursive --acl public-read
      --cache-control "public, max-age=31536000, immutable" --only-show-errors)
aws s3 cp "$dir" "s3://$bucket/tiles/" "${opts[@]}" --exclude "*.dzi"
aws s3 cp "$dir" "s3://$bucket/tiles/" "${opts[@]}" \
  --exclude "*" --include "*.dzi" --content-type application/xml
find "$dir" -name '*.dzi' | sed "s|^$dir/|uploaded |; s|/map.dzi$||"
