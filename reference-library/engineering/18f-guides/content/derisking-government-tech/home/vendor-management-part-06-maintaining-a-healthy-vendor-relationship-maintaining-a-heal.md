## Maintaining a healthy vendor relationship {#maintaining-a-healthy-vendor-relationship}

{% include 'derisking-government-tech/section_image.html' img_path: "assets/derisking-government-tech/img/vendors-healthy-relationships.svg" %}

There are common warning signs that your relationship with a vendor is becoming dysfunctional. These include when you and the vendor are:

* Paying more attention to processes and tools instead of individuals and interactions.

* Valuing comprehensive documentation over working software.

* Spending more time negotiating what the contract means instead of collaborating to deliver value.

* Fixating on initial plans instead of accepting change as an inevitable part of the work.

If any of these occur, don’t immediately blame the vendor. These often appear when a vendor team is pressured by the agency to demonstrate they are following the rules of the contract. You can still get the relationship back on track.

### How to manage and resolve conflict

A “healthy” relationship with a vendor on a software development project will still involve conflict. When conflict does arise, it’s important to make it productive instead of destructive. 

You can do that by always staying close to the work and maintaining good communication channels, which will help you detect issues early and address them before they become major problems. 

Many conflicts with vendors are about performance and ultimately become conflicts over contracts. Contracts are legal documents that protect you and the vendor. To some extent they are also an attempt to predict the types of conflicts that could arise and to resolve them preemptively, or at least to provide an outline for resolution. As such, they are, essentially, relationship agreements. Contracts establish a framework for how the parties will work together. Unfortunately, they are terrible tools for managing software projects. Contract language is typically dense and hard to understand, intended to be difficult to modify rather than accommodating to changing needs, and is designed to meet the needs of someone in a legal or procurement role, not a software project team member. In accordance with the FAR’s guidance for disputes and appeals that govern all federal contracts, we recommend [resolving issues without resorting to contractual claims]({{ "https://www.acquisition.gov/far/subpart-33.2#:~:text=33.204%20Policy.&text=Reasonable%20efforts%20should%20be%20made,572(b)%20)." | url }}).

We’ve found that if the government and vendor team are using the methods for communicating and demonstrating continuous progress that are outlined in this guide, contract claims almost never occur.

The product owner has the most responsibility for resolving conflict since they’re also responsible for maintaining the project’s speed. Some vendors also have an “agile coach,” or someone in a similar mediating role, on the team to help to unblock issues or deal with conflict. The goal for government and vendor is to discuss issues as they arise directly and professionally and resolve them before formal dispute resolution is needed. 

Here are some common issues we’ve seen arise in government-vendor relationships and how they’ve been addressed without resorting to contract claims.

#### Problems meeting the quality expectations 

Monitoring [quality indicators](#quality-indicators) on a continuous basis is the most effective way to get ahead of issues. If the vendor misses one or more indicators, the way to resolve the issue is by discussing it with the vendor and documenting the conversations.

For example, if a vendor isn’t meeting code coverage expectations in early sprints, the technical reviewer should ask the vendor team in the next sprint review meeting what’s causing it to miss the acceptable quality level and discuss remedies together. Documenting the reasons for the problem and the actions the vendor will take in future sprints to correct it should be captured in one place, such as the code repository, for later reference.

#### Staffing misalignment

Avoiding staff misalignment on the vendor team begins during proposal evaluation, when the government should assess the reasonableness and rationale of vendors’ proposed [staffing approach]({{ "/derisking-government-tech/buying-development-services/#staffing-approach" | url }}), including the team’s size and composition of roles. 

Still, if the vendor team is using an iterative approach, the make-up of the team may need to change. For example, after a few sprints, the vendor team might realize there’s a gap in the skills needed to maintain or increase its speed of work. Or user research might reveal a new priority for the project that requires new skills on the team. 

Allowing for an adjustment in staffing is one reason we recommend using a [time-and-materials (T\&M) type contract with a maximum ceiling price]({{ "/derisking-government-tech/buying-development-services/#time-and-materials-type-contract" | url }}) for a custom software project. The T\&M contract type enables the team composition and/or hours to be adjusted as long as the government and vendor agree it’s necessary, and there isn’t a major impact on the estimated ceiling price for the period of performance. 

Staying close to the work enables the government to be able to interpret the reasonableness of proposed staffing changes. It also helps spot potential issues with staffing that should be addressed with the vendor, such as frequent turnover, which might be a sign of friction within the project team. 

#### Turnover of key personnel

In a federal contract, the intention of a [key personnel clause]({{ "/derisking-government-tech/buying-development-services/#adding-a-key-personnel-clause" | url }}) is to ensure a vendor staffs a team that has the necessary expertise and experience for a project. 

If the vendor proposes a change to key personnel after the contract is awarded, the government should discuss the matter with an open mind. A change in leadership on the project might disrupt the flow of the vendor team’s work, so it’s important that the government and vendor discuss the impact of the change and work together to mitigate it. 

Before agreeing to the change, the government should review the résumé(s) for the proposed replacement(s) or meet with them. *However*, the government can’t participate in the vendor’s hiring processes for a replacement, such as reviewing applications or sitting in on interviews. Most agencies are restricted from “acting as an employer” to anyone on the vendor team because they lack “personal service” contract authority (refer to [FAR 37.104]({{ "https://www.acquisition.gov/far/37.104" | url }})). 

#### Doing work outside priority order 

Sometimes a team might work on issues in the backlog that are not in the order of priority set by the product owner. Whenever this occurs, the product owner should find out why. 

There are many possible reasons. The team may not have understood the priorities. It may have disagreed with them. Or, it may have had a logical reason. For instance, the team may have discovered an issue that wasn’t captured in the backlog but needed to be addressed before a prioritized issue. 

If you find that the team didn’t understand the priorities, discuss how your processes and communication can be improved. Clarifying and aligning on how priorities are communicated may be enough to address the issue. 

If the vendor doesn’t understand the priorities, you may need to share more context about the project or program-specific topics. 

If the team doesn’t agree with the priorities, it may be because they have important information you’re not aware of that affects the functionality or integrity of the software. 

If the vendor team understands the priorities but isn’t attending to them, it may be a sign of a staffing issue that the vendor needs to address. For example, if the team repeatedly de-prioritizes an important task, it might indicate weak skills in a particular area or need for a separate workstream. As the product owner, your role is to highlight the impact of the issue on the work and create space and motivation for the vendor to resolve it. 

If the team is choosing to take on more tasks in a sprint than those selected as priorities, it’s not a cause for concern *as long as* the top-priority tasks are being completed at a satisfactory rate. But, if lower priority work is drawing focus away from higher priority tasks, the product owner should address it with the team.

#### Making decisions outside the team’s authority

Good vendor teams sometimes make decisions beyond their authority. Because every agency operates differently, it can be difficult for a new team to know which decisions they are free to make, which decisions need to be communicated along with implications, and which decisions are truly for the agency to decide. The important thing is to spot when this happens and clarify the boundaries of team decision-making. 

Common areas where this issue comes up are:

* Tech re-platforming (such as introducing a new programming framework or data store)  
* Reopening settled questions, especially wanting to redo user research  
* Decisions that constrain launch strategy or operations

While it is the [vendor team’s responsibility to signal when its choices may have a wider impact](#major-decisions), the product owner should strive to create an environment that encourages that communication by asking good questions and remaining engaged throughout the project. Although a product owner should be mindful of leaving decisions to the team that are in its purview, it’s their responsibility to actively manage the consequences of the vendor team’s choices for people outside the team and to consider their long-term impacts.

Like with other challenges, the first thing a product owner should do if a team is making decisions outside its authority is to talk with the team. Escalation should only be a last resort. The product owner should work to clarify the types of decisions to be made and how they want to be involved in each. Decisions on these matters should be written into the document that captures  the team’s operating principles, such as a team charter.

---

**Next:** [Conclusion]({{ "/derisking-government-tech/conclusion/" | url }})