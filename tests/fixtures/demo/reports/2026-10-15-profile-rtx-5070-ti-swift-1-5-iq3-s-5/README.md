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
| 2K fresh | 2,023 | 1,804 | 944 | 19% | 15% | 24% | 9% | 22% | 9% | dequant 15%, wait copy 15%, gemm gate/up 12% |
| 16K fresh | 16,058 | 3,027 | 5,018 | 5% | 8% | 28% | 18% | 26% | 14% | gemm gate/up 14%, gdn 12%, hc read 10% |
| 32K (long) fresh | 32,049 | 3,031 | 10,294 | 6% | 7% | 27% | 20% | 25% | 15% | gemm gate/up 13%, gdn 12%, hc read 10% |

"experts over PCIe" is the time the card waited for expert copies (wait copy, gather, host grouping); "dequant" their expansion on the card; "expert GEMM" gate/up, down and combine; "attention" the QSA projections, indexer, selection, attention and KV staging; "GDN" the linear-attention layers; "other" the hc read, router, PLE and embeddings.

## Decode windows (ms per window)

| Request | Context | Decode tok/s | Hit rate | PCIe share | ms/window | GPU | CPU experts | draft | adapt wait | other | tokens/window | CPU experts per layer-window | their entries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chat | 44 | 79.7 | 79% | 6% | 27.7 | 9.3 | 13.5 | 1.7 | 2.4 | 0.8 | 2.27 | 3.7 | 4.9 |
| long context | 32,049 | 96.5 | 87% | 4% | 22.5 | 10.0 | 8.4 | 2.2 | 1.0 | 0.8 | 2.17 | 2.5 | 2.9 |
| agent turns | 8,733 | 90.8 | 81% | 6% | 28.1 | 10.0 | 13.5 | 2.0 | 1.9 | 0.8 | 2.33 | 3.7 | 4.9 |

"GPU" is the host's wait for the card to reach the end of the window; "CPU experts" the experts the CPU pool computed because they were not in VRAM (plan, activation quantization, jobs and the compute); "draft" the draft layer; "adapt wait" the adaptive tier's swaps; "other" commit, stage and what the line does not itemize. "Hit rate" is the VRAM share of the experts looked up; "PCIe share" the share of all routed experts the card read over PCIe instead (not in the hit rate). The tokens per second were measured with the timing switches on: for speeds, run tools/strata_bench.py.

KV streaming (this request's block reads): long context 96% of 739,440 reads from VRAM, 119 MiB from RAM

## Agent session, turn by turn

| Turn | Prompt tokens | Reused | Read | Prompt tok/s | Decode tok/s | Hit rate | ms/window | GPU | CPU experts |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 609 | 0 | 609 | 486 | 53 | 64% | 39.4 | 9.5 | 22.4 |
| 1 | 3,756 | 633 | 3,123 | 1,548 | 70 | 72% | 32.5 | 9.8 | 17.1 |
| 2 | 5,289 | 3,819 | 1,470 | 1,054 | 73 | 81% | 31.1 | 10.2 | 15.8 |
| 3 | 8,733 | 5,282 | 3,451 | 1,653 | 92 | 84% | 23.1 | 9.7 | 9.5 |
| 4 | 11,659 | 8,796 | 2,863 | 1,482 | 91 | 86% | 25.5 | 10.3 | 11.2 |
| 5 | 14,615 | 11,716 | 2,899 | 1,483 | 94 | 81% | 28.5 | 10.1 | 13.9 |

## Findings

- The 2K read (2,023 tokens, 1,804 tok/s) is compute-bound: expert GEMM 24%, attention 9%, GDN 22% of its GPU time; the experts' copies and dequant 35%.
- The 16K read (16,058 tokens, 3,027 tok/s) is compute-bound: expert GEMM 28%, attention 18%, GDN 26% of its GPU time; the experts' copies and dequant 13%.
- The 32K (long) read (32,049 tokens, 3,031 tok/s) is compute-bound: expert GEMM 27%, attention 20%, GDN 25% of its GPU time; the experts' copies and dequant 13%.
- chat (44 tokens of context, 80 tok/s, expert cache hit rate 79%): of a 27.7 ms window the card takes 9.3 ms and the experts computed on the CPU 13.5 ms (3.7 experts, 4.9 entries per layer-window): the misses of the VRAM expert cache take the larger share of the window.
- long context (32,049 tokens of context, 96 tok/s, expert cache hit rate 87%): of a 22.5 ms window the card takes 10.0 ms, the experts computed on the CPU 8.4 ms, the draft 2.2 ms: the card takes the larger share of the window.
- agent turns (8,733 tokens of context, 91 tok/s, expert cache hit rate 81%): of a 28.1 ms window the card takes 10.0 ms and the experts computed on the CPU 13.5 ms (3.7 experts, 4.9 entries per layer-window): the misses of the VRAM expert cache take the larger share of the window.
- Agent turns: the expert cache hit rate per turn runs from 64% (turn 0) to 86% (turn 4).
- Agent turns: each follow-up turn read 1,470-3,451 tokens after what the conversation cache reused.
- A decode whose larger share is the experts computed on the CPU follows the expert cache's hit rate: the settings on that path are the cache's slots (VRAM: --max-context, --kv-resident, --vram-reserve-mib; docs/DETAILS.md, "The expert cache size is a budget"), the PCIe share and the adaptive tier (setup --calibrate measures both on this PC) and a saved expert profile (expert_profile_save).
