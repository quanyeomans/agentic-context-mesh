## Optional Extras

Extras dependencies can be installed via `pip install -e ".[NAME]"`

### Model Backends

These extras install dependencies required to run specific model backends:

| NAME           | Description                                      |
|----------------|--------------------------------------------------|
| hf             | HuggingFace Transformers (torch, transformers, accelerate, peft) |
| vllm           | vLLM fast inference                              |
| api            | API models (OpenAI, Anthropic, local servers)    |
| gptq           | AutoGPTQ quantized models                        |
| gptqmodel      | GPTQModel quantized models                       |
| ibm_watsonx_ai | IBM watsonx.ai models                            |
| ipex           | Intel IPEX backend                               |
| habana         | Intel Gaudi backend                              |
| optimum        | Intel OpenVINO models                            |
| neuronx        | AWS Inferentia2 instances                        |
| winml          | Windows ML (ONNX Runtime GenAI) - CPU/GPU/NPU    |
| sparsify       | Sparsify model steering                          |
| sae_lens       | SAELens model steering                           |

### Task Dependencies

These extras install dependencies required for specific evaluation tasks:

| NAME                 | Description                    |
|----------------------|--------------------------------|
| tasks                | All task-specific dependencies |
| acpbench             | ACP Bench tasks                |
| audiolm_qwen         | Qwen2 audio models             |
| ifeval               | IFEval task                    |
| japanese_leaderboard | Japanese LLM tasks             |
| longbench            | LongBench tasks                |
| math                 | Math answer checking           |
| multilingual         | Multilingual tokenizers        |
| ruler                | RULER tasks                    |

### Development & Utilities

| NAME          | Description                    |
|---------------|--------------------------------|
| dev           | Linting & contributions        |
| hf_transfer   | Speed up HF downloads          |
| sentencepiece | Sentencepiece tokenizer        |
| unitxt        | Unitxt tasks                   |
| wandb         | Weights & Biases logging       |
| zeno          | Zeno result visualization      |