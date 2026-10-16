# Strata Profile Studio: setup's calibration for one run config, for a Strata build without the /profile routes.
# Strata 0.1.44.1's `setup.py --calibrate` also updates the installed engine and starts the model when it ends, so the
# Studio calls the same function that it calls, setup.calibrate_config(config), and nothing else.  This is what
# Strata's serve/profile_page.py does for POST /profile/tune (Strata PR #2661).
#     <python> calibrate_one.py <Strata folder> <strata-model.json>
# Copyright (c) 2026 Niko1221 and the Strata contributors (the call it makes). MIT licence: LICENSE-strata.txt.
import os
import signal
import sys
from pathlib import Path


def main(argv) -> int:
    if len(argv) != 3:
        print("usage: calibrate_one.py <Strata folder> <strata-model.json>", flush=True)
        return 2
    root, cfg = Path(argv[1]).resolve(), Path(argv[2]).resolve()
    if not (root / "setup.py").is_file() or not cfg.is_file():
        print(f"calibrate_one.py: no setup.py in {root}, or no file {cfg}", flush=True)
        return 2
    os.chdir(root)
    sys.path.insert(0, str(root))
    import setup                                       # Strata's own setup.py

    def _term(signum, frame):                          # the Studio's stop: end the calibration's engine on the way out
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    return 0 if setup.calibrate_config(cfg) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
