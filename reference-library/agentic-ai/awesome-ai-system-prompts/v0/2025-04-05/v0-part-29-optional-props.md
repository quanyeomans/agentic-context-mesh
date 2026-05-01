## Optional Props

The `` component accepts a number of additional properties beyond those which are required. This section describes the most commonly-used properties of the Image component. Find details about more rarely-used properties in the Advanced Props section.

### layout

The layout behavior of the image as the viewport changes size.

| `layout` | Behavior | `srcSet` | `sizes` | Has wrapper and sizer |
| --- | --- | --- | --- | --- |
| `intrinsic` (default) | Scale _down_ to fit width of container, up to image size | `1x`, `2x` (based on imageSizes) | N/A | yes |
| `fixed` | Sized to `width` and `height` exactly | `1x`, `2x` (based on imageSizes) | N/A | yes |
| `responsive` | Scale to fit width of container | `640w`, `750w`, ... `2048w`, `3840w` (based on imageSizes and deviceSizes) | `100vw` | yes |
| `fill` | Grow in both X and Y axes to fill container | `640w`, `750w`, ... `2048w`, `3840w` (based on imageSizes and deviceSizes) | `100vw` | yes |

-   Demo the `intrinsic` layout (default)
  -   When `intrinsic`, the image will scale the dimensions down for smaller viewports, but maintain the original dimensions for larger viewports.
-   Demo the `fixed` layout
  -   When `fixed`, the image dimensions will not change as the viewport changes (no responsiveness) similar to the native `img` element.
-   Demo the `responsive` layout
  -   When `responsive`, the image will scale the dimensions down for smaller viewports and scale up for larger viewports.
  -   Ensure the parent element uses `display: block` in their stylesheet.
-   Demo the `fill` layout
  -   When `fill`, the image will stretch both width and height to the dimensions of the parent element, provided the parent element is relative.
  -   This is usually paired with the `objectFit` property.
  -   Ensure the parent element has `position: relative` in their stylesheet.
-   Demo background image

### loader

A custom function used to resolve URLs. Setting the loader as a prop on the Image component overrides the default loader defined in the `images` section of `next.config.js`.

A `loader` is a function returning a URL string for the image, given the following parameters:

-   `src`
-   `width`
-   `quality`

Here is an example of using a custom loader:

```
import Image from 'next/legacy/image'

const myLoader = ({ src, width, quality }) => {
return `https://example.com/${src}?w=${width}&q=${quality || 75}`
}

const MyImage = (props) => {
return (
  
)
}
```

**[^4]: [Removing Effect Dependencies – React](https://react.dev/learn/removing-effect-dependencies)**
App.jschat.js  
App.js  
Reset[Fork](https://codesandbox.io/api/v1/sandboxes/define?undefined&environment=create-react-app "Open in CodeSandbox")  
import { useState, useEffect } from 'react';
import { createConnection } from './chat.js';  
const serverUrl = 'https://localhost:1234';  
function ChatRoom({ roomId }) {
const [message, setMessage] = useState('');  
// Temporarily disable the linter to demonstrate the problem
// eslint-disable-next-line react-hooks/exhaustive-deps
const options = {
serverUrl: serverUrl,
roomId: roomId
};  
useEffect(() => {
const connection = createConnection(options);
connection.connect();
return () => connection.disconnect();
}, [options]);  
return (
<>
<h1>Welcome to the {roomId} room!</h1>
 setMessage(e.target.value)} />
</>
);
}  
export default function App() {
const [roomId, setRoomId] = useState('general');
return (
<>
<label>
Choose the chat room:{' '}
<select
value={roomId}
onChange={e => setRoomId(e.target.value)}
>
<option value="general">general</option>
<option value="travel">travel</option>
<option value="music">music</option>
</select>
</label>

<ChatRoom roomId={roomId} />
</>
);
}  
Show more  
In the sandbox above, the input only updates the `message` state variable. From the user's perspective, this should not affect the chat connection. However, every time you update the `message`, your component re-renders. When your component re-renders, the code inside of it runs again from scratch.  
A new `options` object is created from scratch on every re-render of the `ChatRoom` component. React sees that the `options` object is a _different object_ from the `options` object created during the last render. This is why it re-synchronizes your Effect (which depends on `options`), and the chat re-connects as you type.  
**This problem only affects objects and functions. In JavaScript, each newly created object and function is considered distinct from all the others. It doesn't matter that the contents inside of them may be the same!**  
// During the first renderconst options1 = { serverUrl: 'https://localhost:1234', roomId: 'music' };// During the next renderconst options2 = { serverUrl: 'https://localhost:1234', roomId: 'music' };// These are two different objects!console.log(Object.is(options1, options2)); // false  
**Object and function dependencies can make your Effect re-synchronize more often than you need.**  
This is why, whenever possible, you should try to avoid objects and functions as your Effect's dependencies. Instead, try moving them outside the component, inside the Effect, or extracting primitive values out of them.  
#### Move static objects and functions outside your component[](#move-static-objects-and-functions-outside-your-component "Link for Move static objects and functions outside your component ")  
If the object does not depend on any props and state, you can move that object outside your component:  
const options = {  serverUrl: 'https://localhost:1234',  roomId: 'music'};function ChatRoom() {  const [message, setMessage] = useState('');  useEffect(() => {    const connection = createConnection(options);    connection.connect();    return () => connection.disconnect();  }, []); // ✅ All dependencies declared  // ...  
This way, you _prove_ to the linter that it's not reactive. It can't change as a result of a re-render, so it doesn't need to be a dependency. Now re-rendering `ChatRoom` won't cause your Effect to re-synchronize.  
This works for functions too:  
function createOptions() {  return {    serverUrl: 'https://localhost:1234',    roomId: 'music'  };}function ChatRoom() {  const [message, setMessage] = useState('');  useEffect(() => {    const options = createOptions();    const connection = createConnection(options);    connection.connect();    return () => connection.disconnect();  }, []); // ✅ All dependencies declared  // ...

**[^5]: [Describing the UI – React](https://react.dev/learn/describing-the-ui)**
---
title: "Describing the UI – React"
description: ""
url: https://react.dev/learn/describing-the-ui
lastmod: "2024-08-22T23:20:28.609Z"
---
[Learn React](/learn)