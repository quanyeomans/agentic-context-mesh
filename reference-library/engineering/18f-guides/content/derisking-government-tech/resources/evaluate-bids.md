---
title: "How to evaluate bids and proposals"
source: 18F Guides
source_url: https://github.com/18F/guides
licence: CC0-1.0
domain: engineering
subdomain: 18f-guides
date_added: 2026-04-25
---

It is common for agencies to use a scoring scheme to evaluate vendor proposals and bids. We recommend a different method that creates a detailed and defensible justification of the government’s vendor selection, which a scoring scheme does not. It also allows the government to give feedback to the vendors that didn’t receive the award by simply summarizing the proposal’s documented pros and cons. 

As you will have explained in the solicitation for a performance-based services contract, this approach regards the three technical evaluation factors — technical approach, staffing approach, similar experience — combined as significantly more important than the price in evaluating the strength of a proposal. 

Following are evaluation criteria for each of those technical factors. Each set includes positive signs and red flags to look for as you review proposals. They aren’t exhaustive, but should help an evaluation team get started and decide which vendors to interview. 

Use our [evaluator worksheet]({{ '/derisking-government-tech/resources/evaluator-worksheet/' | url }}) as a tool during the review process.

---

## Technical approach

Ideally, the vendor proposes to use modern software development practices. The proposed approach should be appropriate for the scope of work and demonstrate technical proficiency. 

Evaluate answers from the [verbal interviews]({{ "/derisking-government-tech/resources/verbal-interview-questions/" | url }}) as part of the technical approach. 

{% capture competencyRedFlags %}

  <li>Misidentifies core technologies in a way that shows inexperience communicating about or using them</li>
  <li>Proposes a highly complex approach or uses highly complex language that confuses rather than clarifies</li>
  <li>Proposes to outsource core technical competencies</li>
  <li>Doesn’t mention using secure code practices</li>
  <li>Doesn’t value testing code</li>

{% endcapture %}

{% capture userResearchPositiveSigns %}

  <li>Expects to conduct regular and ongoing user research to understand user goals and needs, and to use research findings to build features that support those goals and needs</li>
  <li>Includes how qualitative and quantitative data will be leveraged to inform product and design decisions</li>
  <li>Has a plan to conduct user research and test everything from rough prototypes to finished software with actual users throughout the entire design and development process</li>
  <li>Seeks research participants from diverse backgrounds</li>
  <li>Describes target groups for research</li>
  <li>Research will be done with people who will actually use the service, ideally people with diverse perspectives and differing abilities</li>
  <li>Research plan involves people:
    
      <li>Who have disabilities or use assistive technologies With limited digital skills or low literacy
      <li>Who may need help using the service in question
    
  </li>
  <li>Research plan mentions:
    
      <li>Respect for participants
      <li>Informed consent
      <li>Potential harms and how they will be reduced
      <li>Diversity, inclusion, honesty, and transparency
    
  </li>
  <li>Research plan methods are appropriate and the timeline is feasible</li>
  <li>Combines user research with usability testing to ensure that features are meeting user needs</li>

{% endcapture %}

{% capture userResearchRedFlags %}

  <li>Doesn’t indicate that they will use user research to determine the design or the technical approach</li>
  <li>Proposes a process that includes working for long stretches of time without interacting with the agency and/or users</li>
  <li>Proposes using focus groups instead of structured one-on-one research interviews or usability testing sessions</li>
  <li>Doesn’t use research methods appropriate to research goals (e.g., using surveys to uncover user needs or usability testing to validate user goals)</li>
  <li>Design is described as User Acceptance Testing, performed only at the end of a project</li>
  <li>Displays low maturity in UX research and design practices:
    
      <li>Research goals, questions, methods, and expected outcomes don’t align</li>
      <li>Doesn't understand the difference between users and stakeholders</li>
      <li>Doesn’t provide a user recruitment approach or interview protocol provided</li>
    
  </li>

{% endcapture %}

{% capture userCenteredDesignPositiveSigns %}

  <li>Follows a user-centered design process (They explain how they make design decisions in relation to broader user goals and specific needs learned through user research.)</li>
  <li>Indicates that design is considered part of the cross-functional agile development team — it doesn’t operate in a silo</li>

{% endcapture %}

{% capture userCenteredDesignRedFlags %}

  <li>Proposes that requirements will be collected from the business owner, rather than determined according to user needs uncovered through research</li>
  <li>Prioritizes aesthetics over usability and usefulness</li>
  <li>Can’t explain their design decisions</li>

{% endcapture %}

{% capture developmentInfrastructurePositiveSigns %}

  <li>Focuses on automation, reliability, testability, infrastructure as code, etc.</li>
  <li>Refers to modern automation and deployment tooling like Jenkins, Puppet, Chef, Travis CI, CircleCI, Kubernetes, Terraform, AWS, and Heroku</li>

{% endcapture %}

{% capture accessibilityPositiveSigns %}

  <li>Offers specific, detailed description for how the team will build accessibility and testing into the development process</li>
  <li>Lists applicable, up-to-date government accessibility standards</li>

{% endcapture %}

{% capture accessibilityRedFlags %}

  <li>Doesn’t mention accessibility or explain how they will evaluate if their software meets accessibility standards</li>
  <li>Offers “shall comply” without citing specifics, such as Sec. 508 and the protocol for satisfying it</li>

{% endcapture %}

{% capture otherRedFlags %}

  <li>Bypasses page-limit rules in their proposal by using a tiny font size, reduced leading, etc.</li>
  <li>Proposes long-term staff augmentation</li>

{% endcapture %}

<table>
  <thead>
    <th class="evaluate-bids-what-header">What to look for</th>
    <th class="evaluate-bids-pro-con-header">Positive signs</th>
    <th class="evaluate-bids-pro-con-header">Red flags</th>
  </thead>
  <tr>
    <td>Competency</td>
    <td>Demonstrates knowledge of their preferred tools and methods, and is able to explain why they are appropriate for the project</td>
    <td>{{ competencyRedFlags }}</td>
  </tr>
  <tr>
    <td>Lack of novelty</td>
    <td>Recommends established software and infrastructure, as well as use of proven and effective design patterns</td>
    <td></td>
  </tr>
  <tr>
    <td>Lack of certainty</td>
    <td>Highlights areas of uncertainty in their technical approach (Since a vendor can’t know if a proposed approach will be effective until development begins, they should be candid that they can’t be sure.)</td>
    <td></td>
  </tr>
  <tr>
    <td>Vision</td>
    <td>Interprets the intended outcomes in a way that can enable the agency’s vision</td>
    <td></td>
  </tr>
  <tr>
    <td>Program goals</td>
    <td>Demonstrates a clear grasp of the agency’s mission and project’s aims described in the solicitation</td>
    <td>Doesn’t understand program goals that were described clearly in the solicitation</td>
  </tr>
  <tr>
    <td>Open source software</td>
    <td>Has experience developing open source software | Doesn’t have experience developing open source software</td>
    <td></td>
  </tr>
  <tr>
    <td>Collaboration and communication</td>
    <td>Expects to work with an agency product owner and for that person to be an active team member — one who communicates proactively about risks and roadblocks</td>
    <td></td>
  </tr>
  <tr>
    <td>User research</td>
    <td>{{ userResearchPositiveSigns }}</td>
    <td>{{ userResearchRedFlags }}</td>
  </tr>
  <tr>
    <td>User-centered design</td>
    <td>{{ userCenteredDesignPositiveSigns }}</td>
    <td>{{ userCenteredDesignRedFlags }}</td>
  </tr>
  <tr>
    <td>Development infrastructure</td>
    <td>{{ developmentInfrastructurePositiveSigns }}</td>
    <td></td>
  </tr>
  <tr>
    <td>Accessibility</td>
    <td>{{ accessibilityPositiveSigns }}</td>
    <td>{{ accessibilityRedFlags }}</td>
  </tr>
  <tr>
    <td>Other</td>
    <td></td>
    <td>{{ otherRedFlags }}</td>
  </tr>
</table>

## Staffing approach

You want evidence that the staff has experience in their areas of expertise. 

In addition, if the developers have presences on social coding platforms (for example, GitHub, GitLab, Bitbucket), review them to consider: 

* What kinds of projects have they worked on?   
* What languages have they worked with?   
* Is their code readable?   
* Does their code follow best practices for organization?   
* If their projects are open source, are they being actively used or forked?   
* Do their projects show expertise that doesn't appear in their qualifications?

{% capture teamSizeAndRolesRedFlags %}

  <li>Specifies too many [key personnel]({{ ), especially with individuals whose expertise overlaps with that of agency staff</li>
  <li>Over-staffs the bid (If a vendor proposes a team that consists of people with far more experience than necessary, or more people than necessary, it suggests they either don't understand modern software development practices or are just trying to over-staff the engagement.)</li>
  <li>Under-staffs the bid (A vendor might try to win the bid by proposing a smaller team than it knows is needed for the project, with the plan of increasing the size of the team later.)</li>
  <li>Proposes positions that aren’t needed in an iterative development project, such as business analysts, enterprise architects, delivery managers, etc.</li>
  <li>“Access to a database of resumes” is provided, but specific technical staff are not named</li>

{% endcapture %}

{% capture teamCapacityRedFlags %}

  <li>The most qualified team member is allocated a small amount of time on the project</li>
  <li>Proposed staff don’t currently work for the contractor and a letter(s) of intent from the proposed staff is not provided</li>
  <li>Key staff aren’t proposed to be full-time on the project, or the project is to be staffed with mostly partial full-time personnel</li>

{% endcapture %}

{% capture technicalExperiencePositiveSigns %}

  <li>Experience with modern software languages, such as Python, Ruby, PHP, C# (C Sharp), or JavaScript</li>
  <li>Experience with web-based application programming interfaces (APIs), especially REST and GraphQL</li>
  <li>Experience using Git for software version control The lead developer’s skill set and experience will enable them to conduct the work required by the project</li>

{% endcapture %}

{% capture technicalExperienceRedFlags %}

  <li>The proposed lead developer lacks sufficient qualifications</li>
  <li>Proposes outdated software technologies that don’t have an active developer community, e.g., ColdFusion, ASP, or FoxPro</li>
  <li>Lack of experience with test automation, aka DevOps or test-driven development (TDD)</li>
  <li>Proposed staff qualifications are copied in large part or completely from the internet</li>
  <li>Key skills don’t appear in any qualifications, such as:
    
      <li>Agile development experience</li>
      <li>Automated (unit/integration/end-to-end) testing</li>
      <li>Continuous Integration and Continuous Deployment</li>
      <li>DevOps</li>
      <li>Application Protocol Interface (API) development and documentation</li>
      <li>Open source software development</li>
      <li>Cloud deployment</li>
      <li>Building and testing public-facing sites and tools</li>
    
  </li>

{% endcapture %}

{% capture researchDesignPositiveSigns %}

  <li>The lead user researcher’s background demonstrates:
    
      <li>Understanding of how research can inform and shape strategy, design, and development</li>
      <li>Familiarity with a variety of user research and usability testing methods</li>
      <li>Experience deciding the method or methods to use that suit a given research question</li>
      <li>Experience recruiting research participants appropriate to a project</li>
    
  </li>
  <li>The lead UX designer’s background demonstrates:
    
      <li>Strong craft skills and experience generating concepts that reflect overall project strategy, user research, and user-centered design best practices</li>
      <li>Experience and ability communicating those concepts visually via a variety of methods, including sketching, wireframing, prototypes, and more polished mock-ups</li>
    
  </li>

{% endcapture %}

{% capture researchDesignRedFlags %}

  <li>The company, proposed subcontractor, or proposed staff are responsible for poorly designed websites</li>
  <li>Key skills don’t appear in any qualifications, such as:
    
      <li>Product management and strategy</li>
      <li>User research, such as contextual inquiry, stakeholder interviews, and usability testing</li>
      <li>User experience design</li>
      <li>Sketching, wireframing, and/or prototyping, and user task flow development</li>
      <li>Visual design</li>
      <li>Content design, UX writing, and copywriting</li>
    
  </li>

{% endcapture %}

<table>
  <thead>
    <th class="evaluate-bids-what-header">What to look for</th>
    <th class="evaluate-bids-pro-con-header">Positive signs</th>
    <th class="evaluate-bids-pro-con-header">Red flags</th>
  </thead>
  <tr>
    <td>Team size and roles</td>
    <td>[Fewer than 10 team members]({{ ), each of which has a clear role and purpose</td>
    <td>{{ teamSizeAndRolesRedFlags }}</td>
  </tr>
  <tr>
    <td>Team capacity</td>
    <td>The team will be assigned to the project full-time and won’t split members’ time with other projects (Developers, user researchers, designers, and all other key personnel should be fully staffed. A Scrum master or agile coach can be exceptions.)</td>
    <td>{{ teamCapacityRedFlags }}</td>
  </tr>
  <tr>
    <td>Technical team members’ specialized experience and knowledge</td>
    <td>{{ technicalExperiencePositiveSigns }}</td>
    <td>{{ technicalExperienceRedFlags }}</td>
  </tr>
  <tr>
    <td>Research, design, and product team members’ specialized experience and knowledge</td>
    <td>{{ researchDesignPositiveSigns }}</td>
    <td>{{ researchDesignRedFlags }}</td>
  </tr>
</table>

## Similar experience

As part of the solicitation, you will have asked vendors to submit [code repositories]({{ "/derisking-government-tech/buying-development-services/#similar-experience" | url }}) for projects that are similar in size, scope, and complexity to what the agency needs. If you do not have someone on your evaluation team that is familiar with code repositories, you should find a technical advisor.

{% capture technicalEvaluationsPositiveSigns %}

  <li>Proper use of Git, commit changes with personal accounts (not organizational)</li>
  <li>Use of a branching or merging strategy</li>
  <li>Informative comments</li>
  <li>Evidence of peer code reviews and collaboration (work was performed in a reasonable number of GitHub comments)</li>
  <li>Use of a CI/CD pipeline</li>
  <li>Code that conforms well to the quality expectations in the solicitation’s QASP or set of quality indicators</li>
  <li>Substantial projects: the projects weren’t created just to have something to point to for this solicitation</li>
  <li>Iterative incorporation of user feedback into their development process</li>
  <li>Demonstrates the value of testing:
    
      <li>Testing is built into the development process</li>
      <li>Code tests are written well, test coverage is measured and covers most of the code</li>
    
  </li>
  <li>Use of consistent code style</li>
  <li>Code displays modularity and opportunities for reusability</li>
  <li>Sensible data model approach</li>
  <li>Code includes evidence of accessibility considerations (e.g., appropriate alt text, ARIA attributes)</li>
  <li>Evidence of accessibility testing: at minimum, an automated scan; more importantly, manual testing</li>
  <li>The project is set up to be easily deployable by any newly onboarded developer</li>

{% endcapture %}

{% capture technicalEvaluationsRedFlags %}

  <li>No source code is submitted</li>
  <li>There is no Git history or only a single commit, which indicates that this is not the actual code repository and that the code was developed somewhere else (maybe not even with source control)</li>
  <li>None of the provided code samples or described projects are similar in size, scope, and complexity to the project scenario in the RFQ</li>
  <li>The code samples provided do not demonstrate an understanding of writing a modern, maintainable application</li>
  <li>Code is undocumented; there are no code comments</li>
  <li>No automated tests</li>
  <li>The code has obvious vulnerabilities for attacks (e.g., missing SSL certificates, SQL injection attacks, credentials checked into the code, use of unvalidated JWTs)</li>
  <li>Tests are disabled, which suggests that developers may have turned testing off instead of fixing errors; there seems to be a practice of deleting tests or code until the code passes</li>
  <li>Code appears sloppy; there are large sections commented out, unused imports and definitions, or dead code (code that is in the project but is never used)</li>
  <li>No instructions for setting up the project or documentation is boilerplate (e.g., a README)</li>
  <li>Code contains secrets such as passwords, personally identifiable information, or access tokens</li>
  <li>The cited projects lead you to suspect the vendor didn’t create them</li>
  <li>There’s a finished product, but no code, or vice versa</li>

{% endcapture %}

{% capture programmaticEvalutionsPositiveSigns %}

  <li>Work that is conceptually similar to the agency’s needs</li>
  <li>Work that is centered on user needs</li>
  <li>Work that was completed by a team of a size similar to the size of the team that they’re proposing</li>
  <li>Design artifacts that show continuous and ongoing usability testing and that indicate a user-centered approach to iterative design and development</li>
  <li>Illustrates getting stakeholder buy-in on research findings</li>
  <li>Demonstrates that they are comfortable with complexity and challenges</li>
  <li>Communicates openly and emphasizes transparency</li>
  <li>Identifies what is important to each set of stakeholders and tailors their approach accordingly</li>
  <li>Describes frameworks and tools that support iterative development, constant improvement, user-centered design, risk management, and product prioritization</li>

{% endcapture %}

{% capture programmaticEvaluationsRedFlags %}

  <li>The cited projects aren’t similar in size, scope, or complexity to that described in the solicitation</li>
  <li>Work that is led by solutionism</li>
  <li>The projects don’t include design artifacts and research plans, or the plans are incomplete</li>

{% endcapture %}

<table>
  <thead>
    <th class="evaluate-bids-what-header">What to look for in</th>
    <th class="evaluate-bids-pro-con-header">Positive signs</th>
    <th class="evaluate-bids-pro-con-header">Red flags</th>
  </thead>
  <tr>
    <td>Technical evaluations</td>
    <td>{{ technicalEvaluationsPositiveSigns }}</td>
    <td>{{ technicalEvaluationsRedFlags }}</td>
  </tr>
  <tr>
    <td>Programmatic evaluations</td>
    <td>{{ programmaticEvalutionsPositiveSigns }}</td>
    <td>{{ programmaticEvaluationsRedFlags }}</td>
  </tr>
</table>

---

## Evaluator worksheet

Our evaluator worksheet can help you evaluate proposals for a custom software project. [Download a printable PDF]({{ ) or [view it in your browser]({{ '/derisking-government-tech/resources/evaluator-worksheet/' | url }}).
