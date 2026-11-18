# Reproduce the local deployment

The deployment builds the bridge and XiaoZhi server from the source in this
repository. The XiaoZhi source under `deploy/xiaozhi-runtime` was recovered from
the previous `iot-xiaozhi-baseline:local` image; its license is in that directory.
The Python dependency locks record the versions installed in the previous images.
The faster-whisper VAD model is included from the upstream v1.2.1 release.
Building requires internet access to Docker Hub, PyPI and the PyTorch CPU index.

## Prerequisites

- Linux x86-64, Docker Engine and Docker Compose, enough disk space for the
  images and models. The default core Compose file requests an NVIDIA GPU and
  NVIDIA Container Toolkit; use the CPU override below if they are unavailable.
- A LAN address reachable by the ESP32. The optional ThingsBoard Compose file
  creates a separate PostgreSQL database and ThingsBoard 4.3.1.8 instance.
- The speech and language models must be downloaded on the new machine. Device
  records, console accounts, ThingsBoard data and Ollama's volume are not
  included in a Git clone; migrate them separately if the same data is required.

## First start

1. Clone this repository and enter its directory. Make a private `.env.deploy`
   file (it is ignored by Git), with values appropriate to the new host:

   ```dotenv
   HOST_IP=192.168.1.10
   BRIDGE_SECRET=replace-with-a-long-random-secret
   CONSOLE_USERNAME=admin
   CONSOLE_PASSWORD=replace-with-a-strong-password
   THINGSBOARD_DB_PASSWORD=replace-with-a-different-strong-password
   THINGSBOARD_HOST=http://192.168.1.10:8080
   THINGSBOARD_ALLOWED_ORIGINS=http://192.168.1.10:8080
   ```

   Use `openssl rand -hex 32` to generate `BRIDGE_SECRET`. Restrict the file's
   permissions with `chmod 600 .env.deploy`. Both bridge and XiaoZhi receive the
   same secret; there is no need to place it in `deploy/xiaozhi-data/.config.yaml`.
   Configure a ThingsBoard API key in the console if using that integration.

2. If you need a new local ThingsBoard instance, initialize its database **once**
   and then start it. Skip this step when using an existing instance; never run
   initialization against an existing database:

   ```sh
   docker compose --env-file .env.deploy -f docker-compose.thingsboard.yml run --rm -e INSTALL_TB=true thingsboard
   docker compose --env-file .env.deploy -f docker-compose.thingsboard.yml up -d
   ```

   Follow [ThingsBoard's installation guide](https://thingsboard.io/docs/installation/docker/)
   to configure a tenant and change default credentials before exposing it. The
   database is stored in a separate Docker volume. Point `THINGSBOARD_HOST` and
   `THINGSBOARD_ALLOWED_ORIGINS` at the address used to access your instance.

3. Start the AI stack with the fixed project name so it creates the network used
   by the deployment, then install the models:

   ```sh
   docker compose -p iot_assignment up -d --build
   ./init_models.sh
   ```

   For CPU-only Ollama, replace the first command with:

   ```sh
   docker compose -p iot_assignment -f docker-compose.yml -f docker-compose.cpu.yml up -d --build
   ```

   CPU inference will be slower.

4. Start the bridge and XiaoZhi server:

   ```sh
   docker compose --env-file .env.deploy -f docker-compose.deploy.yml up -d --build
   docker compose --env-file .env.deploy -f docker-compose.deploy.yml ps
   ```

   Create the first console account (the command prompts for its password):

   ```sh
   docker compose --env-file .env.deploy -f docker-compose.deploy.yml exec platform-bridge python -m platform-bridge.accounts create admin
   ```

   `CONSOLE_USERNAME` and `CONSOLE_PASSWORD` above configure the separate native
   dashboard login; they do not create this console account. See
   [multi-user setup](multi-user-auth.md) for additional accounts.

   Open `http://<HOST_IP>:8005` for the console. The OTA URL for devices is
   `http://<HOST_IP>:8005/xiaozhi/ota/`. The address must be reachable from the
   device, not `localhost`. Set `PUBLIC_WS_URL` and `PUBLIC_OTA_URL` in
   `.env.deploy` if a proxy or different external port is used.

5. Check service logs and test the device flow:

   ```sh
   docker compose --env-file .env.deploy -f docker-compose.deploy.yml logs --tail=100 platform-bridge xiaozhi-server
   ```

   For the multi-user provisioning smoke test, create two test accounts and set
   the four `PLATFORM_TEST_USER_*` and `PLATFORM_TEST_PASSWORD_*` variables
   described in `tests/test_alpha02_provisioning.py`, then run that script. It
   creates and deletes test records; use a test deployment.
   Verify audio and ThingsBoard control with the actual device and service.

`deploy/platform-data` and `deploy/xiaozhi-data` are persistent bind mounts.
Keep backups of these directories and the Ollama and ThingsBoard volumes before
moving a live installation. Do not commit those backups, secrets or API keys.
