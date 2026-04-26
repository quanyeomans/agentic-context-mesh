# OpenAI client setup
def get_openai_client():
    with DaprClient() as d:
        secret = d.get_secret(store_name="secretstore", key="openai-api-key").secret
        openai.api_key = secret["openai-api-key"]
    return openai