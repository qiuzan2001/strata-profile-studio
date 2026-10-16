# Where the time goes: NVIDIA GeForce RTX 5070 Ti, swift-1.5-iq3_s, 2026-10-15

Measured by `tools/strata_profile.py` through the HTTP API with `STRATA_PREFILL_TIMING=1` and `STRATA_DECODE_TIMING=1` in the engine's environment; the figures are the engine's own lines.

- GPU: NVIDIA GeForce RTX 5070 Ti, 16 GiB VRAM, driver 595.99.02
- CPU: AMD Ryzen 7 5800X3D 8-Core Processor, 8 cores, 76 GiB RAM
- OS: Linux 7.0.0-38-generic; Strata 0.1.44.1 (469ceb71), engine 0.1.44.1
- Model: swift-1.5-iq3_s (IQ3_S), context 262,144
- Launch: `strata --pack swift-iq3_s --native Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf --ple-gguf Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf --expert-profile expert-profile.bin --expert-cache auto --prefill auto --spec 4 --spec-min-p 0.70 --mtp rt --max-context 262144 --kv int8 --kv-resident 32768 --pcie-frac 0.35 --adapt-every 1 --adapt-swaps 160 --adapt-decay 0.97`

## Prompt reads (the GPU timeline by phase)

| Read | Tokens | Prompt tok/s | GPU ms | experts over PCIe | dequant | expert GEMM | attention | GDN | other | Largest phases |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2K fresh | 2,023 | 1,758 | 981 | 20% | 16% | 24% | 9% | 22% | 9% | dequant 16%, wait copy 14%, gdn 12% |
| 16K fresh | 16,058 | 2,944 | 5,170 | 5% | 7% | 30% | 18% | 26% | 14% | gemm gate/up 14%, gdn 12%, hc read 10% |
| 32K (long) fresh | 32,049 | 3,012 | 10,364 | 6% | 7% | 27% | 20% | 25% | 15% | gemm gate/up 13%, gdn 12%, hc read 10% |

"experts over PCIe" is the time the card waited for expert copies (wait copy, gather, host grouping); "dequant" their expansion on the card; "expert GEMM" gate/up, down and combine; "attention" the QSA projections, indexer, selection, attention and KV staging; "GDN" the linear-attention layers; "other" the hc read, router, PLE and embeddings.

## Decode windows (ms per window)

| Request | Context | Decode tok/s | Hit rate | PCIe share | ms/window | GPU | CPU experts | draft | adapt wait | other | tokens/window | CPU experts per layer-window | their entries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chat | 44 | 80.2 | 79% | 6% | 29.1 | 9.5 | 14.3 | 1.8 | 2.6 | 0.8 | 2.40 | 3.9 | 5.1 |
| long context | 32,049 | 99.5 | 87% | 4% | 23.4 | 10.3 | 9.0 | 2.3 | 1.0 | 0.8 | 2.33 | 2.5 | 3.1 |
| agent turns | 8,732 | 79.2 | 82% | 6% | 29.7 | 10.0 | 14.6 | 1.9 | 2.2 | 0.9 | 2.48 | 4.0 | 5.4 |

"GPU" is the host's wait for the card to reach the end of the window; "CPU experts" the experts the CPU pool computed because they were not in VRAM (plan, activation quantization, jobs and the compute); "draft" the draft layer; "adapt wait" the adaptive tier's swaps; "other" commit, stage and what the line does not itemize. "Hit rate" is the VRAM share of the experts looked up; "PCIe share" the share of all routed experts the card read over PCIe instead (not in the hit rate). The tokens per second were measured with the timing switches on: for speeds, run tools/strata_bench.py.

KV streaming (this request's block reads): long context 96% of 739,476 reads from VRAM, 115 MiB from RAM

## Agent session, turn by turn

| Turn | Prompt tokens | Reused | Read | Prompt tok/s | Decode tok/s | Hit rate | ms/window | GPU | CPU experts |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 609 | 0 | 609 | 482 | 55 | 64% | 39.9 | 10.2 | 22.5 |
| 1 | 3,755 | 633 | 3,122 | 1,439 | 76 | 67% | 36.6 | 9.9 | 20.8 |
| 2 | 5,288 | 3,818 | 1,470 | 1,012 | 78 | 79% | 29.4 | 9.8 | 14.6 |
| 3 | 8,732 | 5,351 | 3,381 | 1,563 | 94 | 85% | 25.1 | 10.1 | 10.5 |
| 4 | 11,658 | 8,795 | 2,863 | 1,395 | 117 | 87% | 25.7 | 10.7 | 10.8 |
| 5 | 14,574 | 11,675 | 2,899 | 1,433 | 79 | 82% | 29.5 | 10.6 | 14.2 |

## Findings

- The 2K read (2,023 tokens, 1,758 tok/s) is compute-bound: expert GEMM 24%, attention 9%, GDN 22% of its GPU time; the experts' copies and dequant 36%.
- The 16K read (16,058 tokens, 2,944 tok/s) is compute-bound: expert GEMM 30%, attention 18%, GDN 26% of its GPU time; the experts' copies and dequant 13%.
- The 32K (long) read (32,049 tokens, 3,012 tok/s) is compute-bound: expert GEMM 27%, attention 20%, GDN 25% of its GPU time; the experts' copies and dequant 13%.
- chat (44 tokens of context, 80 tok/s, expert cache hit rate 79%): of a 29.1 ms window the card takes 9.5 ms and the experts computed on the CPU 14.3 ms (3.9 experts, 5.1 entries per layer-window): the misses of the VRAM expert cache take the larger share of the window.
- long context (32,049 tokens of context, 100 tok/s, expert cache hit rate 87%): of a 23.4 ms window the card takes 10.3 ms, the experts computed on the CPU 9.0 ms, the draft 2.3 ms: the card takes the larger share of the window.
- agent turns (8,732 tokens of context, 79 tok/s, expert cache hit rate 82%): of a 29.7 ms window the card takes 10.0 ms and the experts computed on the CPU 14.6 ms (4.0 experts, 5.4 entries per layer-window): the misses of the VRAM expert cache take the larger share of the window.
- Agent turns: the expert cache hit rate per turn runs from 64% (turn 0) to 87% (turn 4).
- Agent turns: each follow-up turn read 1,470-3,381 tokens after what the conversation cache reused.
- A decode whose larger share is the experts computed on the CPU follows the expert cache's hit rate: the settings on that path are the cache's slots (VRAM: --max-context, --kv-resident, --vram-reserve-mib; docs/DETAILS.md, "The expert cache size is a budget"), the PCIe share and the adaptive tier (setup --calibrate measures both on this PC) and a saved expert profile (expert_profile_save).
