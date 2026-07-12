# Automating the briefing and swell alert

`/surfing:briefing` and its `--alert` variant are built to run unattended, so the morning call and the swell ping come to you instead of you asking for them. This doc covers the two pieces you need: **scheduling** (what runs the command on a timer) and **notification** (what carries the output to your phone or desktop).

Nothing here is required to use the commands interactively. It is for the surfer who wants the decision made before breakfast.

## Prerequisites

- The `surfing` plugin is installed and `/surfing:briefing` works interactively.
- You run it **from your surf folder** (the working directory holding `surfer.yaml` and `spots/`), the same directory you run every surfing command from. Scheduling wrappers must `cd` there first, otherwise the sweep finds no home spots.
- Optional but recommended: `WORLDTIDES_KEY` exported in the environment the scheduler uses, so non-US tide extremes are present (the run degrades gracefully without it, per the tool contract, keying the alert on swell, period, and wind).

## Headless invocation

Both scheduling paths below run the command through Claude Code in headless (print) mode:

```bash
cd ~/surf && claude -p "/surfing:briefing"
```

- `-p` (print) runs one turn and exits, writing the result to stdout. Perfect for a cron line or a routine.
- For the alert, use `--alert`. It prints one or more `... may turn on ...` lines when a home spot's works-on thresholds are forecast within 5 days, and a single `No swell alert: ...` line otherwise:

  ```bash
  cd ~/surf && claude -p "/surfing:briefing --alert"
  ```

The alert is designed so that **a ping means something**: it fires only on a real works-on match (size, period, and an offshore/light window, all read from the spot's own profile), so you can wire it straight to a notification without noise. See "Notify only when it fires" below.

## Scheduling

### Option A: Claude Code scheduled routines (recommended)

Claude Code can run a saved prompt on a cron schedule as a cloud routine (ask Claude to "schedule" the command, or use the `schedule` skill). This is the least-moving-parts option: no local cron daemon, no laptop-awake requirement.

- **Daily briefing:** schedule `/surfing:briefing` for, say, 06:00 local, delivered to wherever your routines report (chat, and any notification channel you have wired to it).
- **Swell alert:** schedule `/surfing:briefing --alert` for a convenient hour (e.g. 18:00, so an evening ping gives you time to plan the dawn). Because the silent case emits only the `No swell alert` line, a routine that forwards output only on the `may turn on` phrase stays quiet on flat weeks.

Note: a cloud routine runs in a fresh environment. It needs access to your surf folder and, for non-US tides, `WORLDTIDES_KEY`. If your surf folder lives only on your laptop, prefer the local cron option below.

### Option B: local cron + headless `claude -p`

A plain crontab entry runs the command on your own machine and pipes the output to a notifier. Two example lines (edit the path, the hour, and the notifier):

```cron
# Daily 06:00 briefing -> desktop/phone notification
0 6 * * *  cd $HOME/surf && /usr/local/bin/claude -p "/surfing:briefing" | $HOME/surf/bin/notify-surf.sh briefing

# Swell alert scan at 18:00 -> notify only when a spot may turn on
0 18 * * * cd $HOME/surf && /usr/local/bin/claude -p "/surfing:briefing --alert" | $HOME/surf/bin/notify-surf.sh alert
```

Cron runs with a minimal environment. Put `WORLDTIDES_KEY` (and anything else the run needs) in the wrapper script or a sourced env file, not just your interactive shell profile. Use the absolute path to the `claude` binary (`which claude`).

macOS note: `cron` runs even when logged out but the machine must be awake; for a laptop that sleeps, use a `launchd` agent with `StartCalendarInterval` instead, or lean on Option A.

## Notification paths

The wrapper reads the command's stdout and forwards it. Pick whatever you already use:

| Channel | One-liner |
|---|---|
| macOS banner | `osascript -e "display notification \"$MSG\" with title \"Surf\""` (or `terminal-notifier -title Surf -message "$MSG"`) |
| Linux desktop | `notify-send "Surf" "$MSG"` |
| Email | `printf '%s' "$MSG" \| mail -s "Surf briefing" you@example.com` |
| Slack | `curl -s -X POST -H 'Content-type: application/json' --data "{\"text\":\"$MSG\"}" "$SLACK_WEBHOOK_URL"` |
| Pushover / ntfy (phone) | `curl -s -d "$MSG" "https://ntfy.sh/your-topic"` |

### Notify only when it fires

For the alert, you almost always want a notification **only** when a spot may turn on, and silence otherwise. The alert's contract makes this a one-line filter: the trigger lines contain `may turn on`, the silent line does not.

```bash
#!/usr/bin/env bash
# bin/notify-surf.sh  {briefing|alert}
set -euo pipefail
mode="${1:-briefing}"
msg="$(cat)"

if [ "$mode" = "alert" ]; then
  # Only notify when at least one spot crossed its works-on thresholds.
  if ! grep -q 'may turn on' <<<"$msg"; then
    exit 0   # silent scan, no notification
  fi
fi

# Send $msg through your channel of choice, e.g. macOS:
osascript -e "display notification \"${msg//\"/\'}\" with title \"Surf\""
```

The daily briefing (`briefing` mode) always notifies, since it is the morning call you asked for. The alert (`alert` mode) notifies only on `may turn on`, so a flat week is completely quiet.

## Failure behavior

Automation inherits the tool contract: `fetch_conditions.py` never hard-fails on a network or API error (it exits 0 with an `error`/`note`), so a flaky forecast API degrades a single spot rather than breaking the run. A scheduled briefing on a bad-network morning still produces output for the spots that fetched, and the alert simply cannot fire on a spot it could not read (it never emits a false ping). If the whole run fails, the wrapper's exit status is non-zero; wire that to a separate "briefing failed" notification if you want to know when the scheduler itself is unhealthy.
