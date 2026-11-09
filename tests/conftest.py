import os

from pioneer.paths import TAU2_DATA_DIR

# Point tau2 at the pinned data checkout before tau2 is imported by any test.
os.environ.setdefault("TAU2_DATA_DIR", str(TAU2_DATA_DIR))
