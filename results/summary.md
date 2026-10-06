# Benchmark summary

Character error rate (lower is better), averaged over 20 phrases x 5 random seeds per cell.

| Condition | Textbook | Adaptive (no setup) | Adaptive + calibration | Adaptive, no repair |
| --- | --- | --- | --- | --- |
| Timing jitter (typical % off): 0.0 | 0.0% | 0.0% | 0.0% | 0.0% |
| Timing jitter (typical % off): 0.1 | 0.0% | 0.3% | 0.0% | 0.0% |
| Timing jitter (typical % off): 0.2 | 6.6% | 6.4% | 5.7% | 6.0% |
| Timing jitter (typical % off): 0.3 | 27.6% | 36.3% | 33.5% | 34.5% |
| Timing jitter (typical % off): 0.4 | 45.8% | 58.4% | 54.7% | 55.9% |
| Timing jitter (typical % off): 0.5 | 62.1% | 87.4% | 71.3% | 71.7% |
| Slowdown by end of message (fatigue): 0.0 | 1.3% | 1.7% | 0.7% | 0.7% |
| Slowdown by end of message (fatigue): 0.25 | 1.1% | 1.6% | 0.8% | 0.8% |
| Slowdown by end of message (fatigue): 0.5 | 4.3% | 1.8% | 1.0% | 1.0% |
| Slowdown by end of message (fatigue): 1.0 | 48.4% | 2.3% | 1.6% | 1.6% |
| Slowdown by end of message (fatigue): 1.5 | 103.8% | 3.1% | 3.4% | 3.7% |
| Slowdown by end of message (fatigue): 2.0 | 139.0% | 5.3% | 4.6% | 5.5% |
| Dash length, in dots (textbook = 3): 1.8 | 52.2% | 20.3% | 6.8% | 7.3% |
| Dash length, in dots (textbook = 3): 2.0 | 39.3% | 7.6% | 3.3% | 3.5% |
| Dash length, in dots (textbook = 3): 2.5 | 6.6% | 1.7% | 1.4% | 1.5% |
| Dash length, in dots (textbook = 3): 3.0 | 1.3% | 1.7% | 0.7% | 0.7% |
| Dash length, in dots (textbook = 3): 3.5 | 0.6% | 1.3% | 0.4% | 0.4% |
| Dash length, in dots (textbook = 3): 4.0 | 0.6% | 1.3% | 0.5% | 0.5% |
| Gaps stretched (slow release): 1.0 | 1.3% | 1.7% | 0.7% | 0.7% |
| Gaps stretched (slow release): 1.25 | 2.5% | 1.7% | 0.7% | 0.7% |
| Gaps stretched (slow release): 1.5 | 15.8% | 1.7% | 0.7% | 0.7% |
| Gaps stretched (slow release): 2.0 | 145.7% | 1.7% | 0.7% | 0.7% |
| Gaps stretched (slow release): 2.5 | 241.7% | 1.7% | 0.7% | 0.7% |
| Gaps stretched (slow release): 3.0 | 254.2% | 1.7% | 0.7% | 0.7% |
| Sender speed, WPM (textbook set to 10): 4 | 248.0% | 1.7% | 0.7% | 0.7% |
| Sender speed, WPM (textbook set to 10): 6 | 58.7% | 1.7% | 0.7% | 0.7% |
| Sender speed, WPM (textbook set to 10): 8 | 1.8% | 1.7% | 0.7% | 0.7% |
| Sender speed, WPM (textbook set to 10): 10 | 1.3% | 1.7% | 0.7% | 0.7% |
| Sender speed, WPM (textbook set to 10): 15 | 80.7% | 1.7% | 0.7% | 0.7% |
| Sender speed, WPM (textbook set to 10): 20 | 97.3% | 1.7% | 0.7% | 0.7% |
