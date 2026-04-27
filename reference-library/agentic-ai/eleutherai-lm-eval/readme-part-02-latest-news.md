## Latest News 📣
- [2025/12] **CLI refactored** with subcommands (`run`, `ls`, `validate`) and YAML config file support via `--config`. See the [CLI Reference](./docs/interface.md) and [Configuration Guide](./docs/config_files.md).
- [2025/12] **Lighter install**: Base package no longer includes `transformers`/`torch`. Install model backends separately: `pip install lm_eval[hf]`, `lm_eval[vllm]`, etc.
- [2025/07] Added `think_end_token` arg to `hf` (token/str), `vllm` and `sglang` (str) for stripping CoT reasoning traces from models that support it.
- [2025/03] Added support for steering HF models!
- [2025/02] Added [SGLang](https://docs.sglang.ai/) support!
- [2024/09] We are prototyping allowing users of LM Evaluation Harness to create and evaluate on text+image multimodal input, text output tasks, and have just added the `hf-multimodal` and `vllm-vlm` model types and `mmmu` task as a prototype feature. We welcome users to try out this in-progress feature and stress-test it for themselves, and suggest they check out [`lmms-eval`](https://github.com/EvolvingLMMs-Lab/lmms-eval), a wonderful project originally forking off of the lm-evaluation-harness, for a broader range of multimodal tasks, models, and features.
- [2024/07] [API model](docs/API_guide.md) support has been updated and refactored, introducing support for batched and async requests, and making it significantly easier to customize and use for your own purposes. **To run Llama 405B, we recommend using VLLM's OpenAI-compliant API to host the model, and use the `local-completions` model type to evaluate the model.**
- [2024/07] New Open LLM Leaderboard tasks have been added ! You can find them under the [leaderboard](lm_eval/tasks/leaderboard/README.md) task group.

---