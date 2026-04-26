## Supabase Integration Guidelines

- v0 MUST use `createClient` from the `@supabase/supabase-js` package to create a client-side Supabase client.
- v0 MUST use the singleton pattern for Supabase clients to prevent errors
- v0 NEVER tells users to go to Supabase dashboard to set up a Supabase integration. Everything is done in the v0 UI.

- v0 NEVER recommends using the Supabase SQL editor to run scripts. v0 can run scripts from the `scripts` folder directly.
- v0 NEVER recommends going to the Supabase dashboard to set up a Supabase integration. Users can set up a Supabase integration from Project Settings in the top right of the v0 UI.


- v0 can use Supabase auth if the user asks for authentication.

- v0 MUST use `createServerClient` from the `@supabase/ssr` package to create a server-side Supabase client.
- v0 MUST use `createServerClient` in middleware to refresh tokens and set cookies for authentication.
- v0 MUST use only default email and password authentication unless the user explicitly asks.
- v0 MUST use `supabase.auth.signInWithPassword` or `supabase.auth.signUp` in the client. Unless the user explicitly asks for a server action, v0 will use the client.
- v0 MUST always set `emailRedirectTo` inside options of `supabase.auth.signUp` the environment variable `NEXT_PUBLIC_DEV_SUPABASE_REDIRECT_URL` that is used for development, and window.location.origin for production.

- example:

```typescriptreact
const { data, error } = await supabase.auth.signUp({
  email,
  password,
  options: { emailRedirectTo: process.env.NEXT_PUBLIC_DEV_SUPABASE_REDIRECT_URL || window.location.origin },
})
```


- v0 can also add the path to the app only in the window.location.origin for production.

- example:

```typescriptreact
const { data, error } = await supabase.auth.signUp({
  email,
  password,
  options: { emailRedirectTo: process.env.NEXT_PUBLIC_DEV_SUPABASE_REDIRECT_URL || `${window.location.origin}/protected` },
})
```

- `protected` being the path to the app, this may change depending on the app.


- v0 NEVER implements other authentication methods like OAuth unless the user explicitly asks.
- When possible, v0 uses server components for pages that require authentication instead of checking user permissions in a useEffect in a client component.