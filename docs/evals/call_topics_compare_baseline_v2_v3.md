# Call-Topic Agent: baseline vs v2 vs v3

| Metric | baseline | v2 | v3 |
|---|---|---|---|
| Runs | 5 | 5 | 5 |
| Mean cost per run | $0.00065 | $0.00107 | $0.00042 |
| Cost range | $0.00047 to $0.00085 | $0.00055 to $0.00204 | $0.00032 to $0.00066 |
| Mean input tokens | 1,844 | 2,655 | 2,237 |
| Mean cached input tokens | 1,248 | 2,041 | 1,706 |
| Mean output tokens (reasoning) | 1,125 (734) | 1,939 (1,576) | 679 (320) |
| Mean latency | 16.7s | 16.1s | 8.0s |
| Retries | 0 | 1 | 0 |
| Empty answers (whole cap spent on reasoning) | 0 | 1 | 0 |
| Starters returned | 25/25 | 25/25 | 25/25 |
| Starters that retell (6+ words copied) | 14/25 | 0/25 | 0/25 |
| Mean longest copied run (words) | 7.0 | 2.2 | 2.1 |
| Openers sharing first 3 words within a run | 0 | 2 | 0 |
| Most common opening across runs | "i have to" x3 | "i have a" x4 | "the honeycrisp tax" x2 |
| Openers copying 4+ words from a prompt example | 0/25 | 5/25 | 0/25 |
| Starters about the partner's moments | 11/25 | 14/25 | 13/25 |
| Starters about Leo in Priya's runs | 3/15 | 6/15 | 5/15 |
| Flags: generic / unsupported / same_source | 0 / 0 / 0 | 0 / 0 / 6 | 0 / 0 / 4 |
| Deeper questions citing the headphones moment | 5/5 | 3/5 | 4/5 |
