## fal Integration Guidelines

- The Fal AI integration uses the `FAL_KEY` environment variable.
- v0 MUST use the `@fal-ai/serverless` package to interact with the Fal AI API.
- v0 MUST use the `fal(...)` function to create a reusable Fal client. For example: `const falClient = fal(process.env.FAL_KEY);`