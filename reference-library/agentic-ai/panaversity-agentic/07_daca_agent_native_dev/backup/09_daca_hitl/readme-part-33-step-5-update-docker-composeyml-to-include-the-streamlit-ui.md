## Step 5: Update `docker-compose.yml` to Include the Streamlit UI
We’ll add the `review-ui` service to the `docker-compose.yml` file and ensure it runs with a Dapr sidecar.

Edit `docker-compose.yml`:
```yaml
version: "3.9"
services:
  redis:
    image: redis:6.2
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    networks:
      - dapr-network

  zipkin:
    image: openzipkin/zipkin
    ports:
      - "9411:9411"
    networks:
      - dapr-network

  prometheus:
    image: prom/prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    networks:
      - dapr-network

  chat-service-app:
    build: ./chat_service
    ports:
      - "8000:8000"
    depends_on:
      - redis
    environment:
      - PYTHONUNBUFFERED=1
    networks:
      - dapr-network

  chat-service-dapr:
    image: daprio/dapr:1.12
    command:
      - "./daprd"
      - "--app-id"
      - "chat-service"
      - "--app-port"
      - "8000"
      - "--dapr-http-port"
      - "3500"
      - "--dapr-grpc-port"
      - "50001"
      - "--metrics-port"
      - "9090"
      - "--log-level"
      - "debug"
      - "--config"
      - "/components/tracing.yaml"
      - "--components-path"
      - "/components"
    environment:
      - DAPR_REDIS_HOST=redis:6379
    volumes:
      - ./components:/components
    depends_on:
      - chat-service-app
      - redis
      - zipkin
      - prometheus
    networks:
      - dapr-network

  analytics-service-app:
    build: ./analytics_service
    ports:
      - "8001:8001"
    depends_on:
      - redis
    environment:
      - PYTHONUNBUFFERED=1
    networks:
      - dapr-network

  analytics-service-dapr:
    image: daprio/dapr:1.12
    command:
      - "./daprd"
      - "--app-id"
      - "analytics-service"
      - "--app-port"
      - "8001"
      - "--dapr-http-port"
      - "3501"
      - "--metrics-port"
      - "9091"
      - "--log-level"
      - "debug"
      - "--config"
      - "/components/tracing.yaml"
      - "--components-path"
      - "/components"
    environment:
      - DAPR_REDIS_HOST=redis:6379
    volumes:
      - ./components:/components
    depends_on:
      - analytics-service-app
      - redis
      - zipkin
      - prometheus
    networks:
      - dapr-network

  review-ui-app:
    build: ./review_ui
    ports:
      - "8501:8501"
    depends_on:
      - redis
    environment:
      - PYTHONUNBUFFERED=1
    networks:
      - dapr-network

  review-ui-dapr:
    image: daprio/dapr:1.12
    command:
      - "./daprd"
      - "--app-id"
      - "review-ui"
      - "--app-port"
      - "8501"
      - "--dapr-http-port"
      - "3502"
      - "--metrics-port"
      - "9092"
      - "--log-level"
      - "debug"
      - "--config"
      - "/components/tracing.yaml"
      - "--components-path"
      - "/components"
    environment:
      - DAPR_REDIS_HOST=redis:6379
    volumes:
      - ./components:/components
    depends_on:
      - review-ui-app
      - redis
      - zipkin
      - prometheus
    networks:
      - dapr-network

networks:
  dapr-network:
    driver: bridge

volumes:
  redis-data:
```

#### Explanation of Changes
- Added `review-ui-app`:
  - `build: ./review_ui`: Builds the image from the `Dockerfile` in the `review_ui` directory.
  - `ports: - "8501:8501"`: Exposes the Streamlit UI on port `8501`.
  - `depends_on: - redis`: Ensures Redis starts first.
  - `environment: - PYTHONUNBUFFERED=1`: Ensures Python output is unbuffered.
  - `networks: - dapr-network`: Connects to the custom network.
- Added `review-ui-dapr`:
  - `image: daprio/dapr:1.12`: Uses the Dapr runtime image.
  - `command`: Configures the Dapr sidecar for the `review-ui` app (e.g., `--app-id review-ui`, `--app-port 8501`).
  - `environment: - DAPR_REDIS_HOST=redis:6379`: Overrides the Redis host for Dapr.
  - `volumes: - ./components:/components`: Mounts the `components` directory.
  - `depends_on`: Ensures the `review-ui-app`, Redis, Zipkin, and Prometheus start first.
  - `networks: - dapr-network`: Connects to the custom network.

### Update `prometheus.yml` for the New Service
Add the `review-ui-dapr` metrics endpoint to `prometheus.yml`:
```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'dapr'
    static_configs:
      - targets: ['chat-service-dapr:9090', 'analytics-service-dapr:9091', 'review-ui-dapr:9092']
```

---