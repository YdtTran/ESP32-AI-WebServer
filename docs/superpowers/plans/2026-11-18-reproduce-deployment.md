# Reproduce Deployment Implementation Plan

**Goal:** Rebuild the deployed bridge and XiaoZhi server from repository source on a second machine.

**Architecture:** Recover the running XiaoZhi source and its installed dependency versions, build it and the bridge without local base images, and make host-specific deployment settings configurable. Keep service data and credentials outside the images.

**Tech Stack:** Docker Compose, Python, FastAPI, XiaoZhi server.

## Tasks

- [x] Reconstruct the server build and replace the bridge's local base image; retain source licenses and lock the currently installed Python dependencies.
- [x] Pass a shared secret and host addresses through the deployment environment; provide CPU-only startup and first-run instructions.
- [x] Build the images, test service authentication and server startup in an isolated network with fresh data, and check the Compose configurations.
- [x] Review the resulting files for credentials and unrelated changes, then commit and push the requested changes.
