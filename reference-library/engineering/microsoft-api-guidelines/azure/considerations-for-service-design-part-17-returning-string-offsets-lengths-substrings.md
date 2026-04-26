## Returning String Offsets & Lengths (Substrings)

Some Azure services return substring offset & length values within a string. For example, the offset & length within a string to a name, email address, or phone number.
When a service response includes a string, the client's programming language deserializes that string into that language's internal string encoding. Below are the possible encodings and examples of languages that use each encoding:

| Encoding    | Example languages |
| -------- | ------- |
| UTF-8 | Go, Rust, Ruby, PHP |
| UTF-16 | JavaScript, Java, C# |
| CodePoint (UTF-32) | Python |

Because the service doesn't know in what language a client is written and what string encoding that language uses, the service can't return UTF-agnostic offset and length values that the client can use to index within the string. To address this, the service response must include offset & length values for all 3 possible encodings and then the client code must select the encoding required by its language's internal string encoding.

For example, if a service response needed to identify offset & length values for "name" and "email" substrings, the JSON response would look like this:

```text
{
  (... other properties not shown...)
  "fullString": "(...some string containing a name and an email address...)",
  "name": {
    "offset": {
      "utf8": 12,
      "utf16": 10,
      "codePoint": 4
    },
    "length": {
      "uft8": 10,
      "utf16": 8,
      "codePoint": 2
    }
  },
  "email": {
    "offset": {
      "utf8": 12,
      "utf16": 10,
      "codePoint": 4
    },
    "length": {
      "uft8": 10,
      "utf16": 8,
      "codePoint": 4
    }
  }
}
```

Then, the Go developer, for example, would get the substring containing the name using code like this:

```go
   var response := client.SomeMethodReturningJSONShownAbove(...)
   name := response.fullString[ response.name.offset.utf8 : response.name.offset.utf8 + response.name.length.utf8]
```

The service must calculate the offset & length for all 3 encodings and return them because clients find it difficult working with Unicode encodings and how to convert from one encoding to another. In other words, we do this to simplify client development and ensure customer success when isolating a substring.