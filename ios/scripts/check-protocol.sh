#!/bin/sh
set -eu
protocol_ios_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
protocol_tmp=$(mktemp -d /tmp/hackathon-protocol.XXXXXX)
trap 'rm -rf "$protocol_tmp"' EXIT HUP INT TERM
xcrun swiftc -swift-version 5 -strict-concurrency=complete -warnings-as-errors \
  -module-cache-path "$protocol_tmp/cache" \
  "$protocol_ios_dir/Shared/Sources/ProjectConfiguration.swift" \
  "$protocol_ios_dir/Shared/Sources/SharedStore.swift" \
  "$protocol_ios_dir/Shared/Sources/ServerModels.swift" \
  "$protocol_ios_dir/Shared/Sources/ServerClient.swift" \
  "$protocol_ios_dir/Shared/Sources/SharedJournal.swift" \
  "$protocol_ios_dir/Shared/Sources/ScreenTextReader.swift" \
  "$protocol_ios_dir/Shared/Sources/ScreenTextMailbox.swift" \
  "$protocol_ios_dir/Shared/Sources/ScreenStillness.swift" \
  "$protocol_ios_dir/Shared/Sources/WindowFinder.swift" \
  "$protocol_ios_dir/Shared/Sources/FrameShelf.swift" \
  "$protocol_ios_dir/App/Sources/Call/SpeechDetector.swift" \
  "$protocol_ios_dir/App/Sources/Call/PhraseListener.swift" \
  "$protocol_ios_dir/App/Sources/Call/PauseWords.swift" \
  "$protocol_ios_dir/App/Sources/HintWindow/EyesDirector.swift" \
  "$protocol_ios_dir/App/Sources/HintWindow/EyesPainter.swift" \
  "$protocol_ios_dir/Tests/ProtocolChecks.swift" \
  -o "$protocol_tmp/check-protocol"
"$protocol_tmp/check-protocol" "$@"
