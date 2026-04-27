## Saving & Caching Results

To save evaluation results provide an `--output_path`. We also support logging model responses with the `--log_samples` flag for post-hoc analysis.

> [!TIP]
> Use `--use_cache <DIR>` to cache evaluation results and skip previously evaluated samples when resuming runs of the same (model, task) pairs. Note that caching is rank-dependent, so restart with the same GPU count if interrupted. You can also use --cache_requests to save dataset preprocessing steps for faster evaluation resumption.

To push results and samples to the Hugging Face Hub, first ensure an access token with write access is set in the `HF_TOKEN` environment variable. Then, use the `--hf_hub_log_args` flag to specify the organization, repository name, repository visibility, and whether to push results and samples to the Hub - [example dataset on the  HF Hub](https://huggingface.co/datasets/KonradSzafer/lm-eval-results-demo). For instance:

```bash
lm_eval --model hf \
    --model_args pretrained=model-name-or-path,autogptq=model.safetensors,gptq_use_triton=True \
    --tasks hellaswag \
    --log_samples \
    --output_path results \
    --hf_hub_log_args hub_results_org=EleutherAI,hub_repo_name=lm-eval-results,push_results_to_hub=True,push_samples_to_hub=True,public_repo=False \
```

This allows you to easily download the results and samples from the Hub, using:

```python
from datasets import load_dataset

load_dataset("EleutherAI/lm-eval-results-private", "hellaswag", "latest")
```

For a full list of supported arguments, check out the [interface](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/interface.md) guide in our documentation!