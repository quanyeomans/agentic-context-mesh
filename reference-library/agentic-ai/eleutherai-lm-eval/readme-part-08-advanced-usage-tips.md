## Advanced Usage Tips

For models loaded with the HuggingFace  `transformers` library, any arguments provided via `--model_args` get passed to the relevant constructor directly. This means that anything you can do with `AutoModel` can be done with our library. For example, you can pass a local path via `pretrained=` or use models finetuned with [PEFT](https://github.com/huggingface/peft) by taking the call you would run to evaluate the base model and add `,peft=PATH` to the `model_args` argument:

```bash
lm_eval --model hf \
    --model_args pretrained=EleutherAI/gpt-j-6b,parallelize=True,load_in_4bit=True,peft=nomic-ai/gpt4all-j-lora \
    --tasks openbookqa,arc_easy,winogrande,hellaswag,arc_challenge,piqa,boolq \
    --device cuda:0
```

Models provided as delta weights can be easily loaded using the Hugging Face transformers library. Within --model_args, set the delta argument to specify the delta weights, and use the pretrained argument to designate the relative base model to which they will be applied:

```bash
lm_eval --model hf \
    --model_args pretrained=Ejafa/llama_7B,delta=lmsys/vicuna-7b-delta-v1.1 \
    --tasks hellaswag
```

GPTQ quantized models can be loaded using [GPTQModel](https://github.com/ModelCloud/GPTQModel) (faster) or [AutoGPTQ](https://github.com/PanQiWei/AutoGPTQ)

GPTQModel: add `,gptqmodel=True` to `model_args`

```bash
lm_eval --model hf \
    --model_args pretrained=model-name-or-path,gptqmodel=True \
    --tasks hellaswag
```

AutoGPTQ: add `,autogptq=True` to `model_args`:

```bash
lm_eval --model hf \
    --model_args pretrained=model-name-or-path,autogptq=model.safetensors,gptq_use_triton=True \
    --tasks hellaswag
```

We support wildcards in task names, for example you can run all of the machine-translated lambada tasks via `--task lambada_openai_mt_*`.