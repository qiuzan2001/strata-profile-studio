# Where the time goes: NVIDIA GeForce RTX 5070 Ti, swift-1.5-iq3_s, 2026-10-15

Measured by `tools/strata_profile.py` through the HTTP API with `STRATA_PREFILL_TIMING=1` and `STRATA_DECODE_TIMING=1` in the engine's environment; the figures are the engine's own lines.

- GPU: NVIDIA GeForce RTX 5070 Ti, 16 GiB VRAM, driver 595.99.02
- CPU: AMD Ryzen 7 5800X3D 8-Core Processor, 8 cores, 76 GiB RAM
- OS: Linux 7.0.0-38-generic; Strata 0.1.44.1 (469ceb71), engine 0.1.44.1
- Model: swift-1.5-iq3_s (IQ3_S), context 262,144
- Launch: `strata --pack swift-iq3_s --native Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf --ple-gguf Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf --expert-profile expert-profile.bin --expert-cache auto --prefill auto --spec 4 --mtp rt --max-context 262144 --kv int8 --kv-resident 32768 --spec-min-p 0.5 --adapt-every 1 --adapt-swaps 80 --adapt-decay 0.97`

## Prompt reads (the GPU timeline by phase)

| Read | Tokens | Prompt tok/s | GPU ms | experts over PCIe | dequant | expert GEMM | attention | GDN | other | Largest phases |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2K fresh | 2,023 | 1,761 | 976 | 20% | 15% | 25% | 9% | 22% | 9% | dequant 15%, wait copy 14%, gemm gate/up 12% |
| 16K fresh | 16,058 | 2,973 | 5,113 | 5% | 8% | 30% | 18% | 26% | 14% | gemm gate/up 14%, gdn 12%, hc read 10% |
| 32K (long) fresh | 32,049 | 3,029 | 10,303 | 6% | 7% | 27% | 20% | 25% | 15% | gemm gate/up 13%, gdn 12%, hc read 10% |

"experts over PCIe" is the time the card waited for expert copies (wait copy, gather, host grouping); "dequant" their expansion on the card; "expert GEMM" gate/up, down and combine; "attention" the QSA projections, indexer, selection, attention and KV staging; "GDN" the linear-attention layers; "other" the hc read, router, PLE and embeddings.

## Decode windows (ms per window)

| Request | Context | Decode tok/s | Hit rate | PCIe share | ms/window | GPU | CPU experts | draft | adapt wait | other | tokens/window | CPU experts per layer-window | their entries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chat | 44 | 80.0 | 80% | 7% | 30.8 | 10.4 | 15.1 | 2.0 | 2.5 | 0.8 | 2.45 | 3.9 | 5.5 |
| long context | 32,049 | 93.9 | 87% | 3% | 22.3 | 10.1 | 8.2 | 2.2 | 0.9 | 0.8 | 2.10 | 2.4 | 3.0 |
| agent turns | 8,733 | 86.1 | 81% | 6% | 31.2 | 10.2 | 16.2 | 2.1 | 1.9 | 0.9 | 2.35 | 4.1 | 5.5 |

"GPU" is the host's wait for the card to reach the end of the window; "CPU experts" the experts the CPU pool computed because they were not in VRAM (plan, activation quantization, jobs and the compute); "draft" the draft layer; "adapt wait" the adaptive tier's swaps; "other" commit, stage and what the line does not itemize. "Hit rate" is the VRAM share of the experts looked up; "PCIe share" the share of all routed experts the card read over PCIe instead (not in the hit rate). The tokens per second were measured with the timing switches on: for speeds, run tools/strata_bench.py.

KV streaming (this request's block reads): long context 97% of 801,096 reads from VRAM, 113 MiB from RAM

## Agent session, turn by turn

| Turn | Prompt tokens | Reused | Read | Prompt tok/s | Decode tok/s | Hit rate | ms/window | GPU | CPU experts |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 609 | 0 | 609 | 487 | 51 | 65% | 40.8 | 9.8 | 24.1 |
| 1 | 3,756 | 634 | 3,122 | 1,442 | 64 | 72% | 38.6 | 10.1 | 22.5 |
| 2 | 5,289 | 3,819 | 1,470 | 1,011 | 70 | 81% | 32.8 | 10.0 | 17.9 |
| 3 | 8,733 | 5,282 | 3,451 | 1,577 | 86 | 87% | 24.7 | 10.4 | 10.1 |
| 4 | 11,659 | 8,726 | 2,933 | 1,412 | 89 | 83% | 28.7 | 10.4 | 14.1 |
| 5 | 14,585 | 11,686 | 2,899 | 1,435 | 86 | 80% | 30.2 | 10.4 | 15.4 |

## Findings

- The 2K read (2,023 tokens, 1,761 tok/s) is compute-bound: expert GEMM 25%, attention 9%, GDN 22% of its GPU time; the experts' copies and dequant 36%.
- The 16K read (16,058 tokens, 2,973 tok/s) is compute-bound: expert GEMM 30%, attention 18%, GDN 26% of its GPU time; the experts' copies and dequant 13%.
- The 32K (long) read (32,049 tokens, 3,029 tok/s) is compute-bound: expert GEMM 27%, attention 20%, GDN 25% of its GPU time; the experts' copies and dequant 13%.
- chat (44 tokens of context, 80 tok/s, expert cache hit rate 80%): of a 30.8 ms window the card takes 10.4 ms and the experts computed on the CPU 15.1 ms (3.9 experts, 5.5 entries per layer-window): the misses of the VRAM expert cache take the larger share of the window.
- long context (32,049 tokens of context, 94 tok/s, expert cache hit rate 87%): of a 22.3 ms window the card takes 10.1 ms, the experts computed on the CPU 8.2 ms, the draft 2.2 ms: the card takes the larger share of the window.
- agent turns (8,733 tokens of context, 86 tok/s, expert cache hit rate 81%): of a 31.2 ms window the card takes 10.2 ms and the experts computed on the CPU 16.2 ms (4.1 experts, 5.5 entries per layer-window): the misses of the VRAM expert cache take the larger share of the window.
- Agent turns: the expert cache hit rate per turn runs from 65% (turn 0) to 87% (turn 3).
- Agent turns: each follow-up turn read 1,470-3,451 tokens after what the conversation cache reused.
- A decode whose larger share is the experts computed on the CPU follows the expert cache's hit rate: the settings on that path are the cache's slots (VRAM: --max-context, --kv-resident, --vram-reserve-mib; docs/DETAILS.md, "The expert cache size is a budget"), the PCIe share and the adaptive tier (setup --calibrate measures both on this PC) and a saved expert profile (expert_profile_save).
