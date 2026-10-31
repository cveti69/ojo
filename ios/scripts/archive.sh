#!/usr/bin/env bash
# Builds and exports the app for TestFlight under the shared iPhone lock. The upload is done separately.
# Usage: ios/scripts/archive.sh [--unsigned]
#   --unsigned  only checks that the Release archive compiles, no Apple account needed.
set -euo pipefail

IOS_DIR=$(cd "$(dirname "$0")/.." && pwd)
ROOT=$(dirname "$IOS_DIR")
BUILD_DIR=$ROOT/build
ARCHIVE=$BUILD_DIR/App.xcarchive
EXPORT_DIR=$BUILD_DIR/export
# Machine-specific settings stay out of git: LOCK and BUSY_LOCK name shared build locks on a Mac that has them.
LOCAL_SETTINGS=$IOS_DIR/scripts/local.env
if [ -f "$LOCAL_SETTINGS" ]; then . "$LOCAL_SETTINGS"; fi
LOCK=${LOCK:-}
BUSY_LOCK=${BUSY_LOCK:-}
LOCK_LABEL=${LOCK_LABEL:-archive}
LOCK_SESSION=${LOCK_SESSION:-hackathon-ios}
UNSIGNED=0
[ "${1:-}" = "--unsigned" ] && UNSIGNED=1

setting() { sed -n "s/^$1 = //p" "$IOS_DIR/Config/Project.xcconfig"; }

LOCKED=0
release_lock() { if [ "$LOCKED" = 1 ]; then rm -f "$LOCK"; fi; }
trap release_lock EXIT
trap 'exit 130' INT TERM HUP

take_lock() {
  if [ -z "$LOCK" ]; then
    echo "No build lock configured."
    return
  fi
  while :; do
    # noclobber makes the redirect fail if the lock already exists, so it never overwrites another run.
    if [ ! -e "$BUSY_LOCK" ] && ( set -o noclobber
         printf 'hackathon %s %s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$LOCK_LABEL" "$LOCK_SESSION" "$$" > "$LOCK" ) 2>/dev/null; then
      LOCKED=1
      if [ ! -e "$BUSY_LOCK" ]; then return; fi
      release_lock
      LOCKED=0
    fi
    echo "Waiting for the iPhone build lock..."
    sleep 30
  done
}

take_lock

(cd "$IOS_DIR" && xcodegen generate --quiet)

# A date-based number keeps every upload higher than the last, for all three targets at once.
BUILD_NUMBER=$(date +%y%m%d%H%M)
ARCHIVE_ARGS=(-project "$IOS_DIR/App.xcodeproj" -scheme App -configuration Release
  -destination 'generic/platform=iOS' -derivedDataPath "$BUILD_DIR" -archivePath "$ARCHIVE"
  CURRENT_PROJECT_VERSION="$BUILD_NUMBER")

rm -rf "$ARCHIVE" "$EXPORT_DIR"
# Automatic signing wants a registered device at this step, so the archive stays unsigned and the export signs it.
xcodebuild archive "${ARCHIVE_ARGS[@]}" CODE_SIGNING_ALLOWED=NO
if [ "$UNSIGNED" = 1 ]; then
  echo "Unsigned archive compiled: $ARCHIVE, build $BUILD_NUMBER"
  exit 0
fi

TEAM=$(setting DEVELOPMENT_TEAM)
if [ -z "$TEAM" ]; then
  echo "DEVELOPMENT_TEAM is empty in ios/Config/Project.xcconfig" >&2
  exit 1
fi

# The export reads entitlements from the existing signatures, so a local signature has to carry them.
APP=$ARCHIVE/Products/Applications/App.app
ENTITLEMENTS=$BUILD_DIR/AppGroup.entitlements
sed "s/\$(APP_GROUP_IDENTIFIER)/$(setting APP_GROUP_IDENTIFIER)/" "$IOS_DIR/Config/AppGroup.entitlements" > "$ENTITLEMENTS"
codesign --force --sign - "$APP"/Frameworks/*.framework
for item in "$APP"/PlugIns/*.appex "$APP"; do
  codesign --force --sign - --entitlements "$ENTITLEMENTS" "$item"
done

OPTIONS=$BUILD_DIR/ExportOptions.plist
cat > "$OPTIONS" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>export</string>
  <key>signingStyle</key><string>automatic</string>
  <key>teamID</key><string>$TEAM</string>
  <key>manageAppVersionAndBuildNumber</key><false/>
</dict>
</plist>
EOF

xcodebuild -exportArchive -archivePath "$ARCHIVE" -exportPath "$EXPORT_DIR" \
  -exportOptionsPlist "$OPTIONS" -allowProvisioningUpdates

echo "Ready for upload: $EXPORT_DIR, build $BUILD_NUMBER"
