#!/bin/sh
# Starts Rimuru as the person who owns the folders next to docker-compose.yml, not
# as root: what it and its clones write there is theirs to open, change and delete.
set -e

# skills/ comes with the repository, so it tells who that is. PUID and PGID choose
# someone else. Docker Desktop shows every folder as root's: then a plain user.
owner=$(stat -c %u /rimuru/skills)
group=$(stat -c %g /rimuru/skills)
if [ "$owner" = 0 ]; then
  owner=1000
  group=1000
fi
uid=${PUID:-$owner}
gid=${PGID:-$group}

if [ "$uid" != 0 ]; then
  # Docker makes a missing folder as root's, and images before this one ran as root.
  for folder in /rimuru/data /rimuru/skills /projects; do
    if [ "$(stat -c %u "$folder")" = 0 ]; then
      chown "$uid:$gid" "$folder" 2>/dev/null || true
      # Only Rimuru's own folder, all the way down - and only where chown does anything.
      if [ "$folder" = /rimuru/data ] && [ "$(stat -c %u "$folder")" = "$uid" ]; then
        chown -R "$uid:$gid" "$folder"
      fi
    fi
  done
fi

export HOME=/tmp
exec setpriv --reuid="$uid" --regid="$gid" --clear-groups "$@"
