---
title: Deployment
description: Deploy Nivos in production environments
---

# Deployment

Nivos supports multiple deployment strategies for different environments
and scales.

## Docker

The recommended way to deploy Nivos in production. Multi-stage builds
with CPU and GPU (NVIDIA CUDA, AMD ROCm) variants.

[:octicons-arrow-right-24: Docker deployment](docker.md)

## systemd (Linux)

Run Nivos as a managed system service on Linux servers.

[:octicons-arrow-right-24: systemd setup](systemd.md)

## launchd (macOS)

Register Nivos as a launch agent on macOS.

[:octicons-arrow-right-24: launchd setup](launchd.md)

## API Server

Run Nivos as an OpenAI-compatible HTTP server via `nivos serve`.

[:octicons-arrow-right-24: API server guide](api-server.md)
