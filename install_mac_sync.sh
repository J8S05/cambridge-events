#!/bin/bash
# Sets up the background job that uploads Eventbrite events from this Mac every 6 hours.
#   Install:   bash install_mac_sync.sh
#   Remove:    bash install_mac_sync.sh remove
#   Log:       ~/Library/Logs/cambridge-events-sync.log
set -e
LABEL="uk.cam.events.eventbrite-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
WORK="$HOME/Library/Application Support/CambridgeEvents/repo"
LOG="$HOME/Library/Logs/cambridge-events-sync.log"

if [ "$1" = "remove" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed the Eventbrite background job."
  exit 0
fi

# The job runs from its own copy of the repository (macOS doesn't let background jobs read ~/Documents).
if [ ! -d "$WORK/.git" ]; then
  mkdir -p "$(dirname "$WORK")"
  git clone -q https://github.com/J8S05/cambridge-events.git "$WORK"
fi
git -C "$WORK" pull -q origin main

# One-time check that this Mac may upload to GitHub. If not yet allowed, git asks for
# Username (J8S05) and Password (paste the access token); macOS Keychain remembers it.
echo "Checking that this Mac can upload to GitHub…"
if ! git -C "$WORK" push --dry-run -q origin HEAD:main; then
  echo "GitHub didn't accept the sign-in. Run this script again and paste the token as the password."
  exit 1
fi
echo "GitHub upload access OK."

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/python3</string><string>$WORK/eventbrite_sync.py</string></array>
  <key>StartInterval</key><integer>21600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed. It runs now and then every 6 hours while the Mac is awake."
echo "Log: $LOG"
