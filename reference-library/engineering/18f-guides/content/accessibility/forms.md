---
title: "Forms"
source: 18F Guides
source_url: https://github.com/18F/guides
licence: CC0-1.0
domain: engineering
subdomain: 18f-guides
date_added: 2026-04-25
---

Making forms accessible is a simple process. Each form element should be associated with its instructions and errors, and everything should be accessible via the keyboard.

## Testing

1. Identify each form element.
2. Find all instructions associated with each element.
    * __It is a failure if a form element isn't programmatically associated with _all_ instructions. This includes legends, labels, hint text and tooltips.__
    * A common way of achieving this is using `fieldset` and `legend` tags. `Fieldset` is used to group a set of elements. `Legend` is the first child of a `fieldset` tag and provides context for those fields. 
3. Ensure all field elements are accessible via the keyboard.
    * __If the form cannot be filled out with just a keyboard, this is a failure.__
4. Check for title attributes
    * Title attributes can be a substitute for labels.
    * __If the title attributes provides all the related information it passes, if it provides extra information it fails.__
        * Title attributes are not accessible via keyboard.

## Examples
### Passes

<fieldset>
  <legend>Name</legend>
  <label for="firstname">First</label>
  
  <label for="lastname">Last</label>
  
</fieldset>

<fieldset>
  <legend>Favorite Soup?</legend>
  <label for="peasoup">Pea Soup</label>
  <label for="chicken">Chicken Noodle</label>
  <label for="tomato">Tomato</label>
</fieldset>


```html
<fieldset>
  <legend>Name</legend>
  <label for="firstname">First</label>
  
  <label for="lastname">Last</label>
  
</fieldset>

<fieldset>
  <legend>Favorite Soup?</legend>
  <label for="peasoup">Pea Soup</label>
  <label for="chicken">Chicken Noodle</label>
  <label for="tomato">Tomato</label>
</fieldset>
```
> ___Name:___ Each form element has a ```label```, and it's associated with the ```for``` attribute. The ```for``` attribute refers to the ```id``` of the ```input```. When looking at this form, 'First' and 'Last' wouldn't make sense without 'Name.' This is associated with the ```fieldset``` and ```legend```. All elements are wrapped in a ```fieldset```. There can only be one ```legend``` tag per ```fieldset```. Anything in the ```legend``` tag will be associated.

> ___Favorite Soup:___ ```Fieldset``` and ```legend``` is often used for radio buttons as its the easiest way to associate the radio buttons with the question. Notice there are no ```label```s for the radio buttons, but each button has a ```title``` attribute for assistive technology to read.

### Fails

<fieldset class="exampleFailure" data-pa11y-ignore>
  <legend>Name</legend>
  <label for="first_name-2">First</label>
  
  <label for="1lastname">Last</label>
  
</fieldset>

<fieldset class="exampleFailure">
  <legend>Favorite Soup?</legend>
  This Question Is Required
  <label for="pea-2">Pea Soup</label>
  <label for="chicken-2">Chicken Noodle</label>
  <label for="tomato-2">Tomato</label>
</fieldset>


```html
<fieldset>
  <legend>Name</legend>
  <label for="first_name-2">First</label>
  
  <label for="1lastname">Last</label>
  
</fieldset>

<fieldset>
  <legend>Favorite Soup?</legend>
  This Question Is Required
  <label for="pea-2">Pea Soup</label>
  <label for="chicken-2">Chicken Noodle</label>
  <label for="tomato-2">Tomato</label>
</fieldset>

```

> ___Failure:___ First name label ```for``` and ```id``` don't match.

> ___Failure:___ "This Question Is Required" is not associated with the form fields.

> ___Failure:___ The ```title``` tag for Pea Soup indicates it's 'Chick Pea Soup.' This information is not available to keyboard, sighted users.


### How ARIA affects form inputs

Screen readers vary on what they read and the additional information they provide by default. This is a broad summary of what is read based on VoiceOver for Mac OSX.

You can test these with your own screen reader. If you have a OSX you can turn VoiceOver on by hitting command+F5.

**Further information** Using `aria-label` or `aria-labelledby` will cause a screen reader to only read them and not the default label. If you want an input to read from multiple things like an error message, use `aria-labelledby` and pass it the `for` attribute of the label and any additional `id`s you want read. ex. `aria-labelledby='car1 car_description car-error-message'`

#### No ARIA

Reads just the `label` and not the description

<label for="car_1">Car</label>

Please enter Make and Model

```html
<label for="car_1">Car</label>

Please enter Make and Model
```

**Screen reader reads input as:** `Car Edit text`


#### With aria-label

Reads the `aria-label` and doesn't read the normal `label`.

<label for="car_2">Car</label>

Please enter Make and Model

```html
<label for="car_2">Car</label>

Please enter Make and Model
```

**Screen reader reads input as:** `Car, please enter make and model Edit text`


#### With aria-labelledby pointing at `carmakedescription`

Reads only the `aria-labelledby` attribute and not the default label

<label for="car_3">Car</label>

Please enter Make and Model

```html
<label for="car_3">Car</label>

Please enter Make and Model
```

**Screen reader reads input as:** `Please enter Make and Model Edit text`


#### With aria-labelledby pointing at `carlabel carmakedescription`

Reads both labels indicated by the `aria-labelledby` attribute

<label for="car_4" id="carlabel_4">Car</label>

Please enter Make and Model

```html
<label for="car_4" id="carlabel_4">Car</label>

Please enter Make and Model
```

**Screen reader reads input as:** `Car Please enter Make and Model Edit text`


#### With aria-describedby pointing at `carmakedescription`

JAWS reads both the label and the description. So does VoiceOver, but there is a slight delay before it reads the description.

<label for="car_5">Car</label>

Please enter Make and Model

```html
<label for="car_5">Car</label>

Please enter Make and Model
```

**Screen reader reads input as:** `Car Edit text Please enter Make and Model`
