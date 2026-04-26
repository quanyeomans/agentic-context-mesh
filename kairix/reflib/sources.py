"""Source registry for the kairix reference library.

Single source of truth mapping every sub-source to its metadata.
The normalisation pipeline, frontmatter generator, and licence filter
all read from this registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceDef:
    """Definition of a single source within the reference library."""

    name: str
    """Human-readable source name (e.g. 'OpenAI Cookbook')."""

    collection: str
    """Top-level collection directory (e.g. 'agentic-ai')."""

    dir_name: str
    """Subdirectory name under collection (e.g. 'openai-cookbook')."""

    licence: str
    """SPDX licence identifier (e.g. 'MIT', 'CC0-1.0', 'Apache-2.0')."""

    licence_tier: int
    """1=CC0/PD/Unlicense, 2=MIT/Apache/MPL, 3=CC-BY, 4=CC-BY-SA, 5+=NC/ND/proprietary."""

    source_url: str
    """Canonical source URL (typically GitHub)."""

    exclude_patterns: tuple[str, ...] = ()
    """Path substring patterns to exclude beyond global boilerplate."""

    format: str = "markdown"
    """Primary format: 'markdown', 'json', 'yaml', 'text', 'pdf'."""


def _key(collection: str, dir_name: str) -> str:
    return f"{collection}/{dir_name}"


# ---------------------------------------------------------------------------
# Registry — every source in the reference library
# ---------------------------------------------------------------------------

_SOURCES: list[SourceDef] = [
    # ── agentic-ai ─────────────────────────────────────────────────────────
    SourceDef(
        name="OpenAI Cookbook",
        collection="agentic-ai",
        dir_name="openai-cookbook",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/openai/openai-cookbook",
    ),
    SourceDef(
        name="DAIR.AI Prompt Engineering Guide",
        collection="agentic-ai",
        dir_name="dair-ai-prompts",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/dair-ai/Prompt-Engineering-Guide",
    ),
    SourceDef(
        name="Panaversity Learn Agentic AI",
        collection="agentic-ai",
        dir_name="panaversity-agentic",
        licence="Apache-2.0",
        licence_tier=2,
        source_url="https://github.com/panaversity/learn-agentic-ai",
        exclude_patterns=("translations/",),
    ),
    SourceDef(
        name="Microsoft Generative AI for Beginners",
        collection="agentic-ai",
        dir_name="ms-gen-ai-beginners",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/microsoft/generative-ai-for-beginners",
        exclude_patterns=("translations/",),
    ),
    SourceDef(
        name="Microsoft Prompts for Education",
        collection="agentic-ai",
        dir_name="ms-prompts-edu",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/microsoft/prompts-for-edu",
    ),
    SourceDef(
        name="Awesome AI System Prompts",
        collection="agentic-ai",
        dir_name="awesome-ai-system-prompts",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/dontriskit/awesome-ai-system-prompts",
    ),
    SourceDef(
        name="Microsoft AutoGen",
        collection="agentic-ai",
        dir_name="autogen-docs",
        licence="CC-BY-4.0",
        licence_tier=3,
        source_url="https://github.com/microsoft/autogen",
        exclude_patterns=("test/", "samples/", "notebook/",),
    ),
    SourceDef(
        name="EleutherAI LM Evaluation Harness",
        collection="agentic-ai",
        dir_name="eleutherai-lm-eval",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/EleutherAI/lm-evaluation-harness",
    ),
    SourceDef(
        name="Stanford HELM",
        collection="agentic-ai",
        dir_name="stanford-helm",
        licence="Apache-2.0",
        licence_tier=2,
        source_url="https://github.com/stanford-crfm/helm",
    ),

    # ── data-and-analysis ──────────────────────────────────────────────────
    SourceDef(
        name="dbt Core Documentation",
        collection="data-and-analysis",
        dir_name="dbt-docs",
        licence="Apache-2.0",
        licence_tier=2,
        source_url="https://github.com/dbt-labs/docs.getdbt.com",
        exclude_patterns=("blog/",),
    ),
    SourceDef(
        name="PostHog Documentation",
        collection="data-and-analysis",
        dir_name="posthog-docs",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/PostHog/posthog.com",
        exclude_patterns=(
            "contents/customers/",
            "contents/blog/",
            "contents/founders/",
            "contents/newsletter/",
            "contents/spotlight/",
            "contents/media/",
        ),
    ),
    SourceDef(
        name="MLOps Guide",
        collection="data-and-analysis",
        dir_name="mlops-guide",
        licence="Apache-2.0",
        licence_tier=2,
        source_url="https://github.com/MLOps-Guide/MLOps-Guide",
    ),
    SourceDef(
        name="The Turing Way",
        collection="data-and-analysis",
        dir_name="turing-way",
        licence="CC-BY-4.0",
        licence_tier=3,
        source_url="https://github.com/the-turing-way/the-turing-way",
        exclude_patterns=("translation",),
    ),
    SourceDef(
        name="Causal Inference for the Brave and True",
        collection="data-and-analysis",
        dir_name="causal-inference-handbook",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/matheusfacure/python-causality-handbook",
    ),
    SourceDef(
        name="GrowthBook",
        collection="data-and-analysis",
        dir_name="growthbook-docs",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/growthbook/growthbook",
        exclude_patterns=("packages/",),
    ),

    # ── engineering ────────────────────────────────────────────────────────
    SourceDef(
        name="Architecture Decision Records (JPH)",
        collection="engineering",
        dir_name="adr-examples",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/joelparkerhenderson/architecture-decision-record",
    ),
    SourceDef(
        name="Markdown ADR (MADR)",
        collection="engineering",
        dir_name="madr",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/adr/madr",
    ),
    SourceDef(
        name="Open Source SOC Documentation",
        collection="engineering",
        dir_name="soc-docs",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/madirish/ossocdocs",
    ),
    SourceDef(
        name="18F Guides",
        collection="engineering",
        dir_name="18f-guides",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/18F/guides",
    ),
    SourceDef(
        name="Twelve-Factor App",
        collection="engineering",
        dir_name="12factor",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/heroku/12factor",
    ),
    SourceDef(
        name="Microsoft REST API Guidelines",
        collection="engineering",
        dir_name="microsoft-api-guidelines",
        licence="CC-BY-4.0",
        licence_tier=3,
        source_url="https://github.com/microsoft/api-guidelines",
    ),
    SourceDef(
        name="Microsoft Code-With Engineering Playbook",
        collection="engineering",
        dir_name="microsoft-code-with-playbook",
        licence="CC-BY-4.0",
        licence_tier=3,
        source_url="https://github.com/microsoft/code-with-engineering-playbook",
    ),
    SourceDef(
        name="Google Engineering Practices",
        collection="engineering",
        dir_name="google-eng-practices",
        licence="CC-BY-3.0",
        licence_tier=3,
        source_url="https://github.com/google/eng-practices",
    ),
    SourceDef(
        name="GDS Way (UK Gov Digital Service)",
        collection="engineering",
        dir_name="gds-way",
        licence="OGL-3.0",
        licence_tier=3,
        source_url="https://github.com/alphagov/gds-way",
    ),
    SourceDef(
        name="OpenTelemetry Documentation",
        collection="engineering",
        dir_name="opentelemetry-docs",
        licence="CC-BY-4.0",
        licence_tier=3,
        source_url="https://github.com/open-telemetry/opentelemetry.io",
        exclude_patterns=("static/", "layouts/", "i18n/",),
    ),
    SourceDef(
        name="arc42 Architecture Template",
        collection="engineering",
        dir_name="arc42-template",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/arc42/arc42-template",
    ),
    SourceDef(
        name="Dropbox Engineering Career Framework",
        collection="engineering",
        dir_name="dropbox-career-framework",
        licence="Apache-2.0",
        licence_tier=2,
        source_url="https://github.com/dropbox/dbx-career-framework",
    ),
    SourceDef(
        name="Engineering Ladders (jorgef)",
        collection="engineering",
        dir_name="engineering-ladders",
        licence="Apache-2.0",
        licence_tier=2,
        source_url="https://github.com/jorgef/engineeringladders",
    ),

    # ── security ───────────────────────────────────────────────────────────
    SourceDef(
        name="OWASP Cheat Sheet Series",
        collection="security",
        dir_name="owasp-cheat-sheets",
        licence="CC-BY-SA-4.0",
        licence_tier=4,
        source_url="https://github.com/OWASP/CheatSheetSeries",
    ),
    SourceDef(
        name="SLSA Specification",
        collection="security",
        dir_name="slsa-spec",
        licence="Community-Spec-1.0",
        licence_tier=4,
        source_url="https://github.com/slsa-framework/slsa",
    ),
    SourceDef(
        name="CycloneDX SBOM Specification",
        collection="security",
        dir_name="cyclonedx-spec",
        licence="Apache-2.0",
        licence_tier=2,
        source_url="https://github.com/CycloneDX/specification",
    ),
    SourceDef(
        name="Openlane GRC Platform",
        collection="security",
        dir_name="openlane-grc",
        licence="Apache-2.0",
        licence_tier=2,
        source_url="https://github.com/theopenlane/core",
    ),

    # ── operating-models ───────────────────────────────────────────────────
    SourceDef(
        name="CNCF TAG App Delivery (Platform Engineering)",
        collection="operating-models",
        dir_name="cncf-platform-model",
        licence="Apache-2.0",
        licence_tier=2,
        source_url="https://github.com/cncf/tag-app-delivery",
    ),
    SourceDef(
        name="Ways of Working (JPH)",
        collection="operating-models",
        dir_name="jph-ways-of-working",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/joelparkerhenderson/ways-of-working",
    ),

    # ── product-and-design ─────────────────────────────────────────────────
    SourceDef(
        name="Gong Product Practices",
        collection="product-and-design",
        dir_name="gong-practices",
        licence="CC-BY-4.0",
        licence_tier=3,
        source_url="https://github.com/gong-io/product-practices",
    ),
    SourceDef(
        name="USDS Digital Services Playbook",
        collection="product-and-design",
        dir_name="usds-playbook",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/usds/playbook",
    ),
    SourceDef(
        name="Awesome Retrospectives",
        collection="product-and-design",
        dir_name="awesome-retrospectives",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/josephearl/awesome-retrospectives",
    ),

    # ── leadership-and-culture ─────────────────────────────────────────────
    SourceDef(
        name="Awesome Open Company",
        collection="leadership-and-culture",
        dir_name="awesome-open-company",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/opencompany/awesome-open-company",
    ),
    SourceDef(
        name="Awesome Developing (JPH)",
        collection="leadership-and-culture",
        dir_name="jph-awesome-developing",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/joelparkerhenderson/awesome-developing",
    ),
    SourceDef(
        name="Mozilla Open Leadership Framework",
        collection="leadership-and-culture",
        dir_name="mozilla-open-leadership",
        licence="CC-BY-4.0",
        licence_tier=3,
        source_url="https://github.com/mozilla/open-leadership-framework",
    ),
    SourceDef(
        name="Ontario Service Design Playbook",
        collection="leadership-and-culture",
        dir_name="ontario-service-design",
        licence="CC-BY-4.0",
        licence_tier=3,
        source_url="https://github.com/ongov/Service-Design-Playbook",
    ),

    # ── economics-and-strategy ─────────────────────────────────────────────
    SourceDef(
        name="Business Model Canvas (JPH)",
        collection="economics-and-strategy",
        dir_name="jph-business-model-canvas",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/joelparkerhenderson/business-model-canvas",
    ),
    SourceDef(
        name="Startup Business Guide (SixArm/JPH)",
        collection="economics-and-strategy",
        dir_name="jph-startup-guide",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/SixArm/startup-business-guide",
    ),
    SourceDef(
        name="Meta Robyn (Marketing Mix Modelling)",
        collection="economics-and-strategy",
        dir_name="meta-robyn-mmm",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/facebookexperimental/Robyn",
    ),
    SourceDef(
        name="Google Meridian MMM",
        collection="economics-and-strategy",
        dir_name="google-meridian-mmm",
        licence="Apache-2.0",
        licence_tier=2,
        source_url="https://github.com/google/meridian",
    ),
    SourceDef(
        name="PyMC-Marketing (Bayesian MMM)",
        collection="economics-and-strategy",
        dir_name="pymc-marketing",
        licence="Apache-2.0",
        licence_tier=2,
        source_url="https://github.com/pymc-labs/pymc-marketing",
    ),

    # ── personal-effectiveness ─────────────────────────────────────────────
    SourceDef(
        name="Objectives and Key Results (JPH)",
        collection="personal-effectiveness",
        dir_name="jph-okrs",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/joelparkerhenderson/objectives-and-key-results",
    ),
    SourceDef(
        name="Open Spaced Repetition (FSRS)",
        collection="personal-effectiveness",
        dir_name="open-spaced-repetition",
        licence="MIT",
        licence_tier=2,
        source_url="https://github.com/open-spaced-repetition/fsrs4anki",
    ),
    SourceDef(
        name="Mindful Programming",
        collection="personal-effectiveness",
        dir_name="mindful-programming",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/code-in-flow/mindful-programming",
    ),

    # ── health-and-fitness ─────────────────────────────────────────────────
    SourceDef(
        name="Free Exercise Database",
        collection="health-and-fitness",
        dir_name="free-exercise-db",
        licence="Unlicense",
        licence_tier=1,
        source_url="https://github.com/yuhonas/free-exercise-db",
        format="json",
    ),
    SourceDef(
        name="Awesome Quantified Self",
        collection="health-and-fitness",
        dir_name="awesome-quantified-self",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/woop/awesome-quantified-self",
    ),
    SourceDef(
        name="Awesome Healthcare",
        collection="health-and-fitness",
        dir_name="awesome-healthcare",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/kakoni/awesome-healthcare",
    ),
    SourceDef(
        name="Awesome Mental Health",
        collection="health-and-fitness",
        dir_name="awesome-mental-health",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/dreamingechoes/awesome-mental-health",
    ),
    SourceDef(
        name="Circadiaware (Circadian Health)",
        collection="health-and-fitness",
        dir_name="circadiaware",
        licence="CC-BY-SA-4.0",
        licence_tier=4,
        source_url="https://github.com/Circadiaware/VLiDACMel-entrainment-therapy-non24",
    ),

    # ── philosophy ─────────────────────────────────────────────────────────
    SourceDef(
        name="Tao Te Ching (Standard Ebooks)",
        collection="philosophy",
        dir_name="standard-ebooks-tao-te-ching",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/standardebooks/laozi_tao-te-ching_james-legge",
    ),
    SourceDef(
        name="Art of War (Standard Ebooks)",
        collection="philosophy",
        dir_name="standard-ebooks-art-of-war",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/standardebooks/sun-tzu_the-art-of-war_lionel-giles",
    ),
    SourceDef(
        name="SuttaCentral (Buddhist Suttas)",
        collection="philosophy",
        dir_name="suttacentral",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/suttacentral/sc-data",
    ),
    SourceDef(
        name="Bhagavad Gita (Structured Data)",
        collection="philosophy",
        dir_name="bhagavad-gita-data",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/vedicscriptures/bhagavad-gita-data",
        format="json",
    ),
    SourceDef(
        name="Eastern Classical Texts (Gutenberg)",
        collection="philosophy",
        dir_name="classical-eastern",
        licence="Public-Domain",
        licence_tier=1,
        source_url="https://www.gutenberg.org",
        format="text",
    ),
    SourceDef(
        name="Western Classical Texts (Gutenberg)",
        collection="philosophy",
        dir_name="classical-western",
        licence="Public-Domain",
        licence_tier=1,
        source_url="https://www.gutenberg.org",
        format="text",
    ),
    SourceDef(
        name="Indian Philosophy (Gutenberg)",
        collection="philosophy",
        dir_name="indian-philosophy",
        licence="Public-Domain",
        licence_tier=1,
        source_url="https://www.gutenberg.org",
        format="text",
    ),
    SourceDef(
        name="Martial Arts Philosophy (Gutenberg)",
        collection="philosophy",
        dir_name="martial-arts-philosophy",
        licence="Public-Domain",
        licence_tier=1,
        source_url="https://www.gutenberg.org",
        format="text",
    ),

    # ── family-and-education ───────────────────────────────────────────────
    SourceDef(
        name="Awesome Parenting",
        collection="family-and-education",
        dir_name="awesome-parenting",
        licence="CC0-1.0",
        licence_tier=1,
        source_url="https://github.com/daugaard/awesome-parenting",
    ),
    # Note: Montessori Method and Dewey are at collection root, not in subdirs.
    # They are registered as dir_name="" with the collection as the key.

    # ── industry-standards ─────────────────────────────────────────────────
    SourceDef(
        name="BIAN Semantic APIs",
        collection="industry-standards",
        dir_name="bian-apis",
        licence="Apache-2.0",
        licence_tier=2,
        source_url="https://github.com/bian-official/public",
        format="yaml",
    ),
    SourceDef(
        name="MOSIP Documentation",
        collection="industry-standards",
        dir_name="mosip-docs",
        licence="MPL-2.0",
        licence_tier=2,
        source_url="https://github.com/mosip/documentation",
    ),

    # ── foundations ─────────────────────────────────────────────────────────
    SourceDef(
        name="Open Logic Project",
        collection="foundations",
        dir_name="open-logic-project",
        licence="CC-BY-4.0",
        licence_tier=3,
        source_url="https://github.com/OpenLogicProject/OpenLogic",
    ),
    SourceDef(
        name="Neuromatch Computational Neuroscience",
        collection="foundations",
        dir_name="neuromatch-compneuro",
        licence="CC-BY-4.0",
        licence_tier=3,
        source_url="https://github.com/NeuromatchAcademy/course-content",
    ),
]

# Build lookup dict
SOURCES: dict[str, SourceDef] = {
    _key(s.collection, s.dir_name): s for s in _SOURCES
}


def get_source(collection: str, dir_name: str) -> SourceDef | None:
    """Look up a source definition by collection and directory name."""
    return SOURCES.get(_key(collection, dir_name))


def get_allowed_sources(max_tier: int = 3) -> list[SourceDef]:
    """Return all sources at or below the given licence tier."""
    return [s for s in _SOURCES if s.licence_tier <= max_tier]


def get_excluded_sources(max_tier: int = 3) -> list[SourceDef]:
    """Return sources above the given licence tier (excluded from normalisation)."""
    return [s for s in _SOURCES if s.licence_tier > max_tier]


def all_collections() -> list[str]:
    """Return sorted list of unique collection names."""
    return sorted({s.collection for s in _SOURCES})
