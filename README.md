# RaceRoom dedicated server on Linux (Docker + Wine)

Runs the Windows-only RaceRoom Racing Experience dedicated server in a Docker container, using Wine
and the Microsoft .NET Framework 4.8. No Windows VM or licence required.

Community project, not affiliated with KW Studios / Sector3 Studios. The repository contains no game
files; the server is downloaded from Steam (app 354060, anonymous login).

Tested with server build 112.0.1753.0 on Ubuntu 24.04 with Docker 29, running one instance.

## Requirements

- Linux x86-64 host with Docker and Compose 2.24 or newer
- 7 GB of free disk space
- A dedicated server key from your RaceRoom account
- UDP 60000, TCP 60001 and UDP 60002 reachable from the internet

## Setup

1. Get the files and create the data directory (the container runs as UID 1000):

   ```bash
   git clone https://github.com/GerritK/raceroom-server-docker.git
   cd raceroom-server-docker
   mkdir -p data && sudo chown 1000:1000 data
   ```

2. Create the settings file with a random MAC address for the container:

   ```bash
   cp .env.example .env
   printf 'RR_MAC_ADDRESS=02:%02x:%02x:%02x:%02x:%02x\n' $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)) >> .env
   ```

3. Build the image, download the server and create the Wine prefix:

   ```bash
   docker compose build                      # 10-15 minutes
   docker compose run --rm raceroom update   # downloads the server
   docker compose run --rm raceroom setup    # 5-20 minutes
   ```

4. Set the login for the web admin (one `user:password` per line):

   ```bash
   printf 'admin:choose-a-password\n' > data/server/access
   chmod 600 data/server/access
   ```

5. Start the server and wait for `Starting frontend on http://*:8088/`:

   ```bash
   docker compose up -d
   docker compose logs -f
   ```

6. Open <http://localhost:8088> (remote host: `ssh -L 8088:127.0.0.1:8088 your-server`), log in,
   enter your server key, then create and start an instance.

## Ports

| Port | Purpose |
|---|---|
| 8088/tcp | Web admin, bound to 127.0.0.1 |
| 60000/udp | Game port, first instance |
| 60001/tcp | Game data, first instance |
| 60002/udp | Time port, first instance |

Every further instance takes the next three ports (60003-60005, ...). Publish them in
`docker-compose.yml`. With UFW, add a route rule per port, for example
`ufw route allow proto udp to any port 60000`.

## Settings

Edit `.env`, then run `docker compose up -d`.

| Variable | Values | Default |
|---|---|---|
| `RR_AUTOSTART` | `all`: start every instance when the container starts<br>`last`: start the instances that were running before the restart<br>`none`: start nothing<br>`123456,234567`: start these instance IDs | `all` |
| `RR_AUTOSTART_DELAY` | Seconds to wait before the automatic start. Keep it at 45 or more. | `45` |
| `RR_MAC_ADDRESS` | MAC address of the container, created in setup step 2. Never change it after registering. | none |

The log shows the result about a minute after the start:
`autostart: instance 123456 (My server): running=True, port 60000, time port 60002`.

## Reverse proxy

The web admin only answers requests whose `Host` header contains the port. Make the proxy send
`Host: raceroom.example.com:8088`.

- Traefik: see the commented labels in `docker-compose.yml`.
- nginx: `proxy_set_header Host raceroom.example.com:8088;`

## Update

Game server, after a RaceRoom update (running sessions end):

```bash
docker compose stop raceroom
docker compose run --rm --no-deps raceroom update
docker compose up -d raceroom
```

This project (Dockerfile, scripts, patch):

```bash
git pull
docker compose build
docker compose up -d
```

Instances start again on their own, see `RR_AUTOSTART` in [Settings](#settings).

## Backup

Registration and instances are stored in
`data/prefix/drive_c/users/ubuntu/AppData/Local/Sector3_Studios_AB/` (`settings.bin`, `iv.bin`,
`data.db`). Back up this folder together with `.env`: the settings are only readable with the same
`RR_MAC_ADDRESS`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Nobody can join, the instance is running | Check the ports in the `autostart:` log line or in the web admin. If they are not 60000/60002, stop and start the instance in the web admin. Otherwise check port forwarding and firewall: `sudo iptables -t nat -S \| grep 6000`. |
| "Not registered!" after a restart | `RR_MAC_ADDRESS` in `.env` was changed. Restore it or register again. |
| Web admin hangs behind a proxy | The `Host` header has no port, see [Reverse proxy](#reverse-proxy). |
| `setup` or the first start hangs | The prefix was created with another Wine version. Delete `data/prefix` and run `setup` again. |
| Container restarts | The server front end crashed. Check `docker compose logs raceroom \| grep -E "Unhandled\|c0000005\|unimplemented"`. |
| Game ports look closed inside the container | Wine binds IPv6 dual-stack sockets. Check `/proc/net/udp6` and `/proc/net/tcp6`. |

Messages about `winebth`, `AsyncCausalityTracer`, `libEGL`, `libGL` and `vulkan` are harmless.

Log files:

- Front end: `data/prefix/drive_c/users/ubuntu/AppData/Local/Sector3_Studios_AB/logs/output.log`
- Game server: `data/prefix/drive_c/users/ubuntu/Documents/My Games/SimBin/RaceRoom Dedicated/UserData/Log_Dedicated/`

## Do not change

| Setting | Reason |
|---|---|
| `RR_MAC_ADDRESS` in `.env` | The server encrypts its settings with a key derived from the MAC address. |
| `tty: true` | Without a terminal the server crashes on start. |
| Wine version | Wine packages, the patched DLLs and the prefix have to match, see below. |

## Wine patch

The web admin uses the Windows HTTP Server API, and Wine's implementation (`http.sys`,
`httpapi.dll`) has bugs that crash the server front end or stall requests. The image build
recompiles these two DLLs from the Wine source with `patches/wine-http.patch`. No file of the game
server is modified.

| Bug in Wine 11.0 and 11.22 | Effect without the patch |
|---|---|
| Synchronous calls report to the application's completion port with a stack address | Random crash of the front end (`AccessViolationException` in `PerformIOCompletionCallback`, exit code `80131506`) |
| A receive call only checks the first connection without a request ID | Requests hang forever behind a proxy that keeps idle connections |
| `HttpCancelHttpRequest` is an unimplemented stub | The process aborts when a client drops a request |
| Responses are written with a single non-blocking `send()` | Truncated responses for slow clients (`Got error 10035`) |

`entrypoint.sh` also sets the Windows version for `RRRE_Dedicated.exe` to Windows 10. The prefix
created by `winetricks dotnet48` reports Windows 7, and in that mode .NET does not suppress the
completion-port reports of the first bug.

## Using another Wine version

The Dockerfile pins Wine 11.0 stable. Staging 11.22 is tested as well:

```bash
docker compose build \
  --build-arg WINE_BRANCH=staging \
  --build-arg WINE_VERSION=11.22~noble-1 \
  --build-arg WINE_SOURCE_URL=https://dl.winehq.org/wine/source/11.x/wine-11.22.tar.xz
```

Then delete `data/prefix`, run `setup` again and restore the backup folder. The build fails if the
patch does not apply to the chosen version.

## Web admin API

JSON over HTTP with basic auth:

```bash
A="-u admin:choose-a-password -H Accept:application/json -H Content-Type:application/json"
curl $A http://localhost:8088/dedi/                                  # list instances
curl $A -d '{"ProcessId":123456}' http://localhost:8088/dedi/start
curl $A -d '{"ProcessId":123456}' http://localhost:8088/dedi/stop
curl $A -d '{"Secret":"<server key>"}' http://localhost:8088/dedi/register
```

## License

MIT, see [LICENSE](LICENSE). Exception: `patches/wine-http.patch` modifies Wine source code and is
licensed like Wine, under the LGPL 2.1 or later.

The repository contains no third-party software. Building and setting up the image downloads Wine
(LGPL), SteamCMD, the RaceRoom dedicated server and the Microsoft .NET Framework 4.8 from their
publishers, under their respective licence terms. Do not publish the built image or the `data`
directory.
