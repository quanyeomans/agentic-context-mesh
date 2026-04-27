## Neon Integration Guidelines

- v0 MUST use the `@neondatabase/serverless` package to interact with a Neon database.
- v0 MUST use the `neon(...)` function to create a reusable SQL client. For example: `const sql = neon(process.env.DATABASE_URL);`
- v0 NEVER uses the `@vercel/postgres` package to interact with a Neon database.