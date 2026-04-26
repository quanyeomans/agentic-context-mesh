---
title: "Models"
source: Stanford HELM
source_url: https://github.com/stanford-crfm/helm
licence: Apache-2.0
domain: agentic-ai
subdomain: stanford-helm
date_added: 2026-04-25
---

# Models

## Text Models

{% for tag in ["TEXT_MODEL_TAG", "CODE_MODEL_TAG"] %}

{% for organization, models in models_by_organization_with_tag(tag).items() %}

### {{ organization }}

{% for model in models %}

#### {{ model.display_name }} &mdash; `{{ model.name }}`

{{ model.description }}

{% endfor %}
{% endfor %}
{% endfor %}

## Vision-Language Models

{% for organization, models in models_by_organization_with_tag("VISION_LANGUAGE_MODEL_TAG").items() %}

### {{ organization }}

{% for model in models %}

#### {{ model.display_name }} &mdash; `{{ model.name }}`

{{ model.description }}

{% endfor %}
{% endfor %}

## Text-to-image Models

{% for organization, models in models_by_organization_with_tag("TEXT_TO_IMAGE_MODEL_TAG").items() %}

### {{ organization }}

{% for model in models %}

#### {{ model.display_name }} &mdash; `{{ model.name }}`

{{ model.description }}

{% endfor %}
{% endfor %}

## Audio-Language Models

{% for organization, models in models_by_organization_with_tag("AUDIO_LANGUAGE_MODEL_TAG").items() %}

### {{ organization }}

{% for model in models %}

#### {{ model.display_name }} &mdash; `{{ model.name }}`

{{ model.description }}

{% endfor %}
{% endfor %}
