## Leading product direction

{% include 'derisking-government-tech/section_image.html' img_path: "assets/derisking-government-tech/img/vendors-leading-product.svg" %}

A government custom software project is successful only if the software delivers on the intent of the particular policy or service it was designed for. That purpose must remain front and center at all times. 

The product owner is responsible for ensuring that software achieves its purpose and meets [quality expectations](#quality-indicators). This work involves leadership throughout the many aspects of the project, including:

* Setting the overall product vision and goals.  
* Communicating constraints.  
* Translating the goals into work.  
* Navigating and recording major decisions.  
* Releasing and evaluating the software.

### Setting product vision and goals {#setting-product-vision-and-goals}

The **product vision** is the guiding statement of what a software project is trying to achieve. It should be clear and concrete about what will *change* in the world by delivering the software. 

For example, the vision for a new system within a benefits program might be: “Make it easier for state agency workers to detect potential fraud and error, and take timely action to resolve discrepancies, while preserving participants’ access to the full benefits they deserve.” The vision for an internal purchasing platform might be: “Create a world where the federal government can work out in the open with nontraditional vendors to get quality solutions delivered quickly and cost effectively for the public.” The vision can have more detail than these examples, but it should be immediately understandable and compelling for those involved in the product. (Consult the [18F Product Guide]({{ "https://guides.18f.gov/product/define/vision/" | url }}) for detailed guidance on creating a vision.)

The vision should be established in the solicitation phase and stated in the contract. During the solicitation phase, the vision informs the goals the government is trying to achieve with the vendor’s help. 

After the contract is awarded, the vision helps the team maintain focus during the project. It provides motivation and serves as a tool for aligning the team toward around the same goal. 

The vision helps the product owner and the vendor team set objectives and prioritize work by providing overarching guidance for what to pay attention to and how to weigh trade-offs. As context for all work, it is important to spend time establishing a shared understanding of the vision at the beginning of the project, communicating it frequently, and realigning around it when necessary. 

The team should check their progress against the vision no less than quarterly and update its approach for delivering on it if needed.  

There are a number of frameworks you can use to help you translate the vision into goals. Nonprofit organizations frequently use the framework of Impact, Outcomes, Outputs. Software development teams more often use Objectives and Key Results; the North Star framework; or Goals, Signals, and Measures. 

Choose a structure that fits how your organization discusses work and goals. Then tie the problems you’re trying to solve to the outcomes the team is pursuing, for example, a 20 percent increase in digital application submissions, a 30 percent decrease in call center requests related to application submission errors, time to deploy a new feature shifts from once a quarter to every two weeks, etc. The most important thing is to have *some* way to explain how the day-to-day product development tasks form building blocks toward the vision. 

### Communicating constraints

It’s also important to identify, communicate, and manage constraints that the development team may encounter while building the product. For example, the programming languages the agency can support or who can have access to production servers. 

Even if a constraint is outside the control of the development team, the government must be transparent about any obstacles the team may face. Sharing this information allows the development team to plan for them. It also allows the development team and the government to brainstorm possible solutions and mitigations together. 

### Translating the goals into work

It is not always easy to translate goals into work that delivers on those goals. In most cases, there are many different ways to approach a problem, and it is rarely clear how well an idea will work before it’s realized and users can try it out. The product owner helps the team to navigate this uncertainty. The product owner works with the team to prioritize work that is most likely to deliver the most progress towards the goal soonest, based on user research.  

#### The role of user research

Once a vendor team starts, it can be tempting to dive straight into software development in order to show progress. A better place to start is for the team to become familiar with and invested in the needs of the system’s intended users. 

Even if user research has been done before the vendor joins the project, conducting a round of user research when the vendor starts is helpful. There are always questions about user behavior to address. It also establishes frequent research as a norm that always informs the next set of product decisions.

It may feel risky or inefficient to involve the whole team in user research, especially a new vendor team. But it helps the whole team gain critical context, understanding, and empathy. People will learn the most from direct user contact, which provides nuances and details that may not be apparent from a summary. Participating in user research helps teams make better decisions about the software and results in less rework later.

#### Understanding the software development cycle  

{% image_with_class "assets/derisking-government-tech/img/software-dev-cycle.svg" "margin-bottom-3 margin-top-4 width-full" "" %}

| Stage | Description |
| :---- | :---- |
| **Prioritize** | Government decides what work the vendor should focus on in a sprint based on discussion with the vendor team, user research, and stakeholder input. |
| **Plan** | Vendor and the government lead divvy up and assign tasks and agree on a “definition of done.” |
| **Build** | Vendor team does the work and uses automated and other quality assurance tools to ensure code quality at deployment. |
| **Ship** | Vendor team delivers the work to the government, including all code and other artifacts. Government leads review for adherence to the quality indicators and definition of done. |
| **Reflect**  | Government and vendor team review the work done in the previous sprint, discuss what worked, and what will need to be tweaked in the next sprint. |

One of the most important roles the government product owner plays in a software development project is working with the team in each sprint to decide what to do next and to evaluate what’s been done. This involves frequent interaction via well-structured meetings which is one reason many teams begin work with an existing set of roles and meetings like Scrum.

To decide what to do next, product owners work with a team to define small pieces of work that it can work on independently. The “small pieces” are commonly written as “[user stories]({{ "/derisking-government-tech/principles/#user-centered-design" | url }}),” which capture what a user is trying to do and why. The process of fleshing out stories is critical to aligning the product owner and team on the work to be done and clarifying its connection to goals.

User stories should be accompanied by a “**definition of done**,” the criteria for when work on a backlog item can be considered “done.” This enables a vendor team to work independently and meet the product owner’s expectations for completeness.

At the start of a sprint, the product owner and vendor team agree on the next stories to work on. Then, at the end of the cycle, the team should demonstrate completed work, even if it won’t be directly experienced by the user. 

Having the vendor team demonstrate the product regularly is an indispensable part of healthy oversight and good product leadership. Along with the quality indicators discussed below, “demos” are the only way the government can be truly confident the vendor’s work is on track. They also help to avoid late surprises. 

Based on the demo, the product owner can then agree if it meets the definition of done or if more work and further clarification of the work is needed. If the work doesn’t meet the criteria to be considered done, it’s not a moment for blame, but for discussion and improving how you communicate and collaborate with the vendor team. After all, it’s impossible to anticipate all aspects of the work ahead of time. Ask questions like: “Was something missing from the definition of done?” “Is the team lacking essential context?” 

### Major decisions {#major-decisions}

Some decisions have risks or impacts beyond what a vendor is pre-authorized to decide. These decisions can include issues like whether to use a new third-party component, what data the system should store, or how a new capability should integrate with existing systems.

The government product owner or technical lead will likely be able to decide some of these on their own. But you will often need to work with the vendor about issues that involve established processes and other government stakeholders. It is the government’s responsibility to consult with stakeholders and ensure that the implications of decisions are surfaced. 

Whether the product owner or another agency representative makes a decision, it’s good practice to document major decisions in an [Architecture Decision Record]({{ "https://18f.gsa.gov/2021/07/06/architecture\_decision\_records\_helpful\_now\_invaluable\_later/" | url }}) (ADR), sometimes just called a “decision record.” This tool helps maintain the system, as well as [communicate the decision and any associated risks to stakeholders](#establishing-internal-agency-communication-and-collaboration).

Major decisions come up frequently at early stages of projects and then in waves, such as during release planning. They should be expected. If a vendor team isn’t flagging major decision areas, the product owner should bring it up in a meeting with the team and the contract administrator. 

### Releasing the software

When the software is ready for use, it’s a good risk management practice to release it to a small group of users first before rolling it out to more people. 

It’s important to discuss a rollout strategy for release early in development. Deciding who will use the product first will inform choices about what to build first and in which order to deliver capabilities, such as a whole payment flow or household registration process. The rollout strategy may also impact decisions about how the system captures and stores data.

As you get closer to release, you may need to flesh out responsibilities for compliance, release, and operations between the vendor team and agency. Oftentimes, the vendor team will need to interact with agency teams at this point, like operations or a help desk. You may need to get involved in these discussions to ensure that all of the teams are getting the information they need for a successful launch.

Before release, many agencies require hands-on or “**user acceptance testing**,” where intended users test the product’s features and functionality. But, hands-on testing doesn’t need to wait until all of the software is complete. It’s better to test a set of capabilities of the software or process before the team moves on. The earlier functionality is tested, the simpler it is to localize and fix issues. 

It is common for the product owner to help coordinate testing and work with the team to ensure that the tests adequately exercise the system. It is also likely you will test the system yourself. These tasks help de-risk the project and help you and other government stakeholders gain confidence in the software before it’s released.

### Evaluating the software

After release, a product owner’s work isn’t over. Systems don’t always perform as they should. They may even cause unexpected problems in the processes they’re part of. A key part of the product owner’s role is to evaluate if the system delivers on the [project goals](#setting-product-vision-and-goals) over time. Information collected after release is also essential for making ongoing development plans.

As with a rollout strategy, it’s important to have an evaluation plan in place before release to ensure the information needed to evaluate the product against the goals is being collected. The vendor may be able to help with evaluating how the software is performing, but the government is ultimately responsible for doing that work.