# Operations and recovery

Successful status is the final atomic commit marker. Finalization validates the scientific inventory, schemas, lineage and checksums using the exact canonical terminal metadata, writes the run record, then commits `status.json`. An interruption before that last rename leaves a non-success status; independent `verify` rejects the incomplete package. Scientific output files alone do not establish success.

Slurm accounting is captured for each executed task and retained beside its cache origin. Cached tasks reuse those original records with `accounting_scope: origin_run`; they never query an old native job ID that the scheduler may have reused. If a driver was hard-killed before accounting capture, the resumed task records `origin_accounting_not_recorded`. Missing or delayed usage remains explicit, while task state and requested resources still come from the engine trace. The current run's raw `sacct.tsv` contains only newly executed jobs.

Run from a site-approved driver host. Inputs/reference/SIF are immutable and visible at the same paths on workers. Keep a stable launch directory per workflow session; an exclusive `active.lock` prevents concurrent drivers using it. Inspect the recorded PID and this run's jobs before removing a stale lock. A lock timeout is never treated as proof a process has exited.

On interruption, the launcher forwards SIGTERM to its Nextflow process group. Nextflow owns cancellation. Inspect the console and Slurm `squeue -u "$USER"`; cross-reference this run's native job IDs before taking manual action. Cancel only identified jobs with `scancel JOB_ID`. Never cancel all of another user's jobs or all jobs on the partition.

For the recovery exercise, start a batch-size-one run, wait for at least two analysis tasks to complete in `provenance/trace.tsv`, then press Ctrl-C. Use the printed session-specific resume command with a new output directory. Verify all twelve samples and inspect CACHED versus COMPLETED records. Successful cache reuse requires intact `.nextflow/cache/SESSION_UUID` and declared outputs in `work/`; published result copies alone cannot restore Nextflow's cache.

If the driver receives SIGKILL or the host fails, no signal handler can guarantee cleanup. Inspect `logs/driver.log`, `.nextflow/history`, the active lock, and scheduler state. Cancel surviving jobs from that session, preserve logs, then remove only the confirmed stale lock. Restart into a new output directory. Do not edit an incomplete run to say success.

The Docker lab must be launched with `--init`. The initially provisioned development container lacked an init reaper, so exited Slurm step daemons could remain as defunct processes even after jobs completed. They consume no compute allocation, but that container is not clean process-lifecycle evidence. Reprovision with the documented command for release cleanup acceptance. A cgroup cleanup message about moving an exiting step daemon to the parent is retained in lab diagnostics; verify `squeue` and live task processes separately rather than suppressing it.

| Symptom | Investigation and action |
|---|---|
| Checksum mismatch | Restore the locked bytes or create a new immutable data/reference release; do not update a hash merely to suppress a failure. |
| Unsupported PNG | Use single-channel 8-bit PNG at most 4096 by 4096; keep the affected sample ID in the error report. |
| Exit 75 | Inspect both attempts; only classified temporary I/O errors retry once. |
| OOM / TIMEOUT | Inspect accounting and stderr; correct resource requests before resume. Resource increases are explicit, not automatic. |
| Pending jobs | Inspect partition/QoS/account and `squeue` reason; queue waiting is distinct from task runtime. |
| Finalization failure | Retain outputs/logs; resolve missing files, schemas, or accounting issues and rerun/resume. No success marker is valid until verification passes. |
| Missing accounting | Record null and the reason. The supported lab must still demonstrate actual accounting before release. |
| Slow Docker Desktop execution | Put the SIF/work/cache/data on a Linux volume, identify competing workloads, and report the host conditions. |

The pinned Nextflow Slurm executor requests an early `USR2` warning before a job's walltime expires. A time-limited workflow task may therefore appear as `FAILED` with a signal-derived exit code instead of Slurm `TIMEOUT`. The enforcement acceptance drill uses a test-only harmless `CONT` warning so Slurm reaches its hard limit; a separate default-profile run retains Nextflow's normal cleanup behavior. Preserve accounting, requested time and wrapper headers when diagnosing either case. See the [pinned executor implementation](https://raw.githubusercontent.com/nextflow-io/nextflow/v25.10.4/modules/nextflow/src/main/groovy/nextflow/executor/SlurmExecutor.groovy).

After verification, `samples/`, `summary/`, and the report are independent copies. Work also retains task outputs for resume, so duplicate storage is intentional. Export before archiving or removing a run. The default engineering policy retains work/cache for 30 days after a successful demo, and longer if release verification or resume still needs it. Cleanup is operator-managed; the program never automatically deletes inputs or another run's work. Retain cited data, SIF, source, locks, goldens, and packages durably in the repository service. Project/institution requirements take precedence; record storage sizes and the chosen retention policy in release evidence.
