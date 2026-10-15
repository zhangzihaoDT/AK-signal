# AKsignal Daily Scheduler / Run Control

## Ownership

- **Service Scheduler** owns only Service-domain scheduled tasks such as the 08:50 Service pipeline and key-day jobs.
- **AKsignal Scheduler** is an independent macOS LaunchAgent, `com.aksignal.daily-scheduler`. It owns the AKsignal 08:30 Asia/Shanghai timer and schedule heartbeat.
- **AKsignal Run Control** is the Hub panel's manual control and read-only status view.
- Both scheduled and manual triggers enter the native AKsignal Daily runner, which remains the single owner of the business lock, checkpoint compatibility, validation and publication pointer.

The Service Scheduler does not poll or launch AKsignal. Runtime service status, schedule occurrence status and Daily batch status are separate facts.

## Schedule policy

- The LaunchAgent polls locally every five seconds and processes the 08:30 occurrence once.
- On macOS the waiting scheduler holds a bounded `caffeinate -i -s` assertion during 08:00–08:31 Asia/Shanghai, covering the existing 08:10 system wake. Each native Daily batch independently holds an assertion through update, calculation, validation and publication, releasing it on success or failure. Assertions follow their owner PID so process death also releases them; the display can sleep normally. The scheduler does not prevent sleep all day or change global power settings. This prevents idle sleep (and system sleep on AC where macOS honors the assertion); it does not promise to override explicit sleep, lid closure, shutdown or forced power events. On 2026-10-15 the user-authorized local power plan removed the daily 09:00 scheduled sleep and retained the daily 08:10 wake; normal idle sleep applies after protection is released.
- Scheduled execution uses the online `make run-day` pipeline: update ETF and industry data, calculate market facts (including online stock updates), validate, then publish. It requires the existing complete-publication gate; missing or stale target-date data cannot be published. Offline mode remains available for explicit cache-only runs and replay.
- If the Mac is unavailable at the occurrence, the next poll records that occurrence as `MISSED`; it does not launch a catch-up batch.
- If a scheduled batch fails to start, or its process exits without a valid publication, it becomes `FAILED`. Later polls do not retry it.
- Missed and failed tasks remain available for explicit manual Run/Resume. Future occurrences are independent and are not blocked by an older failure.
- Manual Run selects the newest unresolved eligible `trade_date` by default and uses online acquisition, including for legacy offline failures. The CLI also accepts an explicit `--date YYYYMMDD` or `--offline`. A compatible checkpoint resumes the prior run; a signature mismatch automatically creates a new run from the beginning. The panel exposes one Run/Resume operation.
- Rerunning a complete published date is a no-op. A Scheduler restart only affects the LaunchAgent; it never restarts or cancels an existing Daily batch.

## Status and local records

- `.run_day/scheduler/agent_state.json`: LaunchAgent PID, heartbeat, latest occurrence and Scheduler health.
- `.run_day/scheduler/{trade_date}.json`: occurrence status, trigger source, attempts, PID/log and recovery detail.
- Native checkpoint and publication receipts remain the source of truth for batch progress and formal release.
- The Hub view reports Scheduler heartbeat age and next scheduled occurrence separately from the current actionable batch, publication quality and artifacts. A stale heartbeat is not displayed as an enabled Scheduler.

## Commands

```bash
make daily-scheduler-install    # install and start the independent LaunchAgent
make daily-scheduler-status
make daily-scheduler-start
make daily-scheduler-stop      # pauses only the scheduler process
make daily-scheduler-restart   # restarts only the scheduler process
make daily-scheduler-uninstall

python -m src.daily_schedule --plan
python -m src.daily_schedule --scheduled-check
python -m src.daily_schedule --manual-run
python -m src.daily_schedule --manual-run --date YYYYMMDD
python -m src.daily_schedule --manual-run --restart  # only when signature mismatch blocks resume
```

The Hub Worker registers `AKsignal Scheduler` as an AKsignal-domain service (Enable / Restart / Pause) and `AKsignal Daily` as the single manual Run/Resume operation. The native runner handles incompatible checkpoints by creating a new run_id and never reruns a published target. Worker Run acknowledgement means the manual request was handled; only the native formal receipt means `PUBLISHED`.

## Cutover

The Service-domain external AKsignal hook has been removed. Installing `com.aksignal.daily-scheduler` is a separate, explicit local operation; it is not installed or enabled merely by deploying Hub code or restarting Worker. Verify its heartbeat and the AKsignal panel after installation. No production LaunchAgent is changed by source-code tests.
