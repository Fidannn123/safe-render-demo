"""M5 sim twins: 8 seeded SYNTHETIC SMEs, 90 simulated days, ground-truth labels.

Pure Python (no database, no network). ``build(seed)`` returns every output file as bytes and is
byte-identical for the same seed. CLI: ``python -m safepay_sim.twins --seed 42 --out <dir>``.
"""
