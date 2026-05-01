## Action Operations

Most operations conform to one of the standard REST Create, Read, Update, Delete, or List (CRUDL) style of operations. We refer to all other operations as "action" operations. Some examples of action operations are to reboot a VM, or send an email.

It is good practice to define the path for action operations that is easily distinguished from any resource path of the service. When services allow user-specified resource ids (also a good practice), the recommended approach for this is:
1) constrain user-specified resource ids to allow only certain characters, such as alphanumeric and '-' or '_', and
2) use a special character not in the set of valid characters for resource names to distinguish the "action" in the path.

In Azure we recommend distinguishing action operations by appending a ':' followed by an action verb to the final path segment.  E.g.
```text
https://.../<resource-collection>/<resource-id>:<action>?
```

Other patterns are possible. The key consideration is to ensure that the path for an action operation
cannot collide with a resource path that contains user-specified resource ids.