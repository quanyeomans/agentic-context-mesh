## June 2023

- <Expandable alt_header='Lint format'>

    dbt Labs is excited to announce you can now lint and format your dbt code in the dbt Cloud IDE. This is an enhanced development workflow which empowers you to effortlessly prioritize code quality. 

    You can perform linting and formatting on five different file types: SQL, YAML, Markdown, Python, and JSON. 

    For SQL files, you can easily lint and format your code using [SQLFluff](https://sqlfluff.com/) and apply consistent formatting using [sqlfmt](http://sqlfmt.com/). Additionally, for other file types like YAML, Markdown, JSON, and Python, you can utilize the respective tools powered by [Prettier](https://prettier.io/) and [Black](https://black.readthedocs.io/en/latest/) to ensure clean and standardized code formatting.

    For more info, read [Lint and format your code](/docs/cloud/studio-ide/lint-format).

    <DocCarousel slidesPerView={1}>

    <Lightbox src="/img/docs/dbt-cloud/cloud-ide/sqlfluff.gif" width="100%" title="Use SQLFluff to lint/format your SQL code, and view code errors in the Code Quality tab."/>

    <Lightbox src="/img/docs/dbt-cloud/cloud-ide/sqlfmt.gif" width="95%" title="Use sqlfmt to format your SQL code."/>

    <Lightbox src="/img/docs/dbt-cloud/cloud-ide/prettier.gif" width="95%" title="Format YAML, Markdown, and JSON files using Prettier."/>

    </DocCarousel>

  </Expandable>

- <Expandable alt_header='CI updates'>

    dbt Cloud CI is a critical part of the analytics engineering workflow. Large teams rely on process to ensure code quality is high, and they look to dbt Cloud CI to automate testing code changes in an efficient way, enabling speed while keep the bar high. With status checks directly posted to their dbt PRs, developers gain the confidence that their code changes will work as expected in production, and once you’ve grown accustomed to seeing that green status check in your PR, you won’t be able to work any other way.

    <Lightbox src="/img/docs/release-notes/ci-checks.png" width="75%" title="CI checks directly from within Git"/>

    What separates <Constant name="dbt" /> CI from other CI providers is its ability to keep track of state of what’s running in your production environment, so that when you run a CI job, only the modified data assets in your pull request and their downstream dependencies get built and tested in a staging schema. <Constant name="dbt" /> aims to make each CI check as efficient as possible, so as to not waste any data warehouse resources. As soon as the CI run completes, its status posts directly back to the PR in GitHub, GitLab, or Azure DevOps, depending on which <Constant name="git" /> provider you’re using. Teams can set up guardrails to let only PRs with successful CI checks be approved for merging, and the peer review process is greatly streamlined because <Constant name="dbt" /> does the first testing pass. 

    We're excited to introduce a few critical capabilities to <Constant name="dbt" /> CI that will improve productivity and collaboration in your team’s testing and integration workflow. As of this week, you can now:

    - **Run multiple CI checks in parallel**. If more than one contributor makes changes to the same dbt project in dbt Cloud in short succession, the later arriving CI check no longer has to wait for the first check to complete. Both checks will execute concurrently.

    - **Automatically cancel stale CI runs**. If you push multiple commits to the same PR, <Constant name="dbt" /> will cancel older, now-out-of-date CI checks automatically. No resources wasted on checking stale code.

    - **Run CI checks without blocking production runs**. CI checks will no longer consume run slots, meaning you can have as many CI checks running as you want, without impeding your production jobs.

    To learn more, refer to [Continuous integration](/docs/deploy/continuous-integration) and [CI jobs](/docs/deploy/ci-jobs).

  </Expandable>

- <Expandable alt_header='Admin API'>

    dbt Labs updated the docs for the [dbt Cloud Administrative API](/docs/dbt-cloud-apis/admin-cloud-api) and they are now available for both [v2](/dbt-cloud/api-v2#/) and [v3](/dbt-cloud/api-v3#/). 

    - Now using Spotlight for improved UI and UX.
    - All endpoints are now documented for v2 and v3. Added automation to the docs so they remain up to date.  
    - Documented many of the request and response bodies.
    - You can now test endpoints directly from within the API docs. And, you can choose which [regional server](/docs/cloud/about-cloud/access-regions-ip-addresses) to use (North America, APAC, or EMEA).
    - With the new UI, you can more easily generate code for any endpoint.

  </Expandable>

- <Expandable alt_header='Product docs updates'>
    
    Hello from the dbt Docs team: @mirnawong1, @matthewshaver, @nghi-ly, and @runleonarun! First, we’d like to thank the 17 new community contributors to docs.getdbt.com &mdash; ✨ @aaronbini, @sjaureguimodo, @aranke, @eiof, @tlochner95, @mani-dbt, @iamtodor, @monilondo, @vrfn, @raginjason, @AndrewRTsao, @MitchellBarker, @ajaythomas, @smitsrr, @leoguyaux, @GideonShils, @michaelmherrera!

    Here's what's new to [docs.getdbt.com](http://docs.getdbt.com/) in June:

    ## ☁ Cloud projects

    - We clarified the nuances of [CI and CI jobs](/docs/deploy/continuous-integration), updated the [Scheduler content](/docs/deploy/job-scheduler), added two new pages for the job settings and run visibility, moved the project state page to the [Syntax page](/reference/node-selection/syntax), and provided a landing page for [Deploying with Cloud](/docs/deploy/jobs) to help readers navigate the content better.
    - We reformatted the [Supported data platforms page](/docs/supported-data-platforms) by adding dbt Cloud to the page, splitting it into multiple pages, using cards to display verified adapters, and moving the [Warehouse setup pages](/docs/local/connect-data-platform/about-dbt-connections) to the Docs section. 
    - We launched a new [Lint and format page](/docs/cloud/studio-ide/lint-format), which highlights the awesome new dbt Cloud IDE linting/formatting function.
    - We enabled a connection between [dbt Cloud release notes](/docs/dbt-versions/dbt-cloud-release-notes) and the dbt Slack community. This means new dbt Cloud release notes are automatically sent to the slack community [#dbt-cloud channel](https://getdbt.slack.com/archives/CMZ2V0X8V) via RSS feed, keeping users up to date with changes that may affect them. 
    - We’ve added two new docs links in the dbt Cloud Job settings user interface (UI). This will provide additional guidance and help users succeed when setting up a dbt Cloud job: [job commands](/docs/deploy/job-commands) and job triggers.    
    - We added information related to the newly created [IT license](/docs/cloud/manage-access/about-user-access#license-based-access-control), available for Team and Enterprise plans. 
    - We added a new [Supported browser page](/docs/cloud/about-cloud/browsers), which lists the recommended browsers for dbt Cloud.
    - We launched a new page informing users of [new Experimental features option](/docs/dbt-versions/experimental-features) in dbt Cloud.
    - We worked with dbt Engineering to help publish new beta versions of the dbt [dbt Cloud Administrative API docs](/docs/dbt-cloud-apis/admin-cloud-api). 

    ## 🎯 Core projects

    - We launched the new [MetricFlow docs](/docs/build/build-metrics-intro) on dbt Core v1.6 beta.
    - Split [Global configs](/reference/global-configs/about-global-configs) into individual pages, making it easier to find, especially using search. 

    ## New 📚 Guides, ✏️ blog posts, and FAQs

    - Add an Azure DevOps example in the [Customizing CI/CD with custom pipelines](/guides/custom-cicd-pipelines) guide.

  </Expandable>