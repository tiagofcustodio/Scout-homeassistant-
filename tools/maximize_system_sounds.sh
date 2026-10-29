#!/bin/bash
set -e
find /var/roller_eye/devAudio -name "*.wav" | while read -r f; do
    echo "Boosting: $f"
    sox "$f" "${f}_norm.wav" norm -0.1
    mv "${f}_norm.wav" "$f"
done
echo "All system sounds maximized to 0 dBFS!"
sox /var/roller_eye/devAudio/power_on_down/power_on.wav -n stat 2>&1 | grep -i 'amplitude'
