## Step 6: Run the Application with Docker Compose
### Step 6.1: Start the Application
From the project root (`fastapi-daca-tutorial`), start the application:
```bash
docker-compose up -d
```

Output:
```
Creating network "fastapi-daca-tutorial_dapr-network" with driver "bridge"
Creating volume "fastapi-daca-tutorial_redis-data" with default driver
Creating fastapi-daca-tutorial_redis_1 ... done
Creating fastapi-daca-tutorial_zipkin_1 ... done
Creating fastapi-daca-tutorial_prometheus_1 ... done
Creating fastapi-daca-tutorial_chat-service-app_1 ... done
Creating fastapi-daca-tutorial_analytics-service-app_1 ... done
Building review-ui-app
Step 1/7 : FROM python:3.9-slim
 ---> abc123def456
Step 2/7 : RUN pip install uv
 ---> Running in 123abc456def
...
Successfully built 789xyz123abc
Successfully tagged fastapi-daca-tutorial_review-ui-app:latest
Creating fastapi-daca-tutorial_review-ui-app_1 ... done
Creating fastapi-daca-tutorial_chat-service-dapr_1 ... done
Creating fastapi-daca-tutorial_analytics-service-dapr_1 ... done
Creating fastapi-daca-tutorial_review-ui-dapr_1 ... done
```

### Step 6.2: Verify the Services Are Running
List the running containers:
```bash
docker-compose ps
```
Output:
```
                Name                              Command               State           Ports         
------------------------------------------------------------------------------------------------------
fastapi-daca-tutorial_analytics-service-app_1    uv run uvicorn main:app -- ...   Up      0.0.0.0:8001->8001/tcp
fastapi-daca-tutorial_analytics-service-dapr_1   ./daprd --app-id analytics ...   Up                            
fastapi-daca-tutorial_chat-service-app_1         uv run uvicorn main:app -- ...   Up      0.0.0.0:8000->8000/tcp
fastapi-daca-tutorial_chat-service-dapr_1        ./daprd --app-id chat-serv ...   Up                            
fastapi-daca-tutorial_prometheus_1               /bin/prometheus --config.f ...   Up      0.0.0.0:9090->9090/tcp
fastapi-daca-tutorial_redis_1                    docker-entrypoint.sh redis ...   Up      0.0.0.0:6379->6379/tcp
fastapi-daca-tutorial_review-ui-app_1            uv run streamlit run app.p ...   Up      0.0.0.0:8501->8501/tcp
fastapi-daca-tutorial_review-ui-dapr_1           ./daprd --app-id review-ui ...   Up                            
fastapi-daca-tutorial_zipkin_1                   start-zipkin                     Up      0.0.0.0:9411->9411/tcp
```

---