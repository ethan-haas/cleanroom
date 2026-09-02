# lockfile-platform-mismatch fixture

`package.json` declares a REQUIRED dependency (`darwin-only-fixture-dep`),
and `package-lock.json`'s own entry for it embeds `"os": ["darwin"]` /
`"cpu": ["arm64"]` -- as a real lockfile does for a package with native
platform-specific builds (e.g. `esbuild`'s per-platform optional
packages). Because this is a *required* dependency, `npm ci` cannot skip
it the way it silently skips a platform-mismatched *optional* dependency.

Empirically verified against the pinned `node:20-slim` image, fully
offline (`--network none` -- no registry round-trip needed; npm checks
`os`/`cpu` against `process.platform`/`process.arch` using only the
metadata already embedded in the lockfile, before ever touching the
network):

```
npm error code EBADPLATFORM
npm error notsup Unsupported platform for darwin-only-fixture-dep@1.0.0: wanted {"os":"darwin","cpu":"arm64"} (current: {"os":"linux","cpu":"x64"})
npm error notsup Valid os:   darwin
npm error notsup Actual os:  linux
npm error notsup Valid cpu:  arm64
npm error notsup Actual cpu: x64
```

`cleanroom` classifies this by matching narrowly on npm's own
`EBADPLATFORM` code / "Unsupported platform" / the `Valid os:`/`Valid cpu:`
report / the `wanted {"os":...} (current:` shape -- and *only* those. A
generic `npm ci` failure with no platform dimension (a typo'd package
name -> `E404`, an out-of-sync lockfile -> `EUSAGE`, a missing lockfile ->
`EUSAGE`) is deliberately left `unclassified`: none of those carry a
platform signature, and guessing one would be exactly the overclaim this
tool exists to catch. See `tests/test_classify.py` and
`tests/test_gates.py::test_g1_*_npm_failure_without_platform_signature_is_unclassified`
for the regression coverage on that boundary.
