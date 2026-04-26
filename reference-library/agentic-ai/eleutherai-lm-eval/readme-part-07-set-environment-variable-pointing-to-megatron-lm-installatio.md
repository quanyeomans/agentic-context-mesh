# Set environment variable pointing to Megatron-LM installation
export MEGATRON_PATH=/path/to/Megatron-LM
```

**Basic usage (single GPU):**

```bash
lm_eval --model megatron_lm \
    --model_args load=/path/to/checkpoint,tokenizer_type=HuggingFaceTokenizer,tokenizer_model=/path/to/tokenizer \
    --tasks hellaswag \
    --batch_size 1
```

**Supported checkpoint formats:**
- Standard Megatron checkpoints (`model_optim_rng.pt`)
- Distributed checkpoints (`.distcp` format, auto-detected)

#### Parallelism Modes

The Megatron-LM backend supports the following parallelism modes:

| Mode | Configuration | Description |
|------|---------------|-------------|
| Single GPU | `devices=1` (default) | Standard single GPU evaluation |
| Data Parallelism | `devices>1, TP=1` | Each GPU has a full model replica, data is distributed |
| Tensor Parallelism | `TP == devices` | Model layers are split across GPUs |
| Expert Parallelism | `EP == devices, TP=1` | For MoE models, experts are distributed across GPUs |

> [!Note]
> - Pipeline Parallelism (PP > 1) is not currently supported.
> - Expert Parallelism (EP) cannot be combined with Tensor Parallelism (TP).

**Data Parallelism (4 GPUs, each with full model replica):**

```bash
torchrun --nproc-per-node=4 -m lm_eval --model megatron_lm \
    --model_args load=/path/to/checkpoint,tokenizer_model=/path/to/tokenizer,devices=4 \
    --tasks hellaswag
```

**Tensor Parallelism (TP=2):**

```bash
torchrun --nproc-per-node=2 -m lm_eval --model megatron_lm \
    --model_args load=/path/to/checkpoint,tokenizer_model=/path/to/tokenizer,devices=2,tensor_model_parallel_size=2 \
    --tasks hellaswag
```

**Expert Parallelism for MoE models (EP=4):**

```bash
torchrun --nproc-per-node=4 -m lm_eval --model megatron_lm \
    --model_args load=/path/to/moe_checkpoint,tokenizer_model=/path/to/tokenizer,devices=4,expert_model_parallel_size=4 \
    --tasks hellaswag
```

**Using extra_args for additional Megatron options:**

```bash
lm_eval --model megatron_lm \
    --model_args load=/path/to/checkpoint,tokenizer_model=/path/to/tokenizer,extra_args="--no-rope-fusion --trust-remote-code" \
    --tasks hellaswag
```

> [!Note]
> The `--use-checkpoint-args` flag is enabled by default, which loads model architecture parameters from the checkpoint. For checkpoints converted via Megatron-Bridge, this typically includes all necessary model configuration.

#### Multi-GPU evaluation with OpenVINO models

Pipeline parallelism during evaluation is supported with OpenVINO models

To enable pipeline parallelism, set the `model_args` of `pipeline_parallel`. In addition, you also have to set up `device` to value `HETERO:<GPU index1>,<GPU index2>` for example `HETERO:GPU.1,GPU.0` For example, the command to use pipeline parallelism of 2 is:

```bash
lm_eval --model openvino \
    --tasks wikitext \
    --model_args pretrained=<path_to_ov_model>,pipeline_parallel=True \
    --device HETERO:GPU.1,GPU.0
```

### Tensor + Data Parallel and Optimized Inference with `vLLM`

We also support vLLM for faster inference on [supported model types](https://docs.vllm.ai/en/latest/models/supported_models.html), especially faster when splitting a model across multiple GPUs. For single-GPU or multi-GPU — tensor parallel, data parallel, or a combination of both — inference, for example:

```bash
lm_eval --model vllm \
    --model_args pretrained={model_name},tensor_parallel_size={GPUs_per_model},dtype=auto,gpu_memory_utilization=0.8,data_parallel_size={model_replicas} \
    --tasks lambada_openai \
    --batch_size auto
```

To use vllm, do `pip install "lm_eval[vllm]"`. For a full list of supported vLLM configurations, please reference our [vLLM integration](https://github.com/EleutherAI/lm-evaluation-harness/blob/e74ec966556253fbe3d8ecba9de675c77c075bce/lm_eval/models/vllm_causallms.py) and the vLLM documentation.

vLLM occasionally differs in output from Huggingface. We treat Huggingface as the reference implementation and provide a [script](./scripts/model_comparator.py) for checking the validity of vllm results against HF.

> [!Tip]
> For fastest performance, we recommend using `--batch_size auto` for vLLM whenever possible, to leverage its continuous batching functionality!

> [!Tip]
> Passing `max_model_len=4096` or some other reasonable default to vLLM through model args may cause speedups or prevent out-of-memory errors when trying to use auto batch size, such as for Mistral-7B-v0.1 which defaults to a maximum length of 32k.

### Tensor + Data Parallel and Fast Offline Batching Inference with `SGLang`

We support SGLang for efficient offline batch inference. Its **[Fast Backend Runtime](https://docs.sglang.ai/index.html)** delivers high performance through optimized memory management and parallel processing techniques. Key features include tensor parallelism, continuous batching, and support for various quantization methods (FP8/INT4/AWQ/GPTQ).

To use SGLang as the evaluation backend, please **install it in advance** via SGLang documents [here](https://docs.sglang.io/get_started/install.html#install-sglang).

> [!Tip]
> Due to the installing method of [`Flashinfer`](https://docs.flashinfer.ai/)-- a fast attention kernel library, we don't include the dependencies of `SGLang` within [pyproject.toml](pyproject.toml). Note that the `Flashinfer` also has some requirements on `torch` version.

SGLang's server arguments are slightly different from other backends, see [here](https://docs.sglang.io/advanced_features/server_arguments.html) for more information. We provide an example of the usage here:

```bash
lm_eval --model sglang \
    --model_args pretrained={model_name},dp_size={data_parallel_size},tp_size={tensor_parallel_size},dtype=auto \
    --tasks gsm8k_cot \
    --batch_size auto
```

> [!Tip]
> When encountering out-of-memory (OOM) errors (especially for multiple-choice tasks), try these solutions:
>
> 1. Use a manual `batch_size`, rather than `auto`.
> 2. Lower KV cache pool memory usage by adjusting `mem_fraction_static` - Add to your model arguments for example `--model_args pretrained=...,mem_fraction_static=0.7`.
> 3. Increase tensor parallel size `tp_size` (if using multiple GPUs).

### Windows ML

We support **Windows ML** for hardware-accelerated inference on Windows platforms. This enables evaluation on CPU, GPU, and **NPU (Neural Processing Unit)** devices.

Windows ML?
https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/overview

To use Windows ML, install the required dependencies:

```bash
pip install wasdk-Microsoft.Windows.AI.MachineLearning[all] wasdk-Microsoft.Windows.ApplicationModel.DynamicDependency.Bootstrap onnxruntime-windowsml onnxruntime-genai-winml
```

Evaluate an ONNX Runtime GenAI LLM on NPU/GPU/CPU on Windows:

```bash
lm_eval --model winml \
    --model_args pretrained=/path/to/onnx/model \
    --tasks mmlu \
    --batch_size 1
```

> [!Note]
> The Windows ML backend is ONLY for ONNX Runtime GenAI model format. Models targeting `transformers.js` won't work. You can verify this by finding the `genai_config.json` file in the model folder.

> [!Note]
> To run an ONNX Runtime GenAI model on the target device, you MUST convert the original model to that vendor and device type. Converted models won't work / work well on other vendor or device types. To learn more on model conversion, please visit [Microsoft AI Tool Kit](https://code.visualstudio.com/docs/intelligentapps/modelconversion)

### Model APIs and Inference Servers

> [!Important]
> To use API-based models, first install: `pip install "lm_eval[api]"`

Our library also supports the evaluation of models served via several commercial APIs, and we hope to implement support for the most commonly used performant local/self-hosted inference servers.

To call a hosted model, use:

```bash
export OPENAI_API_KEY=YOUR_KEY_HERE
lm_eval --model openai-completions \
    --model_args model=davinci-002 \
    --tasks lambada_openai,hellaswag
```

We also support using your own local inference server with servers that mirror the OpenAI Completions and ChatCompletions APIs.

```bash
lm_eval --model local-completions --tasks gsm8k --model_args model=facebook/opt-125m,base_url=http://{yourip}:8000/v1/completions,num_concurrent=1,max_retries=3,tokenized_requests=False,batch_size=16
```

Note that for externally hosted models, configs such as `--device` which relate to where to place a local model should not be used and do not function. Just like you can use `--model_args` to pass arbitrary arguments to the model constructor for local models, you can use it to pass arbitrary arguments to the model API for hosted models. See the documentation of the hosting service for information on what arguments they support.

| API or Inference Server                                                                                                   | Implemented?                                                                                            | `--model <xxx>` name                                | Models supported:                                                                                                                                                                                                                                                                                                                                          | Request Types:                                                                 |
|---------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|-----------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| OpenAI Completions                                                                                                        | :heavy_check_mark:                                                                                      | `openai-completions`, `local-completions`           | All OpenAI Completions API models                                                                                                                                                                                                                                                                                                                          | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |
| OpenAI ChatCompletions                                                                                                    | :heavy_check_mark:                                                                                      | `openai-chat-completions`, `local-chat-completions` | [All ChatCompletions API models](https://platform.openai.com/docs/guides/gpt)                                                                                                                                                                                                                                                                              | `generate_until` (no logprobs)                                                 |
| Anthropic                                                                                                                 | :heavy_check_mark:                                                                                      | `anthropic`                                         | [Supported Anthropic Engines](https://docs.anthropic.com/claude/reference/selecting-a-model)                                                                                                                                                                                                                                                               | `generate_until` (no logprobs)                                                 |
| Anthropic Chat                                                                                                            | :heavy_check_mark:                                                                                      | `anthropic-chat`, `anthropic-chat-completions`      | [Supported Anthropic Engines](https://docs.anthropic.com/claude/docs/models-overview)                                                                                                                                                                                                                                                                      | `generate_until` (no logprobs)                                                 |
| Textsynth                                                                                                                 | :heavy_check_mark:                                                                                      | `textsynth`                                         | [All supported engines](https://textsynth.com/documentation.html#engines)                                                                                                                                                                                                                                                                                  | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |
| Cohere                                                                                                                    | [:hourglass: - blocked on Cohere API bug](https://github.com/EleutherAI/lm-evaluation-harness/pull/395) | N/A                                                 | [All `cohere.generate()` engines](https://docs.cohere.com/docs/models)                                                                                                                                                                                                                                                                                     | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |
| [Llama.cpp](https://github.com/ggerganov/llama.cpp) (via [llama-cpp-python](https://github.com/abetlen/llama-cpp-python)) | :heavy_check_mark:                                                                                      | `gguf`, `ggml`                                      | [All models supported by llama.cpp](https://github.com/ggerganov/llama.cpp)                                                                                                                                                                                                                                                                                | `generate_until`, `loglikelihood`, (perplexity evaluation not yet implemented) |
| vLLM                                                                                                                      | :heavy_check_mark:                                                                                      | `vllm`                                              | [Most HF Causal Language Models](https://docs.vllm.ai/en/latest/models/supported_models.html)                                                                                                                                                                                                                                                              | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |
| Mamba                                                                                                                     | :heavy_check_mark:                                                                                      | `mamba_ssm`                                         | [Mamba architecture Language Models via the `mamba_ssm` package](https://huggingface.co/state-spaces)                                                                                                                                                                                                                                                      | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |
| Huggingface Optimum (Causal LMs)                                                                                          | :heavy_check_mark:                                                                                      | `openvino`                                          | Any decoder-only AutoModelForCausalLM converted with Huggingface Optimum into OpenVINO™ Intermediate Representation (IR) format                                                                                                                                                                                                                            | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |
| Huggingface Optimum-intel IPEX (Causal LMs)                                                                               | :heavy_check_mark:                                                                                      | `ipex`                                              | Any decoder-only AutoModelForCausalLM                                                                                                                                                                                                                                                                                                                      | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |
| Huggingface Optimum-habana (Causal LMs)                                                                               | :heavy_check_mark:                                                                                      | `habana`                                              | Any decoder-only AutoModelForCausalLM                                                                                                                                                                                                                                                                                                                      | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |
| Neuron via AWS Inf2 (Causal LMs)                                                                                          | :heavy_check_mark:                                                                                      | `neuronx`                                           | Any decoder-only AutoModelForCausalLM supported to run on [huggingface-ami image for inferentia2](https://aws.amazon.com/marketplace/pp/prodview-gr3e6yiscria2)                                                                                                                                                                                            | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |
| NVIDIA NeMo                                                                                                               | :heavy_check_mark:                                                                                      | `nemo_lm`                                           | [All supported models](https://docs.nvidia.com/nemo-framework/user-guide/24.09/nemotoolkit/core/core.html#nemo-models)                                                                                                                                                                                                                                     | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |
| NVIDIA Megatron-LM                                                                                                        | :heavy_check_mark:                                                                                      | `megatron_lm`                                       | [Megatron-LM GPT models](https://github.com/NVIDIA/Megatron-LM) (standard and distributed checkpoints)                                                                                                                                                                                                                                                     | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |
| Watsonx.ai                                                                                                                | :heavy_check_mark:                                                                                      | `watsonx_llm`                                       | [Supported Watsonx.ai Engines](https://dataplatform.cloud.ibm.com/docs/content/wsj/analyze-data/fm-models.html?context=wx)                                                                                                                                                                                                                                 | `generate_until` `loglikelihood`                                               |
| Windows ML                                                                                           | :heavy_check_mark:                                                                                      | `winml`                                             | [ONNX models in GenAI format](https://code.visualstudio.com/docs/intelligentapps/modelconversion)                                                                                                                                                                                                                                                                                                                                 | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |
| [Your local inference server!](docs/API_guide.md)                                                                         | :heavy_check_mark:                                                                                      | `local-completions` or `local-chat-completions`     | Support for OpenAI API-compatible servers, with easy customization for other APIs.                                                                                                                                                                                                                                                                         | `generate_until`, `loglikelihood`, `loglikelihood_rolling`                     |

Models which do not supply logits or logprobs can be used with tasks of type `generate_until` only, while local models, or APIs that supply logprobs/logits of their prompts, can be run on all task types: `generate_until`, `loglikelihood`, `loglikelihood_rolling`, and `multiple_choice`.

For more information on the different task `output_types` and model request types, see [our documentation](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/model_guide.md#interface).

> [!Note]
> For best performance with closed chat model APIs such as Anthropic Claude 3 and GPT-4, we recommend carefully looking at a few sample outputs using `--limit 10` first to confirm answer extraction and scoring on generative tasks is performing as expected. providing `system="<some system prompt here>"` within `--model_args` for anthropic-chat-completions, to instruct the model what format to respond in, may be useful.

### Other Frameworks

A number of other libraries contain scripts for calling the eval harness through their library. These include [GPT-NeoX](https://github.com/EleutherAI/gpt-neox/blob/main/eval_tasks/eval_adapter.py), [Megatron-DeepSpeed](https://github.com/microsoft/Megatron-DeepSpeed/blob/main/examples/MoE/readme_evalharness.md), and [mesh-transformer-jax](https://github.com/kingoflolz/mesh-transformer-jax/blob/master/eval_harness.py).

To create your own custom integration you can follow instructions from [this tutorial](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/interface.md#external-library-usage).

### Additional Features

> [!Note]
> For tasks unsuitable for direct evaluation — either due risks associated with executing untrusted code or complexities in the evaluation process — the `--predict_only` flag is available to obtain decoded generations for post-hoc evaluation.

If you have a Metal compatible Mac, you can run the eval harness using the MPS back-end by replacing `--device cuda:0` with `--device mps` (requires PyTorch version 2.1 or higher). **Note that the PyTorch MPS backend is still in early stages of development, so correctness issues or unsupported operations may exist. If you observe oddities in model performance on the MPS back-end, we recommend first checking that a forward pass of your model on `--device cpu` and `--device mps` match.**

> [!Note]
> You can inspect what the LM inputs look like by running the following command:
>
> ```bash
> python write_out.py \
>     --tasks <task1,task2,...> \
>     --num_fewshot 5 \
>     --num_examples 10 \
>     --output_base_path /path/to/output/folder
> ```
>
> This will write out one text file for each task.

To verify the data integrity of the tasks you're performing in addition to running the tasks themselves, you can use the `--check_integrity` flag:

```bash
lm_eval --model openai \
    --model_args engine=davinci-002 \
    --tasks lambada_openai,hellaswag \
    --check_integrity
```