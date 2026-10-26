Modified only `algorithm.py` to add a bounded, preference-guided floor translation correction. Each correction stays within 6 cm and must reduce analytic relation error after clamping, while preserving random draws, yaw, and query limits.

Tests were not run; physics, diversity retention, and measured gains await external validation.
