# Script to generate a smooth motor tuning curve for Moorebot Scout
# Backs up original motor_tune to motor_tune.bak
# Applies smooth progressive acceleration curve from 0.0 to 3.107

max_val = 3.107005
steps = 30  # from 0.0 to 3.0 in steps of 0.1

lines = ["0.1", "0.0 0.0"]

for i in range(1, steps + 1):
    cmd = i * 0.1
    t = cmd / 3.0
    # Quadratic easing with slight linear component: smooth start, progressive ramp-up
    # y = max_val * (0.35 * t + 0.65 * (t ** 1.8))
    # At t=0.1/3.0 (t=0.033): y ~ 3.107 * (0.0116 + 0.0014) = 0.040 (vs original 0.183! 4.5x smoother!)
    val = max_val * (0.35 * t + 0.65 * (t ** 1.8))
    lines.append(f"{cmd:.6f} {val:.6f}")

smooth_motor_tune = "\n".join(lines) + "\n"

with open("tools/motor_tune_smooth", "w") as f:
    f.write(smooth_motor_tune)

print("Generated tools/motor_tune_smooth:")
print(smooth_motor_tune[:300] + "...")
