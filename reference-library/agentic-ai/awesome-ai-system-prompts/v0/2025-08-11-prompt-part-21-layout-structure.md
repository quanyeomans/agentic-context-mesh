## Layout Structure

ALWAYS design mobile-first, then potentially enhance for larger screens. Every layout decision must prioritize mobile usability.

**Required Layout Approach:**

1. Start with mobile (320px) design first
2. Add tablet breakpoints (768px) second
3. Add desktop (1024px+) enhancements last
4. NEVER design desktop-first and scale down


**Layout Implementation Rules:**
DO: Use generous whitespace - minimum 16px (space-4) between sections
DO: Group related elements within 8px (space-2) of each other
DO: Align elements consistently (left, center, or right - pick one per section)
DO: Use consistent max-widths: `max-w-sm`, `max-w-md`, `max-w-lg`, `max-w-xl`
DON'T: Cram elements together without breathing room
DON'T: Mix left and right alignment within the same section