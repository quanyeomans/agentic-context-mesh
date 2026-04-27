---
title: "Sample Quality Assurance Surveillance Plan (QASP)"
source: 18F Guides
source_url: https://github.com/18F/guides
licence: CC0-1.0
domain: engineering
subdomain: 18f-guides
date_added: 2026-04-25
---

{% capture documentedCodeStandard %}
All dependencies are listed and the licenses are documented Major functionality in the software/source code is documented in plain language

Individual methods are documented in-line using comments that permit the use of documentation generation tools such as [JSDoc]({{ "https://jsdoc.app/" | url }})

A system diagram is provided
{% endcapture %}

<table class="width-full">
  <thead>
    <tr>
      <th class="width-25-percent">Quality indicator for</th>
      <th class="width-25-percent">Performance standard</th>
      <th class="width-25-percent">Acceptable quality level</th>
      <th class="width-25-percent">Method of assessment</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Tested code</td>
      <td>Code delivered under the order must have substantial test code coverage and a clean code base</td>
      <td>Minimum of 90% test coverage of all code</td>
      <td>Automated testing</td>
    </tr>
    <tr>
      <td>Properly styled code</td>
      <td>Meets acceptable quality level</td>
      <td>0 linting errors and 0 warnings</td>
      <td>Styling standards and linters</td>
    </tr>
    <tr>
      <td>Accessibility</td>
      <td>[Web Content Accessibility Guidelines 2.2 – ‘AA’ standards]({{ )</td>
      <td>0 errors reported using an automated scanner, and 0 errors reported in manual testing</td>
      <td>Automated and manual testing</td>
    </tr>
    <tr>
      <td>Deployed code</td>
      <td>Code must successfully build and deploy into a staging environment</td>
      <td>Successful build with a single command</td>
      <td>Live demonstration</td>
    </tr>
    <tr>
      <td>Documented code</td>
      <td>{{ documentedCodeStandard }}</td>
      <td>Vendor provides above documentation</td>
      <td>Manual review</td>
    </tr>
    <tr>
      <td>Security</td>
      <td>[Open Web Application Security Project (OWASP) Application Security Verification Standard 4.0.3]({{ )</td>
      <td>Code submitted must be free of medium- and high-level static and dynamic security vulnerabilities</td>
      <td>Evidence of automated testing per OWASP</td>
    </tr>
    <tr>
      <td>User research</td>
      <td>Usability testing and other user research methods are conducted at regular intervals throughout the development process (not just at the beginning or end)</td>
      <td>Artifacts from usability testing and/or other research methods with end users are available at the end of every applicable sprint in accordance with the vendor’s research plan</td>
      <td>Demonstrated evidence of user research best practices</td>
    </tr>
  </tbody>
</table>
