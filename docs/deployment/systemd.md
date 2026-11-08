# systemd Service (Linux)

Nivos includes a systemd unit file for running the API server as a managed background service on Linux. This provides automatic startup on boot, crash recovery, and integration with standard Linux service management tools.

## Prerequisites

Before installing the service, ensure that:

1. Nivos is installed in a virtual environment at `/opt/nivos/.venv` (or adjust paths accordingly).
2. A dedicated `nivos` system user exists (recommended for security).
3. An inference engine (such as Ollama) is running and accessible.

Create the user and installation directory:

```bash
sudo useradd --system --create-home --home-dir /opt/nivos nivos
sudo -u nivos python3 -m venv /opt/nivos/.venv
sudo -u nivos git clone https://github.com/nivos/nivos.git /opt/nivos/Nivos
cd /opt/nivos/Nivos && sudo -u nivos uv sync --extra server
```

## Installing the Service

The unit binds `0.0.0.0`, so an **API key is required** — and the unit
declares `EnvironmentFile=/etc/nivos/env` (no `-` prefix), so it will
**fail to start** until that file exists with a key. Create it first:

```bash
sudo mkdir -p /etc/nivos
echo "NIVOS_API_KEY=$(nivos auth generate-key)" | sudo tee /etc/nivos/env
sudo chmod 600 /etc/nivos/env
```

Then copy the unit file, reload the daemon, and enable the service:

```bash
sudo cp deploy/systemd/nivos.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable nivos
sudo systemctl start nivos
```

Clients must send `Authorization: Bearer <key>` on `/v1/*` and `/api/*`
requests. (If you instead bind to `127.0.0.1`, the key is optional and you
can drop the `EnvironmentFile` line.)

Verify it is running:

```bash
sudo systemctl status nivos
```

## Service File Reference

The provided unit file at `deploy/systemd/nivos.service`:

```ini
[Unit]
Description=Nivos API Server
After=network.target

[Service]
Type=simple
User=nivos
WorkingDirectory=/opt/nivos
ExecStart=/opt/nivos/.venv/bin/nivos serve --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
Environment=HOME=/opt/nivos

[Install]
WantedBy=multi-user.target
```

### `[Unit]` Section

| Directive     | Value              | Description                                                                 |
|---------------|--------------------|-----------------------------------------------------------------------------|
| `Description` | `Nivos API Server` | Human-readable name shown in `systemctl status` and logs.              |
| `After`       | `network.target`   | Delays startup until the network stack is available, since the server binds to a network socket and may need to reach a remote engine. |

### `[Service]` Section

| Directive          | Value                                                              | Description                                                                                     |
|--------------------|--------------------------------------------------------------------|-------------------------------------------------------------------------------------------------|
| `Type`             | `simple`                                                           | The process started by `ExecStart` is the main service process. systemd considers the service started immediately. |
| `User`             | `nivos`                                                       | Runs the server as the `nivos` user rather than root, limiting the blast radius of any security issue. |
| `WorkingDirectory` | `/opt/nivos`                                                  | Sets the working directory for the process. This is where Nivos looks for local files and writes data. |
| `ExecStart`        | `/opt/nivos/.venv/bin/nivos serve --host 0.0.0.0 --port 8000` | The command to start the server. Uses the full path to the `nivos` binary inside the virtual environment. |
| `Restart`          | `on-failure`                                                       | Automatically restarts the service if it exits with a non-zero exit code. Does not restart on clean shutdown (`systemctl stop`). |
| `RestartSec`       | `5`                                                                | Waits 5 seconds before attempting a restart, preventing rapid restart loops if the service crashes immediately on startup. |
| `Environment`      | `HOME=/opt/nivos`                                             | Sets the `HOME` environment variable so Nivos finds its configuration at `~/.nivos/config.toml` (resolving to `/opt/nivos/.nivos/config.toml`). |

### `[Install]` Section

| Directive    | Value               | Description                                                                                 |
|--------------|---------------------|---------------------------------------------------------------------------------------------|
| `WantedBy`   | `multi-user.target` | The service starts when the system reaches multi-user mode (standard boot target for servers). `systemctl enable` creates a symlink under this target. |

## Configuration Options

### Changing the Bind Address and Port

Edit the `ExecStart` line to change the host or port:

```ini
ExecStart=/opt/nivos/.venv/bin/nivos serve --host 127.0.0.1 --port 9000
```

!!! tip
    Binding to `127.0.0.1` restricts access to localhost only. Use this when running behind a reverse proxy like Nginx or Caddy.

### Setting the Engine and Model

Pass additional flags to `nivos serve`:

```ini
ExecStart=/opt/nivos/.venv/bin/nivos serve --host 0.0.0.0 --port 8000 --engine ollama --model qwen3:8b
```

### Adding Environment Variables

Add multiple `Environment` directives or use `EnvironmentFile` for complex configurations:

```ini
[Service]
Environment=HOME=/opt/nivos
Environment=NIVOS_ENGINE_DEFAULT=vllm
Environment=NIVOS_OLLAMA_HOST=http://localhost:11434
```

Or load from a file:

```ini
[Service]
EnvironmentFile=/opt/nivos/.env
```

### Changing the User

If you prefer a different service user, update both the `User` directive and the paths:

```ini
[Service]
User=myuser
WorkingDirectory=/home/myuser/nivos
ExecStart=/home/myuser/nivos/.venv/bin/nivos serve --host 0.0.0.0 --port 8000
Environment=HOME=/home/myuser/nivos
```

### Using a Configuration File

Ensure the configuration file exists at the path where `HOME` points:

```bash
sudo -u nivos mkdir -p /opt/nivos/.nivos
sudo -u nivos cp config.toml /opt/nivos/.nivos/config.toml
```

The server reads `~/.nivos/config.toml` on startup, where `~` resolves from the `HOME` environment variable.

## Viewing Logs

Nivos logs are captured by journald. View them with `journalctl`:

```bash
# View all logs for the service
sudo journalctl -u nivos

# Follow logs in real time
sudo journalctl -u nivos -f

# View logs since the last boot
sudo journalctl -u nivos -b

# View logs from the last hour
sudo journalctl -u nivos --since "1 hour ago"

# View only error-level messages
sudo journalctl -u nivos -p err
```

## Managing the Service

### Start, Stop, and Restart

```bash
# Start the service
sudo systemctl start nivos

# Stop the service
sudo systemctl stop nivos

# Restart the service (stop + start)
sudo systemctl restart nivos

# Reload configuration without full restart (sends SIGHUP)
sudo systemctl reload-or-restart nivos
```

### Check Status

```bash
sudo systemctl status nivos
```

Example output:

```
● nivos.service - Nivos API Server
     Loaded: loaded (/etc/systemd/system/nivos.service; enabled; preset: enabled)
     Active: active (running) since Fri 2026-02-21 10:00:00 UTC; 2h ago
   Main PID: 12345 (nivos)
      Tasks: 4 (limit: 4915)
     Memory: 256.0M
        CPU: 1min 23s
     CGroup: /system.slice/nivos.service
             └─12345 /opt/nivos/.venv/bin/python /opt/nivos/.venv/bin/nivos serve --host 0.0.0.0 --port 8000
```

### Enable and Disable on Boot

```bash
# Enable automatic start on boot
sudo systemctl enable nivos

# Disable automatic start on boot
sudo systemctl disable nivos
```

### Apply Changes After Editing the Unit File

After modifying `/etc/systemd/system/nivos.service`, reload the systemd daemon and restart the service:

```bash
sudo systemctl daemon-reload
sudo systemctl restart nivos
```

## Running Alongside Ollama

If Ollama is also managed via systemd, you can add an ordering dependency so the Nivos service waits for Ollama to start:

```ini
[Unit]
Description=Nivos API Server
After=network.target ollama.service
Requires=ollama.service
```

| Directive  | Description                                                              |
|------------|--------------------------------------------------------------------------|
| `After`    | Ensures Nivos starts after Ollama.                                  |
| `Requires` | If Ollama fails to start, Nivos will not start either.              |

!!! note
    Use `Wants` instead of `Requires` if you want Nivos to start even when Ollama is unavailable (for example, if you plan to start Ollama manually later).
