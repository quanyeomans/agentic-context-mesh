## Install

To install the `lm-eval` package from the github repository, run:

```bash
git clone --depth 1 https://github.com/EleutherAI/lm-evaluation-harness
cd lm-evaluation-harness
pip install -e .
```

### Installing Model Backends

The base installation provides the core evaluation framework. **Model backends must be installed separately** using optional extras:

For HuggingFace transformers models:

```bash
pip install "lm_eval[hf]"
```

For vLLM inference:

```bash
pip install "lm_eval[vllm]"
```

For API-based models (OpenAI, Anthropic, etc.):

```bash
pip install "lm_eval[api]"
```

Multiple backends can be installed together:

```bash
pip install "lm_eval[hf,vllm,api]"
```

A detailed table of all optional extras is available at the end of this document.