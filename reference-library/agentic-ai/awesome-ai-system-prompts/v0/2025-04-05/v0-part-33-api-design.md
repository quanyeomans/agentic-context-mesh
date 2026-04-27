## API Design

The AI SDK provides several core functions and integrations:

- `streamText`: This function is part of the AI SDK Core and is used for streaming text from LLMs. It's ideal for interactive use cases like chatbots or real-time applications where immediate responses are expected.
- `generateText`: This function is also part of the AI SDK Core and is used for generating text for a given prompt and model. It's suitable for non-interactive use cases or when you need to write text for tasks like drafting emails or summarizing web pages.
- `@ai-sdk/openai`: This is a package that provides integration with OpenAI's models. It allows you to use OpenAI's models with the standardized AI SDK interface.

### Core Functions

#### 1. `generateText`

- **Purpose**: Generates text for a given prompt and model.
- **Use case**: Non-interactive text generation, like drafting emails or summarizing content.

**Signature**:
```typescript
function generateText(options: {
model: AIModel;
prompt: string;
system?: string;
}): Promise<{ text: string; finishReason: string; usage: Usage }>
```

#### 2. `streamText`

- **Purpose**: Streams text from a given prompt and model.
- **Use case**: Interactive applications like chatbots or real-time content generation.

**Signature**:
```typescript
function streamText(options: {
model: AIModel;
prompt: string;
system?: string;
onChunk?: (chunk: Chunk) => void;
onFinish?: (result: StreamResult) => void;
}): StreamResult
```

### OpenAI Integration

The `@ai-sdk/openai` package provides integration with OpenAI models:

```typescript
import { openai } from '@ai-sdk/openai'

const model = openai('gpt-4o')
```

---