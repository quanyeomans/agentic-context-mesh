## Comparison

Compared to `next/legacy/image`, the new `next/image` component has the following changes:

-   Removes `` wrapper around `` in favor of native computed aspect ratio
-   Adds support for canonical `style` prop
  -   Removes `layout` prop in favor of `style` or `className`
  -   Removes `objectFit` prop in favor of `style` or `className`
  -   Removes `objectPosition` prop in favor of `style` or `className`
-   Removes `IntersectionObserver` implementation in favor of native lazy loading
  -   Removes `lazyBoundary` prop since there is no native equivalent
  -   Removes `lazyRoot` prop since there is no native equivalent
-   Removes `loader` config in favor of `loader` prop
-   Changed `alt` prop from optional to required
-   Changed `onLoadingComplete` callback to receive reference to `` element