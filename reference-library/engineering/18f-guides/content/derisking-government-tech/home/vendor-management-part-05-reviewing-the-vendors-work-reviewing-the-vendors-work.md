## Reviewing the vendor’s work {#reviewing-the-vendors-work}

{% include 'derisking-government-tech/section_image.html' img_path: "assets/derisking-government-tech/img/vendors-reviewing.svg" %}

Good vendor management rests on the government’s power and capacity to accept the vendor’s work or ask for rework. This is much more than just quality assurance. Reviewing the vendor’s work effectively requires understanding the software’s intent and acceptable trade-offs. It also requires focusing on the software and not relying on reports about the project’s progress. 

A significant part of the review is checking that the software’s functionality meets user needs. Review should also check the work against the project’s “quality indicators,” which enable the government to assess if a vendor development team’s work meets the expectations for quality laid out in the contract.

### Quality indicators {#quality-indicators}

In software development, “quality” is sometimes assessed narrowly as a lack of defects or bugs. A more productive way to approach assessing quality is to set clear, positive expectations upfront and monitor them continuously throughout a project. Doing so will enable you to build a high-quality product and maintain a healthy vendor relationship. 

As explained earlier, [quality expectations and indicators for your project should be incorporated into the solicitation]({{ "/derisking-government-tech/buying-development-services/#quality-indicators-defined-in-a-quality-assurance-surveillance-plan" | url }}) and open to questions from vendors before they submit a proposal. Communicating these from the get-go lowers the risk of friction between you and the chosen vendor.

It is reasonable and consistent with private-sector tech practices to ask to see proof from your vendor that they are meeting expectations for quality, so don’t be shy about reviewing quality indicators. Think of them as the vital signs to be checked regularly during a project that help make sure the vendor is building high-quality and maintainable software. 

In general, good quality indicators: 

* Create a space for conversations between government and vendor to keep work on track  
* Focus on necessary, user-centered work products  
* Are grounded in common professional standards  
* Rely on facts, not opinions  
* Don’t create additional work for you or the vendor  
* Give the vendor freedom to meet the criteria in a variety of ways

The vendor should be able to demonstrate they are meeting such quality indicators without additional work. Automation tools collect most of the necessary data by default. 

{% capture monitorQualityContent %}
#### When

After the contract is awarded, the project’s quality indicators should be reviewed at every sprint, usually every two weeks, as part of acceptance of vendor work. 

#### Who

To avoid a conflict of interest, a government employee with sufficient technical knowledge should conduct the review. This may either be the agency product owner or a government technical lead assigned to the project, depending on the requirement. 

#### Method

Indicators are evaluated using two methods: manual review and automated testing. 

In a **manual review**, the government reviewer looks at the deliverable and judges whether it meets the standard set by the expectation stated in the contract. For example, the indicator “documented code” is not satisfied by the existence of documentation. The reviewer must judge if the documentation adequately explains the code.

**Automated testing** is done using tools that run tests every time code is submitted, or by some other trigger in the software development workflow. The vendor should set up the tests and provide the evaluator with the results from the testing tools.

After an initial evaluation of all of the quality indicators, the reviewer should talk to the vendor about the results, good or bad. This dialogue should align the reviewer’s and the vendor’s expectations and address early signs of problems or other concerns. Repeated failures to meet quality expectations should be documented and escalated to the appropriate contracting officials.
{% endcapture %}

{% include 'derisking-government-tech/info_box.html' header: "How to monitor quality indicators" content: monitorQualityContent %}

### 18F quality indicators 

18F teams use the following quality indicators in our projects with agency partners. We recommend these as a minimum set that [should be stated in a solicitation and contract for custom software development]({{ "/derisking-government-tech/buying-development-services/#writing-the-solicitation-using-18fs-agile-contract-format" | url }}). They are presented and explained below in the form by which they’re known at the federal level: [Quality Assurance Surveillance Plan (QASP)]({{ "https://www.acquisition.gov/far/37.604" | url }}). 

Each indicator is accompanied by a method or methods that make it easy to review and document. Expect a software vendor experienced in modern software development practices to be able to easily demonstrate they are meeting them at every sprint review. 

Modify our set of indicators to meet the standards and requirements of your agency and project as needed. If, for example, your agency has more thorough requirements for testing accessibility, those are the performance standards you should use. 

When writing a quality indicator, make sure it:

* States a performance standard(s) that is short and clear  
* Is measurable on an ongoing basis   
* Is work that lies within a vendor team’s scope and capacity to control   
* Is not a specific program outcome, such as reduction in processing times, payment accuracy, etc. (The government can’t pass responsibility for program outcomes to a software vendor. A vendor can follow the program’s assessment of how to generate outcomes, but the program is responsible for the ultimate results.) 

#### Tested code

| Performance standard{.width-33-percent} | Acceptable quality level{.width-33-percent} | Method of assessment{.width-33-percent} |
| :---- | :---- | :---- |
| Code delivered under the order must have substantial test code coverage and a clean code base | Minimum of 90% test coverage of all code | Automated testing |

Testing is an essential practice for developing functional software that performs well. 

Developers write automated tests alongside their code that find flaws and/or verify that features function the way they were designed to. As code develops, tests are added so that future changes and additions run through the entire “suite” of tests. This practice ensures that revised and new code don’t break features and functionality.

To meet this quality standard, a software developer must: 

* Use automated testing tools  
* Write automated tests for the code they develop  
* Address the issues that surface in testing immediately 

A developer can easily demonstrate that they’re following these practices by producing summaries of the automated tests that show the code base passes all of the project’s tests.

An important high-level indicator of quality in those reports is **code coverage**, or what percentage of the code base in the project is executed or touched by the automated tests. Code that isn’t covered by any tests is a source of potential errors and a liability for future development. Expect a high threshold for coverage: 18F’s standard is 90 percent. The 10 percent allows a buffer for how much code can be uncovered since full coverage is not always practical. For instance, the developers may have determined that a certain function should be tested manually. 

Automated testing of code isn’t perfect. New errors can get through even with code coverage and testing. When an error gets through automated testing, a developer must fix it and write an automated test for that particular error so that it is caught in the future. 

If a developer can demonstrate they are regularly using automated testing that provides 90 percent or more code coverage and fixes errors quickly, they have tools and practices in place for satisfying the quality indicator of tested code.

#### Properly styled code

| Performance standard{.width-33-percent} | Acceptable quality level{.width-33-percent} | Method of assessment{.width-33-percent} |
| :---- | :---- | :---- |
| Meeting acceptable quality level for this indicator | 0 linting errors and 0 warnings | Styling standards and linters |

**Code style** refers to established standards for writing and formatting a programming language. This practice maintains the readability and consistency of code so that it’s easy to review and future developers can understand and maintain it. 

Every programming language has its own code styling standards, much like there are various style guides for writing. To help them adhere to a code style, developers use code “linters,” which test code against a style’s rules and show code that needs to be changed to meet the chosen styling standard. 

From a quality perspective, it is important that the vendor uses the chosen code style consistently and that styling errors or warnings caught by the code linter are corrected before the code is delivered and integrated into the product. 

As with automated testing, a developer who is following these standards and using linters can easily and regularly produce output from the tool that shows there are currently no styling errors or warnings.

Review 18F’s recommendation for linters for [JavaScript]({{ "https://guides.18f.gov/engineering/languages-runtimes/javascript/#style" | url }}) and [CSS]({{ "https://guides.18f.gov/engineering/languages-runtimes/css/#linting" | url }}).

#### Accessibility

| Performance standard{.width-33-percent} | Acceptable quality level{.width-33-percent} | Method of assessment{.width-33-percent} |
| :---- | :---- | :---- |
| [Web Content Accessibility Guidelines 2.2 – ‘AA’ standards]({{ "https://www.w3.org/TR/WCAG22/" | url }}) | 0 errors reported using an automated scanner, and 0 errors reported in manual testing | Automated and manual testing |

Note: [Section 508]({{ "https://www.section508.gov/" | url }}) obligates federal agencies to make all their public-facing websites and digital services accessible. [Many states have their own accessibility standards.]({{ "https://www.section508.gov/manage/laws-and-policies/state/" | url }})

To meet full compliance with accessibility standards requires using automated and manual testing. 

Developers of public-facing websites can check that their projects meet common accessibility standards by using an open source accessibility testing tool, like [Pa11y]({{ "https://pa11y.org/" | url }}). **Accessibility testing tools** run a series of automated tests on a site that detect accessibility issues. 

Expect accessibility tests to be included in the suite of automated testing tools set up during a project and that you’ll review their results every sprint. Integrating regular automated accessibility testing during development will keep the project on a path towards meeting this quality expectation. 

Manual testing requires more effort than automated, which makes it impractical to do every sprint. An initial manual test should be done when the project’s main functions and interactions can be tested. This initial review will set the base line for the project and often reveals a number of accessibility issues that need to be addressed. 

To resolve the issues, prioritize them into categories of critical, moderate, and low priority:

* Critical issues pose serious accessibility challenges that will exclude users and should be addressed immediately.   
* Moderate issues should be resolved within the next sprint.   
* Low priority issues can be added to the project backlog and scheduled with other project tasks. 

The development team should conduct a manual review for each major release to the project. These reviews should build on the base line and only test the portions of the project that have changed. The team should also document the review and remediation process for each accessibility issue in each phase of testing so there is an ongoing record.

Refer to the [18F Accessibility Guide]({{ "https://guides.18f.gov/accessibility/checklist/" | url }}) for a comprehensive checklist and descriptions of accessibility issues and how to test for them. 

#### Deployed

| Performance standard{.width-33-percent} | Acceptable quality level{.width-33-percent} | Method of assessment{.width-33-percent} |
| :---- | :---- | :---- |
| Code must successfully build and deploy into a staging environment  | Successful build with a single command | Live demonstration |

Modern development processes approach deployment of code through **continuous integration and continuous deployment (CI/CD)**. These tools create a development “pipeline” that automatically builds and deploys the project so it can be tested and then deployed to a production server that runs the public-facing site.

Automated CI/CD tools, which are integrated into version control systems like GitHub, make this practice possible. These tools and practices make it easier to maintain software and quickly make changes in response to user needs.

A development team can demonstrate it has set up the pipeline using CI/CD practices and tools if it is able to deploy a change to the testing (also called “staging”) or public-facing production environment at any time with just a single command. The deployment process should be comprehensively documented in plain language so it is understandable to non-technical agency staff.

#### Documentation 

| Performance standards{.width-33-percent} | Acceptable quality level{.width-33-percent} | Method of assessment{.width-33-percent} |
| :---- | :---- | :---- |
| <li>All dependencies are listed and the licenses are documented</li><li>Major functionality in the software/source code is documented in plain language</li><li>Individual methods are documented in-line using comments that permit the use of documentation generation tools such as [JSDoc]({{ "https://jsdoc.app/" | url }})</li><li>A system diagram is provided</li> | Vendor provides documentation as specified in this section  | Manual review |

As the owner of the software created by the vendor, you need accurate and current documentation of the software so future developers can understand how it was built and why various decisions were made.

There are two types of documentation:

* **In-line documentation** is written into the code as comments that describe what specific pieces of code do.   
* **Supplementary documentation** is written explanation of how the system works, its major functions, and any open source software “dependencies” required to run it. 

For maintenance, it’s also important to document:

* Tools used during the project  
* Software licenses for the tools  
* How to get access to the tools  
* Where log-ins are stored

This documentation is especially crucial if the system will be transitioned from the vendor to the agency. 

The expectation for this quality indicator is that new code and documentation of it are written at the same time. It is most efficient to document new code as it is written and more likely to be accurate.

#### Security

| Performance standard{.width-33-percent} | Acceptable quality level{.width-33-percent} | Method of assessment{.width-33-percent} |
| :---- | :---- | :---- |
| [Open Web Application Security Project (OWASP) Application Security Verification Standard 4.0.3]({{ "https://owasp.org/www-project-application-security-verification-standard/" | url }}) | Code submitted must be free of medium- and high-level static and dynamic security vulnerabilities | Evidence of automated testing per OWASP |

Make security testing a regular part of the sprint review process. Addressing vulnerabilities when they arise will reduce the risk that the project launches with significant security flaws. These practices should make it easy for a vendor to meet the hosting agency’s security and compliance standards.

To check that applications are free from known security vulnerabilities, developers use open source, community-developed security standards like OWASP, and scanning tools that perform automated testing of applications against those standards. 

Security scanning involves static and dynamic analysis. **Static scanning** refers to scanning the source code for vulnerabilities. **Dynamic scanning** refers to security tests of the application that determine if it is protected against common security vulnerabilities. 

As with other automated tests, the vendor should be able to demonstrate the code in its current state doesn’t have any vulnerabilities that are classified by OWASP as either medium- or high-level static or dynamic vulnerabilities.

Learn more about [good practices for security in government]({{ "https://guides.18f.gov/engineering/security/" | url }}).

#### User research

| Performance standard{.width-33-percent} | Acceptable quality level{.width-33-percent} | Method of assessment{.width-33-percent} |
| :---- | :---- | :---- |
| Usability testing and other user research methods must be conducted at regular intervals throughout the development process (not just at the beginning or end) | Artifacts from usability testing and/or other research methods with end users are available at the end of every applicable sprint, in accordance with the vendor’s research plan | Demonstrated evidence of user research best practices  |

Designing human-centered software involves many decisions. A development team’s decisions are better when they’re informed by the perspectives of a system’s intended users. This is why it’s critical to know a team is conducting and making decisions based on evidence gained from user research throughout the entire project. 

User research explores possibilities, tests assumptions, and reduces risk in a project by engaging frequently with end users. It includes qualitative and quantitative methods, including [user interviews]({{ "https://methods.18f.gov/discover/stakeholder-and-user-interviews/" | url }}), [usability testing]({{ "https://methods.18f.gov/validate/usability-testing/" | url }}), [journey mapping]({{ "https://methods.18f.gov/decide/journey-mapping/" | url }}), and [card sorting]({{ "https://methods.18f.gov/validate/card-sorting/" | url }}). It also involves investigating tools and systems, and interacting with members of the public. 

The research approach and methods used on a particular project will vary depending on the problem it’s trying to solve, timeline, phase of the project, goals, and constraints.  

When reviewing user research materials, processes, or deliverables, these are good signs that reflect the use of best practices:

* Recruiting from a diverse population  
    * To ensure a product or service is accessible to any user, the team recruits participants with diverse backgrounds, needs, and abilities. This includes recruiting people who may face barriers to using the product or service.  
* Research plan(s) with clear and appropriate goals  
    * Planning ensures that participants’ and the team’s time is respected throughout the research process. It also helps the team adapt its approach in response to real-world conditions. A research plan should include clearly stated and appropriate goals, methods, and research questions.  
* The whole team is part of the research process  
    * It’s a good sign to see active team participation in research planning, observing research sessions, debriefing, and discussing the findings because it indicates shared investment in learning and serving the needs of users. (Every member of a team need not participate in every aspect of research.)   
* Research participants’ privacy is protected  
    * When participants trust you, they are more likely to share full and accurate accounts of their experiences. A large part of maintaining trust with participants involves protecting their privacy. Signs that the vendor is protecting PII (personally identifiable information) include the use of pseudonyms, keeping access to raw notes limited, collecting informed consent, and de-identifying research data before synthesizing.  
* Actionable research findings  
    * After each round of research, the whole team should identify how the research findings change the work planned for the next sprint or for future design efforts. Articulating insights from findings involves various activities that allow the project team to work together to begin to map out larger patterns and themes. 

Learn more about [user research in the 18F User Experience Guide]({{ "https://guides.18f.gov/ux-guide/research/" | url }}).

{% capture codeReviewContent %}
**Code review** refers to the common practice of developers regularly reviewing each other’s code on a project. It is critical to maintaining consistency and quality on a project with many contributors. It allows reviewers to suggest improvements to the code and helps keep everyone on the team aware of what others are doing and how it may affect their own work. 

Code review facilitates the manual review method of assessment required for the code-related indicators explained above. It also helps produce higher quality code with fewer defects. 

While our [sample QASP]({{ "/derisking-government-tech/resources/quality-indicators/" | url }}) doesn’t include a specific quality indicator for code review, expect a vendor team to be engaging in this practice as part of its efforts to meet quality expectations. 

You can ensure a vendor team engages in internal code reviews by asking a vendor how its developers approach them as part of the [proposal evaluation process]({{'/derisking-government-tech/resources/evaluate-bids/' | url}}). It may be done in a formal meeting. It may be done through version control systems like GitHub, in which developers review and approve new code and changes to existing code through “pull requests” before they are integrated or “merged” into the project’s code.

The scope of a code review can range from addressing small issues to large ones. The only rule is that all new code or changes to existing code is being reviewed by *at least one person* before being merged.

It can be a challenge to establish a healthy balance of government involvement in a vendor team’s code reviews. The “right” amount supports the flow of work and doesn’t delay or block it. No involvement increases the risk that the project won’t meet end user needs or critical design flaws won’t be discovered until it is difficult to fix them. Too much can undermine the vendor’s autonomy and motivation to produce quality code independently. For instance, if a government reviewer strictly dictates how something should be done and is not open to dialogue, it can lead to frustration and breakdown in communication. The level of government involvement also depends on the availability of staff with relevant technical expertise. (Consider hiring an independent contractor to act as reviewer if needed.)

Open and regular communication is the key to finding a healthy level. When government experts, or independent contractors working on behalf of the government, are able to participate in the code review process, discuss expectations about their level of involvement with the vendor at the start of work. Then, at every sprint review, proactively solicit the team’s feedback about how that participation is going. Acknowledge and resolve issues before they harm the working relationship.

When technical expertise is not available on the government side to participate in code reviews, ask the vendor to confirm 1\) that they are conducting code reviews, and 2\) to demonstrate, in the form of pull request discussions and approvals, that reviews are happening. 

Learn about [18F Engineering’s approach to code review]({{ "https://guides.18f.gov/engineering/our-approach/code-review/" | url }}).

Review an [example of how to document the code review process in a government technology project]({{ "https://github.com/akhealth/EIS-Modernization/blob/master/code-review.md" | url }}).
{% endcapture %}

{% include 'derisking-government-tech/info_box.html' header: "A note on code review" content: codeReviewContent %}