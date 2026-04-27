## Build a trusted adapter

The Trusted Adapter Program exists to allow adapter maintainers to demonstrate to the dbt community that your adapter is trusted to be used in production.

The very first data platform dbt supported was Redshift followed quickly by Postgres ([dbt-core#174](https://github.com/dbt-labs/dbt-core/pull/174)). In 2017, back when dbt Labs (née Fishtown Analytics) was still a data consultancy, we added support for Snowflake and BigQuery. We also turned dbt's database support into an adapter framework ([dbt-core#259](https://github.com/dbt-labs/dbt-core/pull/259/)), and a plugin system a few years later. For years, dbt Labs specialized in those four data platforms and became experts in them. However, the surface area of all possible databases, their respective nuances, and keeping them up-to-date and bug-free is a Herculean and/or Sisyphean task that couldn't be done by a single person or even a single team! Enter the dbt community which enables dbt Core to work on more than 30 different databases (32 as of Sep '22)!

Free and open-source tools for the data professional are increasingly abundant. This is by-and-large a _good thing_, however it requires due diligence that wasn't required in a paid-license, closed-source software world. Before taking a dependency on an open-source project is is important to determine the answer to the following questions:

1. Does it work?
2. Does it meet my team's specific use case?
3. Does anyone "own" the code, or is anyone liable for ensuring it works?
4. Do bugs get fixed quickly?
5. Does it stay up-to-date with new Core features?
6. Is the usage substantial enough to self-sustain?
7. What risks do I take on by taking a dependency on this library?

These are valid, important questions to answer—especially given that `dbt-core` itself only put out its first stable release (major version v1.0) in December 2021! Indeed, up until now, the majority of new user questions in database-specific channels are some form of:

- "How mature is `dbt-<ADAPTER>`? Any gotchas I should be aware of before I start exploring?"
- "has anyone here used `dbt-<ADAPTER>` for production models?"
- "I've been playing with  `dbt-<ADAPTER>` -- I was able to install and run my initial experiments. I noticed that there are certain features mentioned on the documentation that are marked as 'not ok' or 'not tested'. What are the risks?
I'd love to make a statement on my team to adopt dbt, but I'm pretty sure questions will be asked around the possible limitations of the adapter or if there are other companies out there using dbt with Oracle DB in production, etc."

There has been a tendency to trust the dbt Labs-maintained adapters over community- and vendor-supported adapters, but repo ownership is only one among many indicators of software quality. We aim to help our users feel well-informed as to the caliber of an adapter with a new program.

### What it means to be trusted

By opting into the below, you agree to this, and we take you at your word. dbt Labs reserves the right to remove an adapter from the trusted adapter list at any time, should any of the below guidelines not be met.

### Feature Completeness

To be considered for the Trusted Adapter Program, the adapter must cover the essential functionality of <Constant name="core" /> given below, with best effort given to support the entire feature set.

Essential functionality includes (but is not limited to the following features):

- table, view, and seed materializations
- dbt tests

The adapter should have the required documentation for connecting and configuring the adapter. The dbt docs site should be the single source of truth for this information. These docs should be kept up-to-date.

Proceed to the "Document a new adapter" step for more information.

### Release cadence

Keeping an adapter up-to-date with the latest features of dbt, as defined in [dbt-adapters](https://github.com/dbt-labs/dbt-adapters), is an integral part of being a trusted adapter. We encourage adapter maintainers to keep track of new dbt-adapter releases and support new features relevant to their platform, ensuring users have the best version of dbt. 

Before [dbt Core version 1.8](/docs/dbt-versions/core-upgrade/upgrading-to-v1.8#new-dbt-core-adapter-installation-procedure), adapter versions needed to match the semantic versioning of dbt Core. After v1.8, this is no longer required. This means users can use an adapter on v1.8+ with a different version of dbt Core v1.8+. For example, a user could use dbt-core v1.9 with dbt-postgres v1.8. 

### Community responsiveness

On a best effort basis, active participation and engagement with the dbt Community across the following forums:

- Being responsive to feedback and supporting user enablement in dbt Community’s Slack workspace
- Responding with comments to issues raised in public dbt adapter code repository
- Merging in code contributions from community members as deemed appropriate

### Security Practices

Trusted adapters will not do any of the following:

- Output to logs or file either access credentials information to or data from the underlying data platform itself.
- Make API calls other than those expressly required for using dbt features (adapters may not add additional logging)
- Obfuscate code and/or functionality so as to avoid detection

Additionally, to avoid supply-chain attacks:

- Use an automated service to keep Python dependencies up-to-date (such as  Dependabot or similar),
- Publish directly to PyPI from the dbt adapter code repository by using trusted CI/CD process (such as GitHub actions)
- Restrict admin access to both the respective code (GitHub) and package (PyPI) repositories
- Identify and mitigate security vulnerabilities by use of a static code analyzing tool (such as Snyk) as part of a CI/CD process

### Other considerations

The adapter repository is:

- open-souce licensed,
- published to PyPI, and
- automatically tests the codebase against dbt Lab's provided adapter test suite

### How to get an adapter on the trusted list

Open an issue on the [docs.getdbt.com GitHub repository](https://github.com/dbt-labs/docs.getdbt.com) using the "Add adapter to Trusted list" template. In addition to contact information, it will ask confirm that you agree to the following.

1. my adapter meet the guidelines given above
2. I will make best reasonable effort that this continues to be so
3. checkbox: I acknowledge that dbt Labs reserves the right to remove an adapter from the trusted adapter list at any time, should any of the above guidelines not be met.

The approval workflow is as follows:

1. create and populate the template-created issue
2. dbt Labs will respond as quickly as possible (maximally four weeks, though likely faster)
3. If approved, dbt Labs will create and merge a Pull request to formally add the adapter to the list.

### Getting help for my trusted adapter

Ask your question in #adapter-ecosystem channel of the dbt community Slack.