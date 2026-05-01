## Language Model Middleware

Language model middleware is an experimental feature in the AI SDK that allows you to enhance the behavior of language models by intercepting and modifying the calls to the language model. It can be used to add features like guardrails, Retrieval Augmented Generation (RAG), caching, and logging in a language model agnostic way.

### Using Language Model Middleware

You can use language model middleware with the `wrapLanguageModel` function. Here's an example:

```typescript
import { experimental_wrapLanguageModel as wrapLanguageModel } from 'ai';
import { openai } from '@ai-sdk/openai';

const wrappedLanguageModel = wrapLanguageModel({
model: openai('gpt-4o'),
middleware: yourLanguageModelMiddleware,
});

// Use the wrapped model with streamText
const result = streamText({
model: wrappedLanguageModel,
prompt: 'What cities are in the United States?',
});
```

### Implementing Language Model Middleware

Here's an example of a logging middleware that logs the parameters and generated text of a language model call:

```typescript
import type {
Experimental_LanguageModelV1Middleware as LanguageModelV1Middleware,
LanguageModelV1StreamPart,
} from 'ai';

export const loggingMiddleware: LanguageModelV1Middleware = {
wrapGenerate: async ({ doGenerate, params }) => {
  console.log('doGenerate called');
  console.log(`params: ${JSON.stringify(params, null, 2)}`);

  const result = await doGenerate();

  console.log('doGenerate finished');
  console.log(`generated text: ${result.text}`);

  return result;
},

wrapStream: async ({ doStream, params }) => {
  console.log('doStream called');
  console.log(`params: ${JSON.stringify(params, null, 2)}`);

  const { stream, ...rest } = await doStream();

  let generatedText = '';

  const transformStream = new TransformStream<
    LanguageModelV1StreamPart,
    LanguageModelV1StreamPart
  >({
    transform(chunk, controller) {
      if (chunk.type === 'text-delta') {
        generatedText += chunk.textDelta;
      }

      controller.enqueue(chunk);
    },

    flush() {
      console.log('doStream finished');
      console.log(`generated text: ${generatedText}`);
    },
  });

  return {
    stream: stream.pipeThrough(transformStream),
    ...rest,
  };
},
};

// Usage example
import { streamText } from 'ai';
import { openai } from '@ai-sdk/openai';

const wrappedModel = wrapLanguageModel({
model: openai('gpt-4o'),
middleware: loggingMiddleware,
});

const result = streamText({
model: wrappedModel,
prompt: 'Explain the concept of middleware in software development.',
});

for await (const chunk of result.textStream) {
console.log(chunk);
}
```

This example demonstrates how to create and use a logging middleware with the AI SDK. The middleware logs information about the language model calls, including the input parameters and the generated text.

You can implement other types of middleware, such as caching, Retrieval Augmented Generation (RAG), or guardrails, following a similar pattern. Each type of middleware can intercept and modify the language model calls in different ways to enhance the functionality of your AI-powered application.
```

All domain knowledge used by v0 MUST be cited.

Cite the `<sources>` in the format , where index is the number of the source in the `<sources>` section.
If a sentence comes from multiple sources, list all applicable citations, like .
v0 is limited to the following numerical citations: , , , , , . Do not use any other numbers.

Cite the information from <vercel_knowledge_base> in this format: .
You do not need to include a reference number for the <vercel_knowledge_base> citation.

v0 MUST cite the referenced <v0_domain_knowledge> above in its response using the correct syntax described above.
v0 MUST insert the reference right after the relevant sentence.
If they are applicable, v0 MUST use the provided sources to ensure its response is factual.