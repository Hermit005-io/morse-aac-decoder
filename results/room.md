# Sound decoding in a simulated room

Letters wrong, averaged over 20 phrases x 2 seeds, 10 WPM sender with 15% timing wobble, no calibration.

| Situation | Letters wrong | Messages exactly right |
| --- | --- | --- |
| No audio (the sender's timing itself) | 1.4% | 34 of 40 |
| Quiet room | 1.1% | 35 of 40 |
| Quiet room, typing | 1.1% | 35 of 40 |
| Fan and quiet talking | 1.3% | 35 of 40 |
| Talking as loud as beeps | 1.3% | 35 of 40 |
| Talking louder than beeps | 1.8% | 32 of 40 |
| Faint beeps, some echo | 24.0% | 23 of 40 |
| Echoey room | 34.1% | 27 of 40 |

Noise only (loud talking, clicks, rumble; no beeps), 10 minutes: 0 letters decoded.

# Taps on a desk, in the same room

Letters wrong, averaged over 20 phrases x 2 seeds. One tap is a dot, two quick taps a dash; the taps' timing wobbles by 15%.

| Situation | Letters wrong | Messages exactly right |
| --- | --- | --- |
| No audio (the taps' timing itself) | 0.3% | 36 of 40 |
| Quiet room | 0.5% | 35 of 40 |
| Loud talking, nothing else | 1.6% | 30 of 40 |
| Typing nearby, much quieter than the taps | 5.1% | 23 of 40 |
| Other sharp sounds half as loud as the taps | 87.8% | 0 of 40 |

Noise only (loud talking, nothing else; no taps), 10 minutes: 0 taps heard.
Noise only (typing nearby, much quieter than the taps; no taps), 10 minutes: 1171 taps heard.
