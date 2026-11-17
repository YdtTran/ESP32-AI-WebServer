# Repository Guidelines

## Project Structure & Module Organization

- `platform-bridge/` contains the FastAPI platform API, device provisioning, and console hosting.
- `xiaozhi-console/` contains the React, TypeScript, and Tailwind frontend; reusable UI lives in `src/components/`.
- `firmware/` contains ESP-IDF C++ firmware, board implementations, and hardware tools.
- `services/`, `gateway/`, and `thingsboard-mcp/` provide AI routing and IoT integrations. `faster-whisper/` and `piper/` contain speech services.
- `tests/` holds platform integration scripts; component-specific tests may live inside their own directories. Documentation and images are in `docs/`.

## Build, Test, and Development Commands

Run commands from the repository root unless stated otherwise:

- `docker compose up -d --build` builds and starts the core services; `docker compose -f docker-compose.deploy.yml up -d --build` starts the platform deployment stack.
- `cd xiaozhi-console && npm install && npm run dev` starts the frontend development server. Use `npm run build` to type-check and build it.
- `cd firmware && idf.py build` builds firmware using an installed ESP-IDF and the selected target and configuration.
- `python3 tests/test_alpha02_provisioning.py` runs the provisioning integration script against the bridge at `localhost:8005`.

## Coding Style & Naming Conventions

Follow the surrounding module's style: Python uses four-space indentation and `snake_case`; React/TypeScript uses two spaces, `PascalCase` components, and `camelCase` functions; firmware follows existing C++ conventions. Name Python test files `test_*.py`. Keep board-specific hardware settings in the appropriate firmware board directory. The console build runs TypeScript checks; no repository-wide formatter is configured.

## Testing Guidelines

Add focused tests for changed behavior and run the relevant component's tests or integration scripts. Several scripts in `tests/` require running services and may create or modify device records; inspect their prerequisites before running them. Firmware changes should be built for the affected board and, where possible, checked on hardware. There is no documented repository-wide coverage threshold.

## Commit & Pull Request Guidelines

Recent commits use Conventional Commit-style prefixes, such as `feat(mcp): ...` and `docs: ...`. Keep subjects concise and describe the change. Pull requests should explain the problem and solution, list verification performed and service or hardware prerequisites, link related issues when available, and include screenshots for visible console changes.

## Security & Configuration

Keep credentials and device tokens out of commits and logs. Review `.env` files, host addresses, and service ports before deployment; devices must be able to reach the configured platform bridge.
