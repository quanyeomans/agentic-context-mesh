## May 2023

- <Expandable alt_header='dbt Cloud IDE'>

    To continue improving your [Cloud IDE](/docs/cloud/studio-ide/develop-in-studio) development experience, the dbt Labs team continues to work on adding new features, fixing bugs, and increasing reliability ✨.

    Stay up-to-date with [IDE-related changes](/tags/ide).

    ## New features 

    - Lint via SQL Fluff is now available in beta (GA over the next 2-3 weeks)
    - Format markdown files with prettier
    - Leverage developer experience shortcuts, including ``Ctrl + ` `` (toggle history drawer), `CMD + Option + /` (toggle block comment), `CMD + Shift + P` (open command palette), `Option + W` (close editor tab)
    - Display parent folder name for files with same name in Changes section
    - Navigate the new IDE features quickly using [the IDE User Interface](/docs/cloud/studio-ide/ide-user-interface) help page
    - Use `top X` in SQL when previewing in the IDE
    - Opt into the new IDE backend layer over the past month (still with dbt-rpc). Ready for beta later in June!

    ## Product refinements 

    - Performance-related upgrades:
        - Reduced cold start time by 60+%
        - Improved render time of modals in the IDE by 98%
        - Improved IDE performance with dbt Core v1.5+ (faster and snappier – highly encourage you to [upgrade your dbt version](/docs/dbt-versions/upgrade-dbt-version-in-cloud)!)
    - Upgraded sqlfmt (which powers the Format button) to 0.18.0
    - Updated Build button to change menu options based on file/model type (snapshot, macro, etc.)
    - Display message to disable adblocker for file contents error
    - Moved Format button to console bar
    - Made many security enhancements in the IDE

    ## Bug fixes

    - File icon sizes no longer get wonky in small screen
    - Toast notifications no longer take over command bar menu
    - Hover info inside the text editor no longer gets cut off
    - Transition between a file and a recently modified scratchpad no longer triggers a console error
    - dbt v1.5+ now can access the IDE
    - Confirm button on the Unsaved Changes modal now closes after clicking it
    - Long node names no longer overflow in the parsed logs section in history drawer
    - Status pill in history drawer no longer scales with longer command
    - Tooltip for tab name with a long file name is no longer cut off
    - Lint button should no longer available in main branch

  </Expandable>

- <Expandable alt_header='Run history improvements'>

    New usability and design improvements to the **Run History** dashboard in dbt Cloud are now available. These updates allow people to discover the information they need more easily by reducing the number of clicks, surfacing more relevant information, keeping people in flow state, and designing the look and feel to be more intuitive to use.   

    Highlights include:

    - Usability improvements for CI runs with hyperlinks to the branch, PR, and commit SHA, along with more discoverable temporary schema names
    - Preview of runs' error messages on hover
    - Hyperlinks to the environment
    - Better iconography on run status
    - Clearer run trigger cause (API, scheduled, pull request, triggered by user)
    - More details on the schedule time on hover
    - Run timeout visibility

    dbt Labs is making a change to the metadata retrieval policy for Run History in dbt Cloud. 

    **Beginning June 1, 2023,** developers on the <Constant name="dbt" /> multi-tenant application will be able to self-serve access to their account’s run history through the <Constant name="dbt" /> user interface (UI) and API for only 365 days, on a rolling basis. Older run history will be available for download by reaching out to Customer Support. We're seeking to minimize the amount of metadata we store while maximizing application performance. 

    Specifically, all `GET` requests to the dbt Cloud [Runs endpoint](/dbt-cloud/api-v2#/operations/List%20Runs) will return information on runs, artifacts, logs, and run steps only for the past 365 days.  Additionally, the run history displayed in the dbt Cloud UI will only show runs for the past 365 days.  

    <Lightbox src="/img/docs/dbt-cloud/rn-run-history.jpg" width="100%" title="The dbt Cloud UI displaying a Run History"/>

    We will retain older run history in cold storage and can make it available to customers who reach out to our Support team. To request older run history info, contact the Support team at [support@getdbt.com](mailto:support@getdbt.com) or use the dbt Cloud application chat by clicking the `?` icon in the dbt Cloud UI. 

  </Expandable>
 
- <Expandable alt_header='Run details and log improvements'>

    New usability and design improvements to the run details and logs in dbt Cloud are now available. The ability to triage errors in logs is a big benefit of using dbt Cloud's job and scheduler functionality. The updates help make the process of finding the root cause much easier.
        
    Highlights include:
    - Surfacing a warn state on a run step
    - Search in logs
    - Easier discoverability of errors and warnings in logs
    - Lazy loading of logs, making the whole run details page load faster and feel more performant
    - Cleaner look and feel with iconography
    - Helpful tool tips

  </Expandable>

- <Expandable alt_header='Product docs updates'>

    Hello from the dbt Docs team: @mirnawong1, @matthewshaver, @nghi-ly, and @runleonarun! First, we’d like to thank the 13 new community contributors to docs.getdbt.com!

    Here's what's new to [docs.getdbt.com](http://docs.getdbt.com/) in May:

    ## 🔎 Discoverability

    - We made sure everyone knows that Cloud-users don’t need a [profiles.yml file](/docs/local/profiles.yml) by adding a callout on several key pages.
    - Fleshed out the [model Jinja variable page](/reference/dbt-jinja-functions/model), which originally lacked conceptual info and didn’t link to the schema page.
    - Added a new [Quickstarts landing page](/guides). This new format sets up for future iterations that will include filtering! But for now, we are excited you can step through quickstarts in a focused way.

    ## Cloud projects

    - We launched [dbt Cloud IDE user interface doc](/docs/cloud/studio-ide/ide-user-interface), which provides a thorough walkthrough of the IDE UI elements and their definitions.
    - Launched a sparkling new [dbt Cloud Scheduler page](/docs/deploy/job-scheduler) ✨! We went from previously having little content around the scheduler to a subsection that breaks down the awesome scheduler features and how it works.
    - Updated the [dbt Cloud user license page](/docs/cloud/manage-access/seats-and-users#licenses) to clarify how to add or remove cloud users.
    - Shipped these Discovery API docs to coincide with the launch of the Discovery API:
      - [About the Discovery API](/docs/dbt-cloud-apis/discovery-api)
      - [Use cases and examples for the Discovery API](/docs/dbt-cloud-apis/discovery-use-cases-and-examples)
      - [Query the Discovery API](/docs/dbt-cloud-apis/discovery-querying)

    ## 🎯 Core projects

    - See what’s coming up [in Core v 1.6](https://github.com/dbt-labs/docs.getdbt.com/issues?q=is%3Aissue+label%3A%22dbt-core+v1.6%22)!
    - We turned the `profiles.yml` [page](/docs/local/profiles.yml) into a landing page, added more context to profiles.yml page, and moved the ‘About CLI’ higher up in the `Set up dbt` section.

    ## New 📚 Guides, ✏️ blog posts, and FAQs

    If you want to contribute to a blog post, we’re focusing on content

    - Published a blog post: [Accelerate your documentation workflow: Generate docs for whole folders at once](/blog/generating-dynamic-docs-dbt)
    - Published a blog post: [Data engineers + dbt v1.5: Evolving the craft for scale](/blog/evolving-data-engineer-craft)
    - Added an [FAQ](/faqs/Warehouse/db-connection-dbt-compile) to clarify the common question users have on *Why does dbt compile needs to connect to the database?*
    - Published a [discourse article](https://discourse.getdbt.com/t/how-to-configure-external-user-email-notifications-in-dbt-cloud/8393) about configuring job notifications for non-dbt Cloud users

  </Expandable>