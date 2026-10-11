# WirePlumber default-node test sources

These files are byte-identical sources from the WirePlumber `0.5.18` tag:
<https://github.com/PipeWire/wireplumber/tree/0.5.18/src/scripts/default-nodes>.
`node-utils.lua` is from `src/scripts/lib`. Their MIT notices and upstream
LICENSE are retained. `SHA256SUMS` pins the imported bytes. The installed
J713 system's rescan and best-default scripts were also inspected and matched
these source bodies.

The regression invokes these actual hooks with substituted metadata, event,
JSON and object-manager boundaries. It does not launch PipeWire, change saved
user state or open a PCM. Smart-filter classification is fixed false, matching
the protected graph's unset `filter.smart`; device route availability is a
fixture boundary. Synthetic raw priority 1000 versus protected priority 0
exercises the default ranking mechanism; it is not a captured live raw priority.
