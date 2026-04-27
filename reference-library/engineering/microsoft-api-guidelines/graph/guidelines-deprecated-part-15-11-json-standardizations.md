## 11. JSON standardizations
### 11.1. JSON formatting standardization for primitive types
Primitive values MUST be serialized to JSON following the rules of [RFC8259][rfc-8259].

**Important note for 64bit integers:** JavaScript will silently truncate integers larger than `Number.MAX_SAFE_INTEGER` (2^53-1) or numbers smaller than `Number.MIN_SAFE_INTEGER` (-2^53+1). If the service is expected to return integer values outside the range of safe values, strongly consider returning the value as a string in order to maximize interoperability and avoid data loss.

### 11.2. Guidelines for dates and times
#### 11.2.1. Producing dates
Services MUST produce dates using the `DateLiteral` format, and SHOULD use the `Iso8601Literal` format unless there are compelling reasons to do otherwise.
Services that do use the `StructuredDateLiteral` format MUST NOT produce dates using the `T` kind unless BOTH the additional precision is REQUIRED, and ECMAScript clients are explicitly unsupported.
(Non-Normative statement: When deciding which particular `DateKind` to standardize on, the approximate order of preference is `E, C, U, W, O, X, I, T`.
This optimizes for ECMAScript, .NET, and C++ programmers, in that order.)

#### 11.2.2. Consuming dates
Services MUST accept dates from clients that use the same `DateLiteral` format (including the `DateKind`, if applicable) that they produce, and SHOULD accept dates using any `DateLiteral` format.

#### 11.2.3. Compatibility
Services MUST use the same `DateLiteral` format (including the same `DateKind`, if applicable) for all resources of the same type, and SHOULD use the same `DateLiteral` format (and `DateKind`, if applicable) for all resources across the entire service.

Any change to the `DateLiteral` format produced by the service (including the `DateKind`, if applicable) and any reductions in the `DateLiteral` formats (and `DateKind`, if applicable) accepted by the service MUST be treated as a breaking change.
Any widening of the `DateLiteral` formats accepted by the service is NOT considered a breaking change.

### 11.3. JSON serialization of dates and times
Round-tripping serialized dates with JSON is a hard problem.
Although ECMAScript supports literals for most built-in types, it does not define a literal format for dates.
The Web has coalesced around the [ECMAScript subset of ISO 8601 date formats (ISO 8601)][iso-8601], but there are situations where this format is not desirable.
For those cases, this document defines a JSON serialization format that can be used to unambiguously represent dates in different formats.
Other serialization formats (such as XML) could be derived from this format.

#### 11.3.1. The `DateLiteral` format
Dates represented in JSON are serialized using the following grammar.
Informally, a `DateValue` is either an ISO 8601-formatted string or a JSON object containing two properties named `kind` and `value` that together define a point in time.
The following is not a context-free grammar; in particular, the interpretation of `DateValue` depends on the value of `DateKind`, but this minimizes the number of productions required to describe the format.

```
DateLiteral:
  Iso8601Literal
  StructuredDateLiteral

Iso8601Literal:
  A string literal as defined in https://www.ecma-international.org/ecma-262/5.1/#sec-15.9.1.15. Note that the full grammar for ISO 8601 (such as "basic format" without separators) is not supported.
  All dates default to UTC unless specified otherwise.

StructuredDateLiteral:
  { DateKindProperty , DateValueProperty }
  { DateValueProperty , DateKindProperty }

DateKindProperty
  "kind" : DateKind

DateKind:
  "C"            ; see below
  "E"            ; see below
  "I"            ; see below
  "O"            ; see below
  "T"            ; see below
  "U"            ; see below
  "W"            ; see below
  "X"            ; see below

DateValueProperty:
  "value" : DateValue

DateValue:
  UnsignedInteger        ; not defined here
  SignedInteger        ; not defined here
  RealNumber        ; not defined here
  Iso8601Literal        ; as above
```

#### 11.3.2. Commentary on date formatting
A `DateLiteral` using the `Iso8601Literal` production is relatively straightforward.
Here is an example of an object with a property named `creationDate` that is set to February 13, 2015, at 1:15 p.m. UTC:

```json
{ "creationDate" : "2015-02-13T13:15Z" }
```

The `StructuredDateLiteral` consists of a `DateKind` and an accompanying `DateValue` whose valid values (and their interpretation) depend on the `DateKind`. The following table describes the valid combinations and their meaning:

DateKind | DateValue       | Colloquial Name & Interpretation                                                                                                                  | More Info
-------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------
C        | UnsignedInteger | "CLR"; number of milliseconds since midnight January 1, 0001; negative values are not allowed. *See note below.*                                  | [MSDN][clr-time]
E        | SignedInteger   | "ECMAScript"; number of milliseconds since midnight, January 1, 1970.                                                                             | [ECMA International][ecmascript-time]
I        | Iso8601Literal  | "ISO 8601"; a string limited to the ECMAScript subset.                                                                                            |
O        | RealNumber      | "OLE Date"; integral part is the number of days since midnight, December 31, 1899, and fractional part is the time within the day (0.5 = midday). | [MSDN][ole-date]
T        | SignedInteger   | "Ticks"; number of ticks (100-nanosecond intervals) since midnight January 1, 1601. *See note below.*                                             | [MSDN][ticks-time]
U        | SignedInteger   | "UNIX"; number of seconds since midnight, January 1, 1970.                                                                                        | [MSDN][unix-time]
W        | SignedInteger   | "Windows"; number of milliseconds since midnight January 1, 1601. *See note below.*                                                               | [MSDN][windows-time]
X        | RealNumber      | "Excel"; as for `O` but the year 1900 is incorrectly treated as a leap year, and day 0 is "January 0 (zero)".                                     | [Microsoft Support][excel-time]

**Important note for `C` and `W` kinds:** The native CLR and Windows times are represented by 100-nanosecond "tick" values.
To interoperate with ECMAScript clients that have limited precision, _these values MUST be converted to and from milliseconds_ when (de)serialized as a `DateLiteral`.
One millisecond is equivalent to 10,000 ticks.

**Important note for `T` kind:** This kind preserves the full fidelity of the Windows native time formats (and is trivially convertible to and from the native CLR format) but is incompatible with ECMAScript clients.
Therefore, its use SHOULD be limited to only those scenarios that both require the additional precision and do not need to interoperate with ECMAScript clients.

Here is the same example of an object with a property named creationDate that is set to February 13, 2015, at 1:15 p.m. UTC, using several formats:

```json
[
  { "creationDate" : { "kind" : "O", "value" : 42048.55 } },
  { "creationDate" : { "kind" : "E", "value" : 1423862100000 } }
]
```

One of the benefits of separating the kind from the value is that once a client knows the kind used by a particular service, it can interpret the value without requiring any additional parsing.
In the common case of the value being a number, this makes coding easier for developers:

```csharp
// We know this service always gives out ECMAScript-format dates
var date = new Date(serverResponse.someObject.creationDate.value);
```

### 11.4. Durations
[Durations][wikipedia-iso8601-durations] need to be serialized in conformance with [ISO 8601][wikipedia-iso8601-durations].
Durations are "represented by the format `P[n]Y[n]M[n]DT[n]H[n]M[n]S`."
From the standard:
- P is the duration designator (historically called "period") placed at the start of the duration representation.
- Y is the year designator that follows the value for the number of years.
- M is the month designator that follows the value for the number of months.
- W is the week designator that follows the value for the number of weeks.
- D is the day designator that follows the value for the number of days.
- T is the time designator that precedes the time components of the representation.
- H is the hour designator that follows the value for the number of hours.
- M is the minute designator that follows the value for the number of minutes.
- S is the second designator that follows the value for the number of seconds.

For example, "P3Y6M4DT12H30M5S" represents a duration of "three years, six months, four days, twelve hours, thirty minutes, and five seconds."

### 11.5. Intervals
[Intervals][wikipedia-iso8601-intervals] are defined as part of [ISO 8601][wikipedia-iso8601-intervals].
- Start and end, such as "2007-03-01T13:00:00Z/2008-05-11T15:30:00Z"
- Start and duration, such as "2007-03-01T13:00:00Z/P1Y2M10DT2H30M"
- Duration and end, such as "P1Y2M10DT2H30M/2008-05-11T15:30:00Z"
- Duration only, such as "P1Y2M10DT2H30M", with additional context information

### 11.6. Repeating intervals
[Repeating Intervals][wikipedia-iso8601-repeatingintervals], as per [ISO 8601][wikipedia-iso8601-repeatingintervals], are:

> Formed by adding "R[n]/" to the beginning of an interval expression, where R is used as the letter itself and [n] is replaced by the number of repetitions.
Leaving out the value for [n] means an unbounded number of repetitions.

For example, to repeat the interval of "P1Y2M10DT2H30M" five times starting at "2008-03-01T13:00:00Z", use "R5/2008-03-01T13:00:00Z/P1Y2M10DT2H30M."