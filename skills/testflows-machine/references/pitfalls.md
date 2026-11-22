# Pitfalls: refusals, false failures, cleanup

## A failure that is not the bug

Check the failure is the bug's own signature, never any failure.

- **Holds and steps delay threads.** A held or stepped thread loses time on
  the machine's clock. A program that fails a check with a time limit, such
  as "a job waited more than 500 ms", fails it because of the hold alone.
  Know what the bug produces, such as a wait that ran out its whole timeout,
  and match that.
- **Match the exact failure line.** A loose pattern also matches other
  lines: `console~FAILURE` stops on a header that says `FAILURE if ...`
  before anything has run.
- **Run the fix under the same scenario.** The same steps, or the same plan
  and seeds. A failure the fix still shows is not the bug.

## Refusals and their way out

| Refusal | Way out |
|---|---|
| `Account storage is full`, `Session storage is full` | Free space held by deleted runs: `machine storage prune`; `machine storage show` shows the use |
| `Not focused: comm=app` | `machine focus <run> add comm=app`, then the plan or the condition |
| `Session memory is full` | Stop a run you are done with: `machine stop <run>` |
| `Session memory is too small` | The session cannot hold that machine: create a larger session, or give the run less `--mem` |
| `Not signed in` | `machine login --email <address>`, as `setup.md` says |
| `No active session` | Name it: `machine -s <session> ...` |
| `Session work is not connected here` | Connect it once on this computer: `machine sessions use work` |
| `This needs <email> signed in, not an API key` | `machine login --email <email>`: billing and account changes need a login |

Most refusals end with hints, each a command that gets you out. With
`-o json` they are `hints` in the error document on standard error.

## Reading output without filling your context

- `machine output <run>` prints the last 25 lines; `-n -20` the last 20.
- `machine output <run> -n : | grep -E 'FAILURE:|SUCCESS:'` searches every line for
  the ones that decide. Without `-n :`, a signature printed earlier is
  missed.
- `machine run` prints where each vCPU stopped; with `-o json` it is a
  document you can read fields of.
- `machine runs <run>` lists one run's tree, not every run of the account.

## Cost and cleanup

- A session costs credits while it runs; an idle stop ends the charge. It
  holds only so many machines: each run's machine keeps its memory until it
  is stopped.
- Stop each run when it has its answer: `machine -s work stop <run>`.
- Delete the session at the end: `machine sessions delete work -y`. Keep a run
  you want to show; a stopped run keeps its recording, and `machine start
  <run>` starts it again in any session.
- Ask before deleting anything you did not create.

## Commands and docs

- [`machine focus add`](https://testflows.com/docs/machine/commands.md#machine-focus-add),
  [`machine stop`](https://testflows.com/docs/machine/commands.md#machine-stop),
  [`machine output`](https://testflows.com/docs/machine/commands.md#machine-output),
  [`machine sessions delete`](https://testflows.com/docs/machine/commands.md#machine-sessions-delete)
- Scripting with the client, JSON and exit codes:
  https://testflows.com/docs/machine/scripting.md
- Sessions and their memory: https://testflows.com/docs/machine/sessions.md
