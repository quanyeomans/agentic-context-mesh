## Performance Hooks[](#performance-hooks "Link for Performance Hooks ")  
A common way to optimize re-rendering performance is to skip unnecessary work. For example, you can tell React to reuse a cached calculation or to skip a re-render if the data has not changed since the previous render.  
To skip calculations and unnecessary re-rendering, use one of these Hooks:  
*   [`useMemo`](/reference/react/useMemo) lets you cache the result of an expensive calculation.
*   [`useCallback`](/reference/react/useCallback) lets you cache a function definition before passing it down to an optimized component.  
function TodoList({ todos, tab, theme }) {  const visibleTodos = useMemo(() => filterTodos(todos, tab), [todos, tab]);  // ...}  
Sometimes, you can't skip re-rendering because the screen actually needs to update. In that case, you can improve performance by separating blocking updates that must be synchronous (like typing into an input) from non-blocking updates which don't need to block the user interface (like updating a chart).  
To prioritize rendering, use one of these Hooks:  
*   [`useTransition`](/reference/react/useTransition) lets you mark a state transition as non-blocking and allow other updates to interrupt it.
*   [`useDeferredValue`](/reference/react/useDeferredValue) lets you defer updating a non-critical part of the UI and let other parts update first.  
* * *

**[^2]: [useEffect – React](https://react.dev/reference/react/useEffect)**

### Wrapping Effects in custom Hooks

Effects are an "escape hatch": you use them when you need to "step outside React" and when there is no better built-in solution for your use case. If you find yourself often needing to manually write Effects, it's usually a sign that you need to extract some custom Hooks for common behaviors your components rely on.

For example, this `useChatRoom` custom Hook "hides" the logic of your Effect behind a more declarative API:

```
function useChatRoom({ serverUrl, roomId }) {  useEffect(() => {    const options = {      serverUrl: serverUrl,      roomId: roomId    };    const connection = createConnection(options);    connection.connect();    return () => connection.disconnect();  }, [roomId, serverUrl]);}
```

Then you can use it from any component like this:

```
function ChatRoom({ roomId }) {  const [serverUrl, setServerUrl] = useState('https://localhost:1234');  useChatRoom({    roomId: roomId,    serverUrl: serverUrl  });  // ...
```

There are also many excellent custom Hooks for every purpose available in the React ecosystem.

Learn more about wrapping Effects in custom Hooks.

#### Examples of wrapping Effects in custom Hooks

1. Custom `useChatRoom` Hook 2. Custom `useWindowListener` Hook 3. Custom `useIntersectionObserver` Hook

#### 

Example 1 of 3:

Custom `useChatRoom` Hook

This example is identical to one of the earlier examples, but the logic is extracted to a custom Hook.

App.jsuseChatRoom.jschat.js

App.js

ResetFork

import { useState } from 'react';
import { useChatRoom } from './useChatRoom.js';

function ChatRoom({ roomId }) {
const \[serverUrl, setServerUrl\] = useState('https://localhost:1234');

useChatRoom({
  roomId: roomId,
  serverUrl: serverUrl
});

return (
  <\>
    <label\>
      Server URL:{' '}
       setServerUrl(e.target.value)}
      />
    </label\>
    <h1\>Welcome to the {roomId} room!</h1\>
  </\>
);
}

export default function App() {
const \[roomId, setRoomId\] = useState('general');
const \[show, setShow\] = useState(false);
return (
  <\>
    <label\>
      Choose the chat room:{' '}
      <select
        value\={roomId}
        onChange\={e \=> setRoomId(e.target.value)}
      \>
        <option value\="general"\>general</option\>
        <option value\="travel"\>travel</option\>
        <option value\="music"\>music</option\>
      </select\>
    </label\>
     setShow(!show)}\>
      {show ? 'Close chat' : 'Open chat'}
    
    {show && }
    {show && <ChatRoom roomId\={roomId} />}
  </\>
);
}

Show more

Next Example

* * *

### Controlling a non-React widget

Sometimes, you want to keep an external system synchronized to some prop or state of your component.

For example, if you have a third-party map widget or a video player component written without React, you can use an Effect to call methods on it that make its state match the current state of your React component. This Effect creates an instance of a `MapWidget` class defined in `map-widget.js`. When you change the `zoomLevel` prop of the `Map` component, the Effect calls the `setZoom()` on the class instance to keep it synchronized:

App.jsMap.jsmap-widget.js

Map.js

ResetFork

import { useRef, useEffect } from 'react';
import { MapWidget } from './map-widget.js';

export default function Map({ zoomLevel }) {
const containerRef = useRef(null);
const mapRef = useRef(null);

useEffect(() \=> {
  if (mapRef.current === null) {
    mapRef.current = new MapWidget(containerRef.current);
  }

  const map = mapRef.current;
  map.setZoom(zoomLevel);
}, \[zoomLevel\]);

return (
  
);
}

Show more

In this example, a cleanup function is not needed because the `MapWidget` class manages only the DOM node that was passed to it. After the `Map` React component is removed from the tree, both the DOM node and the `MapWidget` class instance will be automatically garbage-collected by the browser JavaScript engine.

* * *

**[^3]: [Components: Image (Legacy) | Next.js](https://nextjs.org/docs/pages/api-reference/components/image-legacy)**

API ReferenceComponentsImage (Legacy)