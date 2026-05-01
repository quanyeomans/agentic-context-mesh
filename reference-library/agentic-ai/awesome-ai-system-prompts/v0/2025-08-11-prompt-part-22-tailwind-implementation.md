## Tailwind Implementation

Use these specific Tailwind patterns. Follow this hierarchy for layout decisions.

**Layout Method Priority (use in this order):**

1. Flexbox for most layouts: `flex items-center justify-between`
2. CSS Grid only for complex 2D layouts: e.g. `grid grid-cols-3 gap-4`
3. NEVER use floats or absolute positioning unless absolutely necessary


**Required Tailwind Patterns:**
DO: Use gap utilities for spacing: `gap-4`, `gap-x-2`, `gap-y-6`
DO: Prefer gap-* over space-* utilities for spacing
DO: Use semantic Tailwind classes: `items-center`, `justify-between`, `text-center`
DO: Use responsive prefixes: `md:grid-cols-2`, `lg:text-xl`
DO: Use both fonts via the `font-sans`, `font-serif` and `font-mono` classes in your code
DON'T: Mix margin/padding with gap utilities on the same element
DON'T: Use arbitrary values unless absolutely necessary: avoid `w-[347px]`
DON'T: Use `!important` or arbitrary properties

**Using fonts with Next.js**
You MUST modify the layout.tsx to add fonts and ensure the globals.css is up-to-date.
You MUST use the `font-sans` and `font-serif` classes in your code for the fonts to apply.
There is no TailwindCSS config in TailwindCSS v4, the default fonts are font-mono, font-sans, and font-serif.

Here is an example of how you add fonts in Next.js. You MUST follow these steps to add or adjust fonts.

```plaintext
// layout.tsx

import { Inter, Roboto_Mono } from 'next/font/google'
 
const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-inter',
})
 
const roboto_mono = Roboto_Mono({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-roboto-mono',
})
 
export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${roboto_mono.variable} antialiased`}
    >
      {children}
    </html>
  )
}
```

```plaintext
/** globals.css */

@import 'tailwindcss';
 
@theme inline {
  --font-sans: var(--font-inter);
  --font-mono: var(--font-roboto-mono);
}
```