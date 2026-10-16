# Where the time goes: NVIDIA GeForce RTX 5070 Ti, swift-1.5-iq3_s, 2026-10-15

Measured by `tools/strata_profile.py` through the HTTP API with `STRATA_PREFILL_TIMING=1` and `STRATA_DECODE_TIMING=1` in the engine's environment; the figures are the engine's own lines.

- GPU: NVIDIA GeForce RTX 5070 Ti, 16 GiB VRAM, driver 595.99.02
- CPU: AMD Ryzen 7 5800X3D 8-Core Processor, 8 cores, 76 GiB RAM
- OS: Linux 7.0.0-38-generic; Strata 0.1.44.1 (fa12b98c), engine 0.1.44.1
- Model: swift-1.5-iq3_s (IQ3_S), context 262,144
- Launch: `strata --pack swift-iq3_s --native Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf --ple-gguf Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf --expert-profile expert-profile.bin --expert-cache auto --prefill auto --spec 4 --spec-min-p 0.70 --mtp rt --max-context 262144 --kv int8 --kv-resident 32768 --pcie-frac 0.35 --adapt-every 1 --adapt-swaps 160 --adapt-decay 0.97`

## Prompt reads (the GPU timeline by phase)

| Read | Tokens | Prompt tok/s | GPU ms | experts over PCIe | dequant | expert GEMM | attention | GDN | other | Largest phases |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2K fresh | 2,023 | 1,765 | 979 | 21% | 15% | 25% | 9% | 22% | 9% | dequant 15%, wait copy 14%, gdn 12% |
| 16K fresh | 16,058 | 2,948 | 5,163 | 6% | 7% | 30% | 18% | 25% | 14% | gemm gate/up 14%, gdn 12%, hc read 10% |
| 32K (long) fresh | 32,049 | 3,015 | 10,357 | 6% | 7% | 27% | 20% | 25% | 15% | gemm gate/up 13%, gdn 12%, hc read 10% |

"experts over PCIe" is the time the card waited for expert copies (wait copy, gather, host grouping); "dequant" their expansion on the card; "expert GEMM" gate/up, down and combine; "attention" the QSA projections, indexer, selection, attention and KV staging; "GDN" the linear-attention layers; "other" the hc read, router, PLE and embeddings.

## Decode windows (ms per window)

| Request | Context | Decode tok/s | Hit rate | PCIe share | ms/window | GPU | CPU experts | draft | adapt wait | other | tokens/window | CPU experts per layer-window | their entries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chat | 44 | 79.5 | 80% | 6% | 27.7 | 9.3 | 13.3 | 1.7 | 2.5 | 0.8 | 2.27 | 3.6 | 4.7 |
| long context | 32,049 | 96.9 | 88% | 3% | 22.8 | 10.1 | 8.5 | 2.2 | 1.1 | 0.9 | 2.21 | 2.4 | 2.9 |
| agent turns | 8,721 | 85.7 | 81% | 6% | 29.4 | 9.9 | 14.6 | 1.9 | 2.1 | 0.8 | 2.27 | 3.9 | 5.3 |

"GPU" is the host's wait for the card to reach the end of the window; "CPU experts" the experts the CPU pool computed because they were not in VRAM (plan, activation quantization, jobs and the compute); "draft" the draft layer; "adapt wait" the adaptive tier's swaps; "other" commit, stage and what the line does not itemize. "Hit rate" is the VRAM share of the experts looked up; "PCIe share" the share of all routed experts the card read over PCIe instead (not in the hit rate). The tokens per second were measured with the timing switches on: for speeds, run tools/strata_bench.py.

KV streaming (this request's block reads): long context 96% of 739,440 reads from VRAM, 110 MiB from RAM

## Agent session, turn by turn

| Turn | Prompt tokens | Reused | Read | Prompt tok/s | Decode tok/s | Hit rate | ms/window | GPU | CPU experts |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 609 | 0 | 609 | 485 | 46 | 60% | 40.1 | 9.2 | 22.4 |
| 1 | 3,744 | 623 | 3,121 | 1,447 | 70 | 71% | 38.0 | 10.2 | 21.2 |
| 2 | 5,277 | 3,808 | 1,469 | 1,006 | 73 | 80% | 28.3 | 9.7 | 14.0 |
| 3 | 8,721 | 5,270 | 3,451 | 1,593 | 86 | 84% | 21.8 | 9.3 | 8.7 |
| 4 | 11,625 | 8,763 | 2,862 | 1,401 | 88 | 87% | 25.6 | 10.3 | 11.2 |
| 5 | 14,541 | 11,642 | 2,899 | 1,437 | 89 | 81% | 31.7 | 10.3 | 16.4 |

## Findings

- The 2K read (2,023 tokens, 1,765 tok/s) is compute-bound: expert GEMM 25%, attention 9%, GDN 22% of its GPU time; the experts' copies and dequant 36%.
- The 16K read (16,058 tokens, 2,948 tok/s) is compute-bound: expert GEMM 30%, attention 18%, GDN 25% of its GPU time; the experts' copies and dequant 13%.
- The 32K (long) read (32,049 tokens, 3,015 tok/s) is compute-bound: expert GEMM 27%, attention 20%, GDN 25% of its GPU time; the experts' copies and dequant 13%.
- chat (44 tokens of context, 80 tok/s, expert cache hit rate 80%): of a 27.7 ms window the card takes 9.3 ms and the experts computed on the CPU 13.3 ms (3.6 experts, 4.7 entries per layer-window): the misses of the VRAM expert cache take the larger share of the window.
- long context (32,049 tokens of context, 97 tok/s, expert cache hit rate 88%): of a 22.8 ms window the card takes 10.1 ms, the experts computed on the CPU 8.5 ms, the draft 2.2 ms: the card takes the larger share of the window.
- agent turns (8,721 tokens of context, 86 tok/s, expert cache hit rate 81%): of a 29.4 ms window the card takes 9.9 ms and the experts computed on the CPU 14.6 ms (3.9 experts, 5.3 entries per layer-window): the misses of the VRAM expert cache take the larger share of the window.
- Agent turns: the expert cache hit rate per turn runs from 60% (turn 0) to 87% (turn 4).
- Agent turns: each follow-up turn read 1,469-3,451 tokens after what the conversation cache reused.
- A decode whose larger share is the experts computed on the CPU follows the expert cache's hit rate: the settings on that path are the cache's slots (VRAM: --max-context, --kv-resident, --vram-reserve-mib; docs/DETAILS.md, "The expert cache size is a budget"), the PCIe share and the adaptive tier (setup --calibrate measures both on this PC) and a saved expert profile (expert_profile_save).
