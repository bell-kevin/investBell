# Running investBell on the home server

> **Status, 2026-09-29: paused.** The project is on hold, and the server and the backup machine are disconnected and powered off. Nothing below is running now: no dashboard, daily reports, alerts or backups. The instructions are kept for a restart.
>
> **To restart:** power on the backup machine first, then the server. Lingering starts the containers, the alert unit and the backup timer at boot, and the scheduler sends its startup message. The backup machine's dead CMOS battery (see [Backups](#backups)) may have reset its BIOS settings while unplugged, so confirm that it answers before relying on backups, then run one backup by hand. If the server gets code newer than 2026-09-29, the first daily run fits a new model: the model version name changed, which starts a new model series.
>
> **To power off again:** see [Powering off](#powering-off). The server turns itself back on unless its wake sources are switched off first.

The home server is the former ZimaOS computer (Intel i7-6700, 16 GiB RAM), reinstalled with **Ubuntu Server 26.04 LTS** without disk encryption so that it can reboot unattended. It runs this project as two rootless Podman containers, the dashboard and a daily research scheduler, which share one persistent data directory. systemd starts both at boot through Podman's Quadlet units in `deploy/`. The Docker daemon and Docker Compose are not used.

The local network inventory records the host's address and identity separately and is excluded from source distribution.

## One-time host setup

These two commands need administrative access. Everything after them runs as the ordinary account that owns the containers.

```sh
sudo apt update && sudo apt install -y podman
sudo loginctl enable-linger "$USER"
```

Lingering starts that account's systemd user services at boot, without anyone logging in. Check the installed version with `podman --version`. The units were written for Podman 5; Ubuntu 26.04 provides 5.7.

## Build and start

Put the project at `~/investBell`, from a Git clone or the source download, then build the image:

```sh
cd ~/investBell
podman build -t localhost/investbell:local .
```

Podman reads the `Containerfile` and `.containerignore`. The base image comes from Docker Hub's `docker.io/library/python`; Podman fetches it directly, with no Docker software involved.

Create the data directory and the alert settings file, then install the two units and the alert unit:

```sh
mkdir -p ~/investbell-data ~/.config/containers/systemd ~/.config/systemd/user ~/.config/investbell
touch ~/.config/investbell/alerts.env && chmod 600 ~/.config/investbell/alerts.env
cp deploy/investbell-dashboard.container deploy/investbell-scheduler.container ~/.config/containers/systemd/
cp deploy/investbell-alert@.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start investbell-dashboard investbell-scheduler
systemctl --user status investbell-dashboard investbell-scheduler
journalctl --user -u investbell-scheduler -n 50 --no-pager
```

Quadlet generates the services during `daemon-reload`. Their `[Install]` sections start them at boot; `systemctl --user enable` does not apply to generated units. The empty `alerts.env` leaves alerts off; see [Alerts](#alerts) to turn them on. The scheduler needs the file to exist, even empty: Podman refuses to start it without the file.

Open `http://SERVER-ADDRESS:8765` from the home LAN or over Tailscale. The dashboard has no user authentication or TLS. Do not forward the port through the router or expose it to the public Internet.

## How the containers run

- **Rootless.** No process runs as root on the host. The image's UID/GID 10001 is mapped to the account that owns the units, so files in `~/investbell-data` belong to that account.
- **Locked down.** Each container has a read-only filesystem apart from `/data` and temporary directories, drops all Linux capabilities, and cannot gain new privileges. The host's root filesystem and the Podman socket are not mounted.
- **Self-healing.** systemd restarts either container if it exits. The dashboard's health check calls `/api/health` every 30 seconds; after three failures Podman stops the container and systemd restarts it.
- **Logged to the journal.** `journalctl --user -u investbell-dashboard` or `-u investbell-scheduler` shows output; journald handles log rotation.

The scheduler runs a report at startup, then every weekday at 18:00 America/New_York. It retries failed runs and calculates trading-session availability from the data; exchange holidays do not create invented bars. Reports are idempotent and do not submit orders. It uses fitted mode: only DCA/DVA/capture are selected from Yahoo training history, with a later chronological evaluation. Models persist in `~/investbell-data/models/` and are reused until their two-calendar-year retraining date. The scheduler checks that date on each run, and the dashboard shows saved model status. Change the unit's `Exec=` line to use `--model-mode fixed` with manual percentages for the earlier fixed-parameter workflow. Research retraining does not change a frozen paper-account policy, and neither unit starts the separate paper runner. The paper runner has its own unit, `deploy/investbell-paper.container`, installed only after a paper account is set up; see [the paper guide](paper-trading.md#on-the-home-server). This schedule uses daily opening prices even though the download occurs after the close.

## Alerts

The server sends push alerts through [ntfy](https://ntfy.sh), a free and open-source notification service. Alerts go to a topic, and anyone who knows the topic's name can read it and post to it, so use a long random name and keep it private.

1. Save a random topic in the alert settings file and print it:

   ```sh
   printf 'INVESTBELL_NTFY_URL=https://ntfy.sh/investbell-%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')" > ~/.config/investbell/alerts.env
   cat ~/.config/investbell/alerts.env
   ```

2. Install the ntfy app on your phone, or open ntfy.sh in a browser, and subscribe to that topic.
3. Send a test message, then restart the scheduler so that it reads the file:

   ```sh
   cd ~/investBell
   (set -a; . ~/.config/investbell/alerts.env; python3 -m investbell.notify --title "investBell test" --message "Alerts work")
   systemctl --user restart investbell-scheduler
   ```

The scheduler's startup line in the journal shows `"alerts": "on"` once it has the topic. These events send a message:

- **A report succeeds.** Every weekday evening, and after each restart, a low-priority message without sound gives the next-session plan from the research model, the replay's return next to buy and hold and simple DCA, and the model's settings. The plan is hypothetical; no orders are sent. **If no message arrives on a weekday evening, check the server.**
- **A research report fails.** The scheduler sends one alert when reports start failing and another when a report succeeds again. In between it retries every 15 minutes without further alerts.
- **A unit fails.** Each Quadlet unit has `OnFailure=investbell-alert@%n.service`, which runs `python3 -m investbell.notify --unit` on the host with the unit's last output lines. This covers a dashboard crash or failed health check, a scheduler crash, and any unrequested stop of the paper runner, which waits for review after an error.

A requested stop or restart, including at shutdown, is not a failure and sends no alert. systemd starts the alert unit on every failure, including failures it then restarts automatically. So that a crash loop cannot flood your phone, each unit sends at most one alert every ten minutes, and the next alert says how many failures it held back.

The alerts have these limits:

- They come from this machine. If it loses power or its network connection, nothing is sent, and the missing weekday-evening message is the only sign. Nothing outside the house checks on it.
- ntfy.sh is a public server run by the ntfy project. Messages pass through it and are cached there for up to 12 hours; they contain the unit's recent log lines, which hold no credentials. To avoid this, run your own ntfy server and point `INVESTBELL_NTFY_URL` at it, adding `INVESTBELL_NTFY_TOKEN=` if it requires a login.
- An empty `alerts.env` turns alerts off. Write one `KEY=value` per line, without quotes.

## Backups

Every evening at 19:30 America/New_York, after the weekday report, a timer backs up `~/investbell-data` to a second computer on the home network, the backup machine. It uses [restic](https://restic.net), which encrypts, deduplicates and compresses the backup. The backup machine needs only its SSH server; restic reaches it over SFTP.

Each run of `deploy/investbell-backup.service`:

1. copies the data directory into `~/.local/state/investbell-backup/data` with `python3 -m investbell.backup`. That module takes each SQLite database, including a paper account's ledger, through SQLite's online backup, so the containers keep running and the copy is consistent. It leaves out the Yahoo cache, which rebuilds itself;
2. sends that copy to the restic repository as a new snapshot;
3. keeps 30 daily, 52 weekly and 120 monthly snapshots and removes the rest;
4. reads every stored file back from the backup machine and checks it.

If any step fails, including when the backup machine is off or unreachable, `investbell-alert@.service` sends an alert. A successful backup sends nothing.

### One-time setup

Install restic on the server:

```sh
sudo apt install -y restic
```

On the server, create an SSH key that is used only for backups, and a `Host` entry named `investbellbackup` in `~/.ssh/config`. Replace `BACKUP-ADDRESS` and `BACKUP-USER` with the backup machine's address and account:

```sh
ssh-keygen -t ed25519 -N "" -C investbell-backup@investbell -f ~/.ssh/investbell_backup_ed25519
cat >> ~/.ssh/config <<'EOF'
Host investbellbackup
    HostName BACKUP-ADDRESS
    User BACKUP-USER
    IdentityFile ~/.ssh/investbell_backup_ed25519
    IdentitiesOnly yes
    UserKnownHostsFile ~/.ssh/known_hosts_investbellbackup
    StrictHostKeyChecking yes
    ServerAliveInterval 30
EOF
chmod 600 ~/.ssh/config
ssh-keyscan -t ed25519 BACKUP-ADDRESS > ~/.ssh/known_hosts_investbellbackup
ssh-keygen -lf ~/.ssh/known_hosts_investbellbackup
cat ~/.ssh/investbell_backup_ed25519.pub
```

Compare the printed fingerprint with the one shown on the backup machine's own console by `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. On the backup machine, append the public key to `~/.ssh/authorized_keys` with a prefix that limits it to file transfer, so that it cannot open a shell, and create the repository's folder:

```text
restrict,command="/usr/lib/openssh/sftp-server" ssh-ed25519 AAAA… investbell-backup@investbell
```

```sh
mkdir -p ~/restic
```

Back on the server, create the repository's password and the repository:

```sh
head -c 32 /dev/urandom | base64 > ~/.config/investbell/restic-password
chmod 600 ~/.config/investbell/restic-password
RESTIC_REPOSITORY=sftp:investbellbackup:restic/investbell \
  RESTIC_PASSWORD_FILE=~/.config/investbell/restic-password restic init
```

**Keep a copy of this password off the server**, for example in a password manager. Without it, nobody can read the backups, and a copy that exists only on the server is lost with the server's disk.

Install the units, run one backup, then turn on the timer:

```sh
cd ~/investBell
cp deploy/investbell-backup.service deploy/investbell-backup.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start investbell-backup.service
journalctl --user -u investbell-backup -n 40 --no-pager
systemctl --user enable --now investbell-backup.timer
```

A router DHCP reservation for the backup machine keeps its address stable. If the address changes, backups fail with an alert until `HostName` is updated.

### Checking and restoring

```sh
systemctl --user list-timers investbell-backup.timer
export RESTIC_REPOSITORY=sftp:investbellbackup:restic/investbell
export RESTIC_PASSWORD_FILE=~/.config/investbell/restic-password
restic snapshots
```

Snapshots store the staging path, so restore the folder under it. To put a snapshot back in place of the data directory, stop the services first:

```sh
restic restore latest:$HOME/.local/state/investbell-backup/data --target ~/investbell-restore
systemctl --user stop investbell-dashboard investbell-scheduler
mv ~/investbell-data ~/investbell-data.before-restore
mv ~/investbell-restore ~/investbell-data
systemctl --user start investbell-dashboard investbell-scheduler
```

If the server itself is lost, install restic on any computer that can log in to the backup machine, and use `sftp:BACKUP-USER@BACKUP-ADDRESS:restic/investbell` as the repository together with the saved password.

These backups have limits:

- Both computers are in the same house, so a fire, theft or power surge could destroy both. A second repository somewhere else, such as a cloud storage bucket, would cover this.
- The server's key can delete snapshots over SFTP. Someone who took over the server could also erase the backups.
- Alerts cover failed runs, not missing ones. If the timer is disabled or the server is off, nothing reports it; check `restic snapshots` now and then.
- The backup holds the data directory only. The source code is in Git, and `alerts.env` and the restic password are kept separately.

### When the backup machine is off

If the backup machine is off or unreachable at 19:30, that evening's backup fails with an alert whose log lines say `No route to host`. The timer does not retry until the next evening, so once the machine is back, run the missed backup on the server:

```sh
systemctl --user start investbell-backup.service
journalctl --user -u investbell-backup -n 20 --no-pager
```

Two firmware settings keep a backup machine without a monitor or keyboard reachable. On a Dell, press F2 at the logo to open the BIOS setup:

- **Power Management → AC Recovery: Power On** starts the machine by itself when power returns after a cut. The default leaves it off until someone presses the button.
- **Power Management → Wake on LAN: LAN Only**, with **Deep Sleep Control: Disabled**, lets the server start the machine over the network after it was shut down. Deep sleep cuts power to the network adapter.

Wake on LAN also has to be on in Linux. With netplan's networkd renderer it works reliably only when the adapter is matched by its MAC address, so add a file that netplan merges with the installer's configuration. Replace `enp2s0` with the adapter's name and `BACKUP-MAC` with its address, both shown by `ip link`:

```sh
sudo tee /etc/netplan/90-wakeonlan.yaml >/dev/null <<'EOF'
network:
  version: 2
  ethernets:
    enp2s0:
      match:
        macaddress: "BACKUP-MAC"
      set-name: enp2s0
      wakeonlan: true
EOF
sudo chmod 600 /etc/netplan/90-wakeonlan.yaml
sudo netplan generate
```

If `netplan generate` prints nothing, run `sudo reboot`; afterwards `cat /sys/class/net/enp2s0/device/power/wakeup` prints `enabled`. If it prints an error, delete the file before rebooting. To wake the machine, run this on the server with the home network's broadcast address, such as `192.168.1.255`, in place of `BROADCAST-ADDRESS`:

```sh
python3 -c 'import socket; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1); s.sendto(b"\xff" * 6 + bytes.fromhex("BACKUP-MAC".replace(":", "")) * 16, ("BROADCAST-ADDRESS", 9))'
```

**Known issue: the backup machine's CMOS battery is dead.** The backup machine, a Dell OptiPlex 3040 from 2016, lost power on 2026-09-24 and stayed off, so that evening's backup failed. When it started again, its hardware clock read January 2016. That means the coin-cell battery that keeps the clock running without wall power is dead. The clock itself does not matter: Ubuntu takes the time from the network within seconds of starting, and snapshots carry the server's time. But the same battery may also keep the BIOS settings, so after the next power cut AC Recovery and Wake on LAN may fall back to their defaults and leave the machine off. Pulling the power cord for 30 seconds shows whether they survive. The fix is a new CR2032 coin cell on the motherboard; replacing it was put off on 2026-09-25. Until then, if a backup alert says the machine cannot be reached, press its power button or try waking it from the server, then run the missed backup.

## Updates and removal

After changing the source, rebuild the image and restart both services:

```sh
cd ~/investBell
podman build -t localhost/investbell:local .
systemctl --user restart investbell-dashboard investbell-scheduler
podman image prune
```

This does not rebuild the paper runner's `localhost/investbell:paper` image, which stays fixed for the length of a paper experiment.

When a file in `deploy/` changes, copy it again as in [Build and start](#build-and-start). After editing an installed unit file, run `systemctl --user daemon-reload` before restarting. A dependency or base-image update can change the environment; run the tests and inspect a fresh report after updating. The image pins the Python minor version and the top-level Yahoo dependency, but not every transitive package or the base-image digest.

`~/investbell-data` contains market snapshots, fitted models, dependency caches, and reports. [Backups](#backups) copies it to the backup machine every evening.

To remove the application, stop the services, run `systemctl --user disable --now investbell-backup.timer`, delete the two files from `~/.config/containers/systemd/` and `investbell-alert@.service`, `investbell-backup.service` and `investbell-backup.timer` from `~/.config/systemd/user/`, and run `systemctl --user daemon-reload`. This preserves `~/investbell-data`, the backups and the files in `~/.config/investbell/`. Do not delete the data directory as part of an update.

## Powering off

The accounts on both machines need a password for `sudo`, so run the shutdown from a terminal with `ssh -t`, which lets `sudo` ask for it. Running `systemctl poweroff` over SSH without `sudo` is refused. Power off the server first and the backup machine second, so the server does not attempt a backup while the backup machine is off. A clean shutdown sends no alert.

The server turns itself back on after a clean power-off. On 2026-09-29 it started again 18 seconds after shutting down, most likely woken by one of the USB or PCIe wake sources listed in `/proc/acpi/wakeup`. To keep it off, switch those off for that one shutdown, power off, and unplug it once its power light goes out:

```sh
ssh -t USER@SERVER-ADDRESS 'for d in $(awk "\$3 == \"*enabled\" {print \$1}" /proc/acpi/wakeup); do echo $d | sudo tee /proc/acpi/wakeup >/dev/null; done; sudo poweroff'
```

Writing a device name to `/proc/acpi/wakeup` toggles it, and the change lasts only until the next boot. The backup machine stays off after `sudo poweroff`, but its AC Recovery setting may turn it on as soon as it is plugged back in.

## Validation status

Deployed and checked on the server on 2026-09-24, with Podman 5.7.0 on Ubuntu Server 26.04.1 LTS:

- The image builds, and the full test suite passes inside it with a read-only root filesystem, all capabilities dropped and no new privileges.
- Quadlet generates both services; both start, and the data files belong to the unit owner.
- The dashboard's health check reports `healthy`. From another LAN computer, `/api/health`, the page, model status and a default Yahoo sweep all respond.
- The scheduler's startup run fitted a model, wrote a report, sent no orders and scheduled the next weekday 18:00 New York run.
- After `sudo reboot`, with no one logged in, both services started, the dashboard became healthy, and the scheduler reused the saved model and found its report already written.

That first deployment exposed a bug: the scheduler passed the no-loss rule to the report command as `True`, which the report's number parser rejected, so every run failed and retried. Boolean parameters now take `true` or `false`, and a test checks that the report command accepts every scheduler command.

Development continues on a workstation without Podman. After changing the source there, copy it to the server and rebuild as described under Updates. Private data, credentials and the network inventory stay on the workstation:

```sh
rsync -a --delete --exclude .git --exclude .venv --exclude data --exclude artifacts \
  --include .env.example --exclude '.env*' --exclude __pycache__ --exclude docs/network-discovery.md \
  --exclude .shared-chat.txt ./ USER@SERVER-ADDRESS:investBell/
```

The systemd units `deploy/investbell.service`, `deploy/investbell-daily.service`, `deploy/investbell-daily.timer` and `deploy/investbell-paper.service` are an alternative for a Python installation without containers; see [the general deployment guide](deployment.md).
