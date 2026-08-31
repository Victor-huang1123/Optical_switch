# Waksman N=10 C pre-remediation routing audit

These files preserve the final failed routing artifact from the three-rung
`max_astar_pops` ladder before the same-net whole-net reroute remediation was
enabled.

- 30,000 pops: genuine pop exhaustion on nets I9→O9 and I3→O3.
- 100,000 pops: I4→O4 failed at
  `waksman_10x10_s4_w0_4.drop→waksman_10x10_s5_w4_6.in` with
  `pops:92`, `window:0`, and `same_net:63`.
- 300,000 pops: identical I4→O4 hop and counters. The archived routing files
  are from this rung.

Root cause: same-net self-interference in the bounded multi-hop fallback. Its
own earlier committed segments, including `(968,448)→(968,416)` and
`(993,448)→(968,448)`, boxed in the remaining hop. The hard same-net no-touch
pruning then exhausted the small reachable search after 92 pops. Soft corridor
guides did not prevent this failure, so further pop escalation could not fix it.

## Archived SHA-256 checksums

```text
fa9636ce5d9a435a8554c65c1d74b54c4a5181aa8373efd3cbdc1d12dc15d074  config.json
b50d56cce96efeae22e844884b933d13a17ddba2df07269c6320de7925157eb7  fabric_drc_violations.csv
ce4d567e4bf5b19ca05c128f44d721ab103250b7ee78530aec3f69ae9d2b5c8f  fabric_edges.csv
e701b98e0f5aebb9dfdfe12b409c20f3f7cabc32dce33fe5b28b70c1a79dc185  fabric_routing_summary.json
5c69877ecc1c9eb503fb1e47f7b68ec9b0c075846e8ce074cbb10d152600f365  fixed_fabric_layout.png
a6a8e32be32d04ae0c0620ee815aadaae0c68a5f5c3b56129012b74c0f575e93  routing_result.pkl
```
