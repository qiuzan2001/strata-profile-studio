Fixtures from documentation, unverified on hardware:

- rocm_smi.json: the shape of `rocm-smi --showclocks --showuse --showpower --showtemp --showmemuse --json` as AMD's
  ROCm SMI documentation shows it (card0 with "<name> (<unit>)" keys).  Key names differ between ROCm releases.
- amd_smi.json: the shape of `amd-smi metric --json` (AMD SMI documentation: nested {"value", "unit"} entries).
- vm_stat.txt: the output format of macOS `vm_stat`.
- proc_stat_crlf.txt: a /proc/stat with Windows line ends (a copy edited on Windows), for the parser's tolerance.

The Windows structures (SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION, PROCESSOR_POWER_INFORMATION) are built in the
test from their documented layouts.  The files one folder up (../) are real, from the development PC.
