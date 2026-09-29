# Step 5 dataset audit

Status: **PASS_WITH_EXCLUSIONS**

10,574 rows, 10 exclusions, 20 designs, 25 features.

| Design | Split | Common FFs | Rows | Excluded | Coverage |
|---|---|---:|---:|---:|---:|
| picorv32 | train | 1466 | 1456 | 10 | 99.32% |
| femtorv_quark | train | 1215 | 1215 | 0 | 100.00% |
| osu_riscv32i | validation | 1056 | 1056 | 0 | 100.00% |
| serv | test | 1190 | 1190 | 0 | 100.00% |
| fastfir8 | train | 505 | 505 | 0 | 100.00% |
| boxcar16 | train | 354 | 354 | 0 | 100.00% |
| divider32 | validation | 142 | 142 | 0 | 100.00% |
| seqcordic | test | 152 | 152 | 0 | 100.00% |
| aes128 | train | 562 | 562 | 0 | 100.00% |
| sha256 | train | 1034 | 1034 | 0 | 100.00% |
| chacha | validation | 1100 | 1100 | 0 | 100.00% |
| crc32 | test | 64 | 64 | 0 | 100.00% |
| uart_tx | train | 35 | 35 | 0 | 100.00% |
| uart_rx | train | 44 | 44 | 0 | 100.00% |
| simple_spi | train | 132 | 132 | 0 | 100.00% |
| i2c_master | validation | 72 | 72 | 0 | 100.00% |
| axis_fifo16 | train | 593 | 593 | 0 | 100.00% |
| axis_arb_mux4 | train | 210 | 210 | 0 | 100.00% |
| axis_skid32 | train | 67 | 67 | 0 | 100.00% |
| sfifo16 | test | 591 | 591 | 0 | 100.00% |

Exclusions: `{"no_public_rtl_q_alias": 10}`.

All retained rows passed the recorded structural, timing, units, and split checks.
No prediction scores were inspected. Missing timings were not converted into zero labels.

## Limits

- single SOG representation and one critical BOG path
- no sampled-path max-loss or representation ensemble
- no formal equivalence proof for the full 20-design set
- post-synthesis labels with zero explicit wire RC
- common-lowered register population, not a census of every pre-optimization RTL declaration
- no model training or predictive evaluation in Step 5
