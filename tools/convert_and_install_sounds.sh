#!/bin/bash
set -e
cd /tmp/scout_audio

for f in *.mp3; do
    base="${f%.mp3}"
    echo "Processing $f -> $base.wav"
    mpg123 -w "${base}_tmp.wav" "$f"
    sox "${base}_tmp.wav" -r 48000 -c 2 -b 16 "${base}.wav"
    rm -f "${base}_tmp.wav"
done

echo "Installing sounds to /var/roller_eye/devAudio/..."
cp -f power_on.wav /var/roller_eye/devAudio/power_on_down/power_on.wav
cp -f power_down.wav /var/roller_eye/devAudio/power_on_down/power_down.wav
cp -f charging.wav /var/roller_eye/devAudio/go_leave_home/charging.wav
cp -f leave_home.wav /var/roller_eye/devAudio/go_leave_home/leave_home.wav
cp -f battery_low.wav /var/roller_eye/devAudio/battery_low/battery_low.wav
cp -f obstacle_detected.wav /var/roller_eye/devAudio/detected/obstacle_detected.wav
cp -f got_new_instrction.wav /var/roller_eye/devAudio/got_new_instruction/got_new_instrction.wav
cp -f alert.wav /var/roller_eye/devAudio/alert/alert.wav

chown -R linaro:linaro /var/roller_eye/devAudio/
chmod -R 644 /var/roller_eye/devAudio/*/*
chmod -R 755 /var/roller_eye/devAudio/*/

echo "SUCCESS! Installed Portuguese system sounds."
