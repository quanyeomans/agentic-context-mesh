## Effect Hooks[](#effect-hooks "Link for Effect Hooks ")  
_Effects_ let a component [connect to and synchronize with external systems.](/learn/synchronizing-with-effects) This includes dealing with network, browser DOM, animations, widgets written using a different UI library, and other non-React code.  
*   [`useEffect`](/reference/react/useEffect) connects a component to an external system.  
function ChatRoom({ roomId }) {  useEffect(() => {    const connection = createConnection(roomId);    connection.connect();    return () => connection.disconnect();  }, [roomId]);  // ...  
Effects are an "escape hatch" from the React paradigm. Don't use Effects to orchestrate the data flow of your application. If you're not interacting with an external system, [you might not need an Effect.](/learn/you-might-not-need-an-effect)  
There are two rarely used variations of `useEffect` with differences in timing:  
*   [`useLayoutEffect`](/reference/react/useLayoutEffect) fires before the browser repaints the screen. You can measure layout here.
*   [`useInsertionEffect`](/reference/react/useInsertionEffect) fires before React makes changes to the DOM. Libraries can insert dynamic CSS here.  
* * *