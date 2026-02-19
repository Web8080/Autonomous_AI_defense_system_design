# Credentials and External Access

Before using cloud or hardware resources, the following must be provided and approved:

1. **AWS**: Access key, secret, region, and bucket names if using S3 for telemetry, datasets, or models.
2. **Roboflow**: API key and workspace/project if using Roboflow for dataset management.
3. **MQTT / Drone / Hardware**: Broker URL and credentials, drone API base URL and key, or other sensor/asset API credentials.

Do not paste credentials into the repo or into chat. Use `.env` (gitignored) or a secret manager. Local simulation and tests run without these.
