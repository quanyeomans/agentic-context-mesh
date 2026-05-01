## Your first component[](#your-first-component "Link for Your first component ")  
React applications are built from isolated pieces of UI called _components_. A React component is a JavaScript function that you can sprinkle with markup. Components can be as small as a button, or as large as an entire page. Here is a `Gallery` component rendering three `Profile` components:  
App.js  
App.js  
Reset[Fork](https://codesandbox.io/api/v1/sandboxes/define?undefined&environment=create-react-app "Open in CodeSandbox")  
function Profile() {
return (

);
}  
export default function Gallery() {
return (
<section>
<h1>Amazing scientists</h1>
<Profile />
<Profile />
<Profile />
</section>
);
}  
Show more

**[^6]: [AI SDK](https://sdk.vercel.ai)**