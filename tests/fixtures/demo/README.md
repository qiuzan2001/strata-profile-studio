# Demo data

`python3 studio.py --demo` and the page check use these files. They are real data of one PC (RTX 5070 Ti 16 GB,
Ryzen 7 5800X3D, Swift 1.5 IQ3_S), recorded on 2026-10-15, with the home folder replaced by `~`.

- `reports/`: seven profile reports of Strata's `tools/strata_profile.py` (runs 1 to 7). Run 1 has the engine
  defaults (the baseline). The tune of 15:24 is between run 5 and run 6.
- `report-times.json`: the time of each report (Git keeps no file times; the demo sets them on its copies).
- `studio-state.json`: the Studio's record of that tune.
- `live.json`: per-request samples of Strata's `/profile/last`, replayed by the demo Strata.
- `hardware.json`: Strata's `/metrics` hardware readings.
- `settings.json`: Strata's `/profile/settings`.
- `strata-swift-iq3_s.json`: the run config, without the PC's paths, host names and library folders.
- `strata-swift-iq3_s.log`: three lines of the engine log (the PCIe probe, the pool workers, a VRAM warning).
- `expert-profile.bin`: Strata's expert profile for this model (`data/expert-profile.bin` of
  [Strata](https://github.com/Niko1221/Strata), MIT licence, Copyright (c) 2026 Niko1221 and the Strata contributors).
