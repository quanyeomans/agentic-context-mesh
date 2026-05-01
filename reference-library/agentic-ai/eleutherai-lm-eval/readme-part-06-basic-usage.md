## Basic Usage

### Documentation

| Guide | Description |
|-------|-------------|
| [CLI Reference](./docs/interface.md) | Command-line arguments and subcommands |
| [Configuration Guide](./docs/config_files.md) | YAML config file format and examples |
| [Python API](./docs/python-api.md) | Programmatic usage with `simple_evaluate()` |
| [Task Guide](./lm_eval/tasks/README.md) | Available tasks and task configuration |

Use `lm-eval -h` to see available options, or `lm-eval run -h` for evaluation options.

List available tasks with:

```bash
lm-eval ls tasks
```

### Hugging Face `transformers`

> [!Important]
> To use the HuggingFace backend, first install: `pip install "lm_eval[hf]"`

To evaluate a model hosted on the [HuggingFace Hub](https://huggingface.co/models) (e.g. GPT-J-6B) on `hellaswag` you can use the following command (this assumes you are using a CUDA-compatible GPU):

```bash
lm_eval --model hf \
    --model_args pretrained=EleutherAI/gpt-j-6B \
    --tasks hellaswag \
    --device cuda:0 \
    --batch_size 8
```

Additional arguments can be provided to the model constructor using the `--model_args` flag. Most notably, this supports the common practice of using the `revisions` feature on the Hub to store partially trained checkpoints, or to specify the datatype for running a model:

```bash
lm_eval --model hf \
    --model_args pretrained=EleutherAI/pythia-160m,revision=step100000,dtype="float" \
    --tasks lambada_openai,hellaswag \
    --device cuda:0 \
    --batch_size 8
```

Models that are loaded via both `transformers.AutoModelForCausalLM` (autoregressive, decoder-only GPT style models) and `transformers.AutoModelForSeq2SeqLM` (such as encoder-decoder models like T5) in Huggingface are supported.

Batch size selection can be automated by setting the  ```--batch_size``` flag to ```auto```. This will perform automatic detection of the largest batch size that will fit on your device. On tasks where there is a large difference between the longest and shortest example, it can be helpful to periodically recompute the largest batch size, to gain a further speedup. To do this, append ```:N``` to above flag to automatically recompute the largest batch size ```N``` times. For example, to recompute the batch size 4 times, the command would be:

```bash
lm_eval --model hf \
    --model_args pretrained=EleutherAI/pythia-160m,revision=step100000,dtype="float" \
    --tasks lambada_openai,hellaswag \
    --device cuda:0 \
    --batch_size auto:4
```

> [!Note]
> Just like you can provide a local path to `transformers.AutoModel`, you can also provide a local path to `lm_eval` via `--model_args pretrained=/path/to/model`

#### Evaluating GGUF Models

`lm-eval` supports evaluating models in GGUF format using the Hugging Face (`hf`) backend. This allows you to use quantized models compatible with `transformers`, `AutoModel`, and llama.cpp conversions.

To evaluate a GGUF model, pass the path to the directory containing the model weights, the `gguf_file`, and optionally a separate `tokenizer` path using the `--model_args` flag.

**🚨 Important Note:**  
If no separate tokenizer is provided, Hugging Face will attempt to reconstruct the tokenizer from the GGUF file — this can take **hours** or even hang indefinitely. Passing a separate tokenizer avoids this issue and can reduce tokenizer loading time from hours to seconds.

**✅ Recommended usage:**

```bash
lm_eval --model hf \
    --model_args pretrained=/path/to/gguf_folder,gguf_file=model-name.gguf,tokenizer=/path/to/tokenizer \
    --tasks hellaswag \
    --device cuda:0 \
    --batch_size 8
```

> [!Tip]
> Ensure the tokenizer path points to a valid Hugging Face tokenizer directory (e.g., containing tokenizer_config.json, vocab.json, etc.).

#### Multi-GPU Evaluation with Hugging Face `accelerate`

We support three main ways of using Hugging Face's [accelerate 🚀](https://github.com/huggingface/accelerate) library for multi-GPU evaluation.

To perform *data-parallel evaluation* (where each GPU loads a **separate full copy** of the model), we leverage the `accelerate` launcher as follows:

```bash
accelerate launch -m lm_eval --model hf \
    --tasks lambada_openai,arc_easy \
    --batch_size 16
```

(or via `accelerate launch --no-python lm_eval`).

For cases where your model can fit on a single GPU, this allows you to evaluate on K GPUs K times faster than on one.

**WARNING**: This setup does not work with FSDP model sharding, so in `accelerate config` FSDP must be disabled, or the NO_SHARD FSDP option must be used.

The second way of using `accelerate` for multi-GPU evaluation is when your model is *too large to fit on a single GPU.*

In this setting, run the library *outside the `accelerate` launcher*, but passing `parallelize=True` to `--model_args` as follows:

```bash
lm_eval --model hf \
    --tasks lambada_openai,arc_easy \
    --model_args parallelize=True \
    --batch_size 16
```

This means that your model's weights will be split across all available GPUs.

For more advanced users or even larger models, we allow for the following arguments when `parallelize=True` as well:

- `device_map_option`: How to split model weights across available GPUs. defaults to "auto".
- `max_memory_per_gpu`: the max GPU memory to use per GPU in loading the model.
- `max_cpu_memory`: the max amount of CPU memory to use when offloading the model weights to RAM.
- `offload_folder`: a folder where model weights will be offloaded to disk if needed.

The third option is to use both at the same time. This will allow you to take advantage of both data parallelism and model sharding, and is especially useful for models that are too large to fit on a single GPU.

```bash
accelerate launch --multi_gpu --num_processes {nb_of_copies_of_your_model} \
    -m lm_eval --model hf \
    --tasks lambada_openai,arc_easy \
    --model_args parallelize=True \
    --batch_size 16
```

To learn more about model parallelism and how to use it with the `accelerate` library, see the [accelerate documentation](https://huggingface.co/docs/transformers/v4.15.0/en/parallelism)

**Warning: We do not natively support multi-node evaluation using the `hf` model type! Please reference [our GPT-NeoX library integration](https://github.com/EleutherAI/gpt-neox/blob/main/eval.py) for an example of code in which a custom multi-machine evaluation script is written.**

**Note: we do not currently support multi-node evaluations natively, and advise using either an externally hosted server to run inference requests against, or creating a custom integration with your distributed framework [as is done for the GPT-NeoX library](https://github.com/EleutherAI/gpt-neox/blob/main/eval_tasks/eval_adapter.py).**

### Steered Hugging Face `transformers` models

To evaluate a Hugging Face `transformers` model with steering vectors applied, specify the model type as `steered` and provide the path to either a PyTorch file containing pre-defined steering vectors, or a CSV file that specifies how to derive steering vectors from pretrained `sparsify` or `sae_lens` models (you will need to install the corresponding optional dependency for this method).

Specify pre-defined steering vectors:

```python
import torch

steer_config = {
    "layers.3": {
        "steering_vector": torch.randn(1, 768),
        "bias": torch.randn(1, 768),
        "steering_coefficient": 1,
        "action": "add"
    },
}
torch.save(steer_config, "steer_config.pt")
```

Specify derived steering vectors:

```python
import pandas as pd

pd.DataFrame({
    "loader": ["sparsify"],
    "action": ["add"],
    "sparse_model": ["EleutherAI/sae-pythia-70m-32k"],
    "hookpoint": ["layers.3"],
    "feature_index": [30],
    "steering_coefficient": [10.0],
}).to_csv("steer_config.csv", index=False)
```

Run the evaluation harness with steering vectors applied:

```bash
lm_eval --model steered \
    --model_args pretrained=EleutherAI/pythia-160m,steer_path=steer_config.pt \
    --tasks lambada_openai,hellaswag \
    --device cuda:0 \
    --batch_size 8
```

### NVIDIA `nemo` models

[NVIDIA NeMo Framework](https://github.com/NVIDIA/NeMo) is a generative AI framework built for researchers and pytorch developers working on language models.

To evaluate a `nemo` model, start by installing NeMo following [the documentation](https://github.com/NVIDIA/NeMo?tab=readme-ov-file#installation). We highly recommended to use the NVIDIA PyTorch or NeMo container, especially if having issues installing Apex or any other dependencies (see [latest released containers](https://github.com/NVIDIA/NeMo/releases)). Please also install the lm evaluation harness library following the instructions in [the Install section](https://github.com/EleutherAI/lm-evaluation-harness/tree/main?tab=readme-ov-file#install).

NeMo models can be obtained through [NVIDIA NGC Catalog](https://catalog.ngc.nvidia.com/models) or in [NVIDIA's Hugging Face page](https://huggingface.co/nvidia). In [NVIDIA NeMo Framework](https://github.com/NVIDIA/NeMo/tree/main/scripts/nlp_language_modeling) there are conversion scripts to convert the `hf` checkpoints of popular models like llama, falcon, mixtral or mpt to `nemo`.

Run a `nemo` model on one GPU:

```bash
lm_eval --model nemo_lm \
    --model_args path=<path_to_nemo_model> \
    --tasks hellaswag \
    --batch_size 32
```

It is recommended to unpack the `nemo` model to avoid the unpacking inside the docker container - it may overflow disk space. For that you can run:

```bash
mkdir MY_MODEL
tar -xvf MY_MODEL.nemo -c MY_MODEL
```

#### Multi-GPU evaluation with NVIDIA `nemo` models

By default, only one GPU is used. But we do support either data replication or tensor/pipeline parallelism during evaluation, on one node.

1) To enable data replication, set the `model_args` of `devices` to the number of data replicas to run. For example, the command to run 8 data replicas over 8 GPUs is:

```bash
torchrun --nproc-per-node=8 --no-python lm_eval \
    --model nemo_lm \
    --model_args path=<path_to_nemo_model>,devices=8 \
    --tasks hellaswag \
    --batch_size 32
```

1) To enable tensor and/or pipeline parallelism, set the `model_args` of `tensor_model_parallel_size` and/or `pipeline_model_parallel_size`. In addition, you also have to set up `devices` to be equal to the product of `tensor_model_parallel_size` and/or `pipeline_model_parallel_size`. For example, the command to use one node of 4 GPUs with tensor parallelism of 2 and pipeline parallelism of 2 is:

```bash
torchrun --nproc-per-node=4 --no-python lm_eval \
    --model nemo_lm \
    --model_args path=<path_to_nemo_model>,devices=4,tensor_model_parallel_size=2,pipeline_model_parallel_size=2 \
    --tasks hellaswag \
    --batch_size 32
```

Note that it is recommended to substitute the `python` command by `torchrun --nproc-per-node=<number of devices> --no-python` to facilitate loading the model into the GPUs. This is especially important for large checkpoints loaded into multiple GPUs.

Not supported yet: multi-node evaluation and combinations of data replication with tensor or pipeline parallelism.

### Megatron-LM models

[Megatron-LM](https://github.com/NVIDIA/Megatron-LM) is NVIDIA's large-scale transformer training framework. This backend allows direct evaluation of Megatron-LM checkpoints without conversion.

**Requirements:**
- Megatron-LM must be installed or accessible via `MEGATRON_PATH` environment variable
- PyTorch with CUDA support

**Setup:**

```bash