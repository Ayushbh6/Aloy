#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."
runtime_dir="$HOME/Library/Application Support/Aloy/runtime"
if [[ ! -x "$runtime_dir/venv/bin/python" ]]; then
  echo "Missing Aloy runtime at $runtime_dir/venv; run the local setup first." >&2
  exit 1
fi
"$runtime_dir/venv/bin/python" -m pip install --quiet --no-deps --no-build-isolation .
mkdir -p mac/build/Aloy.app/Contents/MacOS mac/build/Aloy.app/Contents/Resources
swiftc -swift-version 5 mac/Aloy.swift mac/OrbView.swift mac/InteractionState.swift mac/VoiceAudio.swift mac/Playground.swift mac/Canvas.swift mac/CompanionBubble.swift mac/ComputerActionApproval.swift mac/ScreenCapture.swift -framework AppKit -framework ApplicationServices -framework AVFoundation -framework CoreGraphics -framework ScreenCaptureKit -framework SwiftUI \
  -o mac/build/Aloy.app/Contents/MacOS/Aloy
cp mac/Info.plist mac/build/Aloy.app/Contents/Info.plist
cat > mac/build/Aloy.app/Contents/Resources/launch.json <<EOF
{"python":"$runtime_dir/venv/bin/python","project":"$runtime_dir"}
EOF
echo "$PWD/mac/build/Aloy.app"
