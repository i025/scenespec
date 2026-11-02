Modified only `algorithm.py` to add bounded 3D distance refinement. It preserves seeded yaws and footprint bounds while accepting only analytic preference improvements that do not increase relation-limit error.

Tests were left to the external controller; Mesh and diversity gains remain unverified.
