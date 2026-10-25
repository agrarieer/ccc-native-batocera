# Third-party overlay notices

The wrapper's license does not relicense pygame, pinproc or their incorporated code.

* pygame 1.9.6: LGPL-2.1; `pygame-0-LGPL`, `LGPL-2.1.txt` and companion `GPL-2.txt`. Full retained source notices include SDL_gfx, rotozoom and Python-derived queue/thread code. Source bytes are preserved in the flat source sidecar.
* Python license for queue/thread-derived code: `Python-2.7.18.txt`, retrieved from https://raw.githubusercontent.com/python/cpython/v2.7.18/LICENSE (SHA256 `de4d1f2d2ad5ad0cfd1657a106476b31cb5db5ef9d1ff842b237c0c81f0c8a23`). No changes to retained queue/thread code.
* libpinproc / pypinproc: MIT grants in their respective README-and-license files and source headers.
* FreeSans and pygame icons are NOT in either redistributed overlay archive. The recipient downloads the official pinned pygame sdist directly and copies six resources locally. The old font's metadata specifies GPL; no font exception or preferred-form font source was established here. Do not publish the assembled runtime or download cache.

Modified LGPL components may be replaced and debugged without additional restrictions imposed by this wrapper. The distributed overlay is a fresh audited rebuild, not an assertion about the earlier historical binaries. Its code-only source sidecar retains generated Setup, compile/link logs, toolchain records, static archive fingerprint and a linker map matched to the fresh binary allowlist; see BUILD-INSTRUCTIONS.txt. Historical binary correspondence remains unverified and is superseded by this fresh build.
