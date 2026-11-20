# C++ extensions (optional)

Placeholder for performance-critical kernels (e.g. per-frame pitch detection,
phase-vocoder inner loops) if pure NumPy turns out to be too slow.

Planned approach: write kernels in `cpp/src/`, expose them with
[pybind11](https://pybind11.readthedocs.io/), and import them from Python as
`dsfp_tune._native`, always keeping a NumPy fallback so the package works
without a compiler.

Nothing here is built yet.
