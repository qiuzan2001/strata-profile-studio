# Where the time goes: NVIDIA GeForce RTX 5070 Ti, swift-1.5-iq3_s, 2026-10-15

Measured by `tools/strata_profile.py` through the HTTP API with `STRATA_PREFILL_TIMING=1` and `STRATA_DECODE_TIMING=1` in the engine's environment; the figures are the engine's own lines.

- GPU: NVIDIA GeForce RTX 5070 Ti, 16 GiB VRAM, driver 595.99.02
- CPU: AMD Ryzen 7 5800X3D 8-Core Processor, 8 cores, 76 GiB RAM
- OS: Linux 7.0.0-38-generic; Strata 0.1.44.1 (469ceb71), engine 0.1.44.1
- Model: swift-1.5-iq3_s (IQ3_S), context 262,144
- Launch: `strata --pack swift-iq3_s --native Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf --ple-gguf Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf --expert-profile expert-profile.bin --expert-cache auto --prefill auto --spec 4 --mtp rt --max-context 262144 --kv int8 --kv-resident 32768 --spec-min-p 0.70 --adapt-every 1 --adapt-swaps 160 --adapt-decay 0.97 --pcie-frac 0.35`

## Prompt reads (the GPU timeline by phase)

| Read | Tokens | Prompt tok/s | GPU ms | experts over PCIe | dequant | expert GEMM | attention | GDN | other | Largest phases |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2K fresh | 2,023 | 1,784 | 954 | 19% | 16% | 25% | 9% | 22% | 9% | dequant 16%, wait copy 14%, gdn 12% |
| 16K fresh | 16,058 | 2,981 | 5,091 | 5% | 7% | 28% | 19% | 26% | 15% | gemm gate/up 14%, gdn 12%, hc read 10% |
| 32K (long) fresh | 32,049 | 3,010 | 10,364 | 6% | 7% | 27% | 21% | 25% | 15% | gemm gate/up 13%, gdn 12%, hc read 10% |

"experts over PCIe" is the time the card waited for expert copies (wait copy, gather, host grouping); "dequant" their expansion on the card; "expert GEMM" gate/up, down and combine; "attention" the QSA projections, indexer, selection, attention and KV staging; "GDN" the linear-attention layers; "other" the hc read, router, PLE and embeddings.

## Decode windows (ms per window)

| Request | Context | Decode tok/s | Hit rate | PCIe share | ms/window | GPU | CPU experts | draft | adapt wait | other | tokens/window | CPU experts per layer-window | their entries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chat | 44 | 76.3 | 80% | 6% | 30.1 | 9.7 | 15.4 | 2.0 | 2.3 | 0.8 | 2.35 | 4.2 | 5.7 |
| long context | 32,049 | 92.6 | 87% | 4% | 24.7 | 10.4 | 10.1 | 2.4 | 1.1 | 0.8 | 2.29 | 2.9 | 3.6 |
| agent turns | 8,732 | 78.9 | 82% | 6% | 30.7 | 10.3 | 15.6 | 2.1 | 1.8 | 0.8 | 2.39 | 4.1 | 5.6 |

"GPU" is the host's wait for the card to reach the end of the window; "CPU experts" the experts the CPU pool computed because they were not in VRAM (plan, activation quantization, jobs and the compute); "draft" the draft layer; "adapt wait" the adaptive tier's swaps; "other" commit, stage and what the line does not itemize. "Hit rate" is the VRAM share of the experts looked up; "PCIe share" the share of all routed experts the card read over PCIe instead (not in the hit rate). The tokens per second were measured with the timing switches on: for speeds, run tools/strata_bench.py.

KV streaming (this request's block reads): long context 96% of 844,212 reads from VRAM, 120 MiB from RAM

## Agent session, turn by turn

| Turn | Prompt tokens | Reused | Read | Prompt tok/s | Decode tok/s | Hit rate | ms/window | GPU | CPU experts |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 609 | 0 | 609 | 489 | 48 | 63% | 45.5 | 9.8 | 28.7 |
| 1 | 3,755 | 633 | 3,122 | 1,556 | 76 | 68% | 38.4 | 10.1 | 22.3 |
| 2 | 5,288 | 3,819 | 1,469 | 1,054 | 70 | 82% | 30.3 | 10.3 | 15.2 |
| 3 | 8,732 | 5,352 | 3,380 | 1,632 | 79 | 85% | 26.2 | 10.2 | 11.6 |
| 4 | 11,658 | 8,796 | 2,862 | 1,478 | 89 | 86% | 27.9 | 10.8 | 12.9 |
| 5 | 14,608 | 11,709 | 2,899 | 1,477 | 79 | 79% | 32.3 | 10.4 | 17.2 |

## Findings

- The 2K read (2,023 tokens, 1,784 tok/s) is compute-bound: expert GEMM 25%, attention 9%, GDN 22% of its GPU time; the experts' copies and dequant 35%.
- The 16K read (16,058 tokens, 2,981 tok/s) is compute-bound: expert GEMM 28%, attention 19%, GDN 26% of its GPU time; the experts' copies and dequant 13%.
- The 32K (long) read (32,049 tokens, 3,010 tok/s) is compute-bound: expert GEMM 27%, attention 21%, GDN 25% of its GPU time; the experts' copies and dequant 12%.
- chat (44 tokens of context, 76 tok/s, expert cache hit rate 80%): of a 30.1 ms window the card takes 9.7 ms and the experts computed on the CPU 15.4 ms (4.2 experts, 5.7 entries per layer-window): the misses of the VRAM expert cache take the larger share of the window.
- long context (32,049 tokens of context, 93 tok/s, expert cache hit rate 87%): of a 24.7 ms window the card takes 10.4 ms, the experts computed on the CPU 10.1 ms, the draft 2.4 ms: the card takes the larger share of the window.
- agent turns (8,732 tokens of context, 79 tok/s, expert cache hit rate 82%): of a 30.7 ms window the card takes 10.3 ms and the experts computed on the CPU 15.6 ms (4.1 experts, 5.6 entries per layer-window): the misses of the VRAM expert cache take the larger share of the window.
- Agent turns: the expert cache hit rate per turn runs from 63% (turn 0) to 86% (turn 4).
- Agent turns: each follow-up turn read 1,469-3,380 tokens after what the conversation cache reused.
- A decode whose larger share is the experts computed on the CPU follows the expert cache's hit rate: the settings on that path are the cache's slots (VRAM: --max-context, --kv-resident, --vram-reserve-mib; docs/DETAILS.md, "The expert cache size is a budget"), the PCIe share and the adaptive tier (setup --calibrate measures both on this PC) and a saved expert profile (expert_profile_save).
