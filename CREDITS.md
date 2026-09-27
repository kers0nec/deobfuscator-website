# Credits and licenses

This site is a front end and an orchestration layer. The deobfuscation itself is
done by other people's engines, vendored under `engine/` with their licenses
intact. Nothing here would work without them.

## Vendored engines

### `engine/cadmio` — Apache License 2.0

The dynamic deobfuscation pipeline: it runs a protected script in a real Luau VM
against a fake Roblox/executor environment, records what it does, and rebuilds
source from that. For Luraph v15 and IronBrew 1 it goes further and lifts the VM
bytecode back to Luau with control flow, locals and closures.

- Upstream: <https://github.com/requiemzc/Cadmio> (`deobf/` package)
- License: Apache-2.0 — full text in [`engine/cadmio/LICENSE`](engine/cadmio/LICENSE)
- The pipeline's original author credits themselves in output as
  `ccjvwsod on Discord`; the Cadmio README credits **Francy**. Both are the
  reason Luraph v15 and IronBrew 1 devirtualization works here.

**Modifications (Apache-2.0 §4(b)).** The vendored sources under
`engine/cadmio/deobf/` are **unmodified** — they are byte-for-byte what upstream
ships, minus the Windows `.exe` binaries, the `research/` development scripts
and the generator scripts (`build_luau.py`, `gen_roblox.py`, `gen_unicode.py`),
which this repo replaces with its own equivalents under `tools/`.

The one behavioural change is made from *outside* the vendored tree, at runtime,
in [`server/branding.py`](server/branding.py): it rebinds
`obfuscators.base.Job.credit_header` and `traceout.header` so result files carry
this site's name instead of an upstream author's Discord handle. This is the
same seam upstream's own `--no-credit` flag uses. Upstream's Apache-2.0 license
and copyright notice remain in the distribution, as the license requires.

### `engine/luauvmp` — MIT License

Static and sandbox-assisted analysis of Luau VM-protected scripts, including the
staged **Luraph v14.x** loaders: base85/range-code unpacking, VM interpreter and
bytecode extraction, dispatcher recovery, and prototype-tree devirtualization.

- Upstream: <https://github.com/binxgtl/luau-vmp-deobf> (`luauvmp/` package)
- License: MIT — full text in [`engine/luauvmp/LICENSE`](engine/luauvmp/LICENSE)
- Copyright (c) binxgtl

**Modifications.** None. The package is vendored as published. This site always
passes `--no-lua-expert`, which disables upstream's optional post-pass that
uploads compiled bytecode to the third-party `api.lua.expert` service — a user of
this site never agreed to that upload.

## External components fetched at setup time

| Component | License | Where it comes from | What it is used for |
|---|---|---|---|
| [Luau](https://github.com/luau-lang/luau) | MIT | cloned and compiled by `tools/build_luau.py` | the sandbox VM (`luau`) and the AST dumper (`luau-ast`) |
| [Lune](https://github.com/lune-org/lune) | MIT | downloaded by `tools/install_lune.sh` | the restricted capture sandbox for Luraph v14.x |

The Luau build applies one patch, described in
[`tools/build_luau.py`](tools/build_luau.py): the vector type's metatable is
left writable instead of frozen, so the sandbox environment can install Roblox's
`Vector3` members. The patch approach follows Cadmio's `deobf/build_luau.py`
(Apache-2.0); building `luau-ast` alongside `luau` is this repo's addition.

Neither binary is committed. They are built or downloaded on the machine that
runs the site, so no third-party binary is redistributed from here.

## Research and prior art

The techniques these engines implement are published research. In particular
ferib's Lua devirtualization series
([part 3 covers Luraph](https://ferib.dev/blog/lua-devirtualization-part-3-devirtualizing-luraph/))
documented the lifting approach that later Luraph devirtualizers build on.

## Sample corpus

Detection was developed and measured against
[kers0nec/obfuscator-samples](https://github.com/kers0nec/obfuscator-samples)
("brought to you by 25ms"), a labelled collection of obfuscated scripts. **No
sample from that corpus is committed to this repository.** It is only read at
test time when `SAMPLES_DIR` points at a local checkout:

```bash
SAMPLES_DIR=/path/to/obfuscator-samples python tests/smoke.py --corpus
```

The fixtures under `tests/fixtures/` are synthetic and written for this repo.

## This repository's own code

`server/`, `web/`, `tools/`, `tests/`, `run.py`, `setup.sh`, `Dockerfile` and
`docker-compose.yml` are original to this project and licensed under the MIT
license in [`LICENSE`](LICENSE).
