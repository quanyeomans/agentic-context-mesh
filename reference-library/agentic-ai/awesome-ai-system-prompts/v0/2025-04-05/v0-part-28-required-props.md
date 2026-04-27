## Required Props

The `` component requires the following properties.

### src

Must be one of the following:

-   A statically imported image file
-   A path string. This can be either an absolute external URL, or an internal path depending on the loader prop or loader configuration.

When using the default loader, also consider the following for source images:

-   When src is an external URL, you must also configure remotePatterns
-   When src is animated or not a known format (JPEG, PNG, WebP, AVIF, GIF, TIFF) the image will be served as-is
-   When src is SVG format, it will be blocked unless `unoptimized` or `dangerouslyAllowSVG` is enabled

### width

The `width` property can represent either the _rendered_ width or _original_ width in pixels, depending on the `layout` and `sizes` properties.

When using `layout="intrinsic"` or `layout="fixed"` the `width` property represents the _rendered_ width in pixels, so it will affect how large the image appears.

When using `layout="responsive"`, `layout="fill"`, the `width` property represents the _original_ width in pixels, so it will only affect the aspect ratio.

The `width` property is required, except for statically imported images, or those with `layout="fill"`.

### height

The `height` property can represent either the _rendered_ height or _original_ height in pixels, depending on the `layout` and `sizes` properties.

When using `layout="intrinsic"` or `layout="fixed"` the `height` property represents the _rendered_ height in pixels, so it will affect how large the image appears.

When using `layout="responsive"`, `layout="fill"`, the `height` property represents the _original_ height in pixels, so it will only affect the aspect ratio.

The `height` property is required, except for statically imported images, or those with `layout="fill"`.