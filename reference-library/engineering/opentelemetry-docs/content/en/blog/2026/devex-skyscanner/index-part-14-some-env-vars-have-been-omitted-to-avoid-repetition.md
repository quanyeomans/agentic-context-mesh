# Some env vars have been omitted to avoid repetition
setup_otel_agent() {
   if [[ -n "$AWS_REGION" ]]; then CLOUD_REGION="cloud.region=${AWS_REGION},"; else CLOUD_REGION=""; fi
   if [[ -n "$AWS_ACCOUNT" ]]; then CLOUD_ACCOUNT_ID="cloud.account.id=${AWS_ACCOUNT},"; else CLOUD_ACCOUNT_ID=""; fi
   if [[ -n "$CLUSTER_NAME" ]]; then K8S_CLUSTER_NAME="k8s.cluster.name=${CLUSTER_NAME},"; else K8S_CLUSTER_NAME=""; fi
   if [[ -n "$SERVICE" ]]; then SERVICE_NAME="service.name=${SERVICE}"; else SERVICE_NAME=""; fi
   echo -n "-javaagent:$OTEL_AGENT" \
       "-Dotel.resource.attributes=${CLOUD_REGION}${CLOUD_ACCOUNT_ID}${K8S_CLUSTER_NAME}${SERVICE_NAME}"
}

JAVA_OPTS="-D64 -server -showversion $(setup_otel_agent) ${ADDITIONAL_JAVA_OPTS:-}"

exec java $JAVA_OPTS "$@"
```

Finally, an individual service Dockerfile extends the same base and only adds
the extra instrumentations that service needs:

```Dockerfile my-service
FROM image/registry/skyscanner-java-base:x.y.z

COPY my-service.jar