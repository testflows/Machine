# Time travel: checkpoints, branches, replay

A run can go back to any point it recorded and go another way from there,
while what it recorded stays. The examples use a run named `app`; add
`-s <session>` to each command.

## Points

A point is written one way in every command that takes one:

| Point | Means |
|---|---|
| `^booted` | the checkpoint named booted |
| `'#42'` | entry 42 of the recording |
| `-10` | ten entries back |
| `10ms` | a time on the machine's clock |
| `console~regex` | where the console printed a match |
| `now`, `root`, `parent` | the tip, the root, the parent's fork point |
| `%crashed` | the branch marked crashed |

## Checkpoints and branches

| Action | Command |
|---|---|
| Save the whole machine at a point | `machine checkpoint app booted` |
| List the checkpoints | `machine checkpoints app` |
| Branch from a point and start it beside the run | `machine fork app --at ^booted --name try-1` |
| Branch it without starting it | `machine fork app --at ^booted --name try-1 --no-start` |
| Branch at a point and move this machine onto it | `machine go app ^booted` |
| Move this machine onto another branch | `machine switch app app/try-1` |
| Put the run back at a point, discarding what came after | `machine rewind app to ^booted` |
| A new root, a full copy with no shared history | `machine detach app --at '#42'` |
| See the tree, and a run's ancestors | `machine branches app`, `machine lineage app/try-1` |
| Mark a branch, to find it again | `machine marks add app/try-1 crashed` |

A branch is named `root/name`: `app/try-1`. A forked branch is another
machine and needs its own session memory; `go` and `switch` use the machine
already running.

## Replay

Replay repeats a recording exactly, to the instruction, and checks each
entry as it goes.

| Action | Command |
|---|---|
| Replay a branch along its parent's recording | `machine replay app/check` |
| Replay to a point | `machine replay app/check --to ^booted` |
| Show where it differs from the recording | `machine diff app/check --entry 5000 --context 5` |
| Compare two branches at entries | `machine diff app/try-1 app/try-2:4980 --entry 5000` |
| Hash two machines stopped at the same point | `machine hash app mem,disk` |
| Start a run at an earlier checkpoint and replay to its tip | `machine start app --at ^booted` |

## Going back in the debugger

`machine run app --back` moves onto a branch at the newest breakpoint hit,
as reverse-continue does. `machine step app 412 --back 3` goes three
executions of tid 412 back. See `debugging.md`.

## Docs

- Checkpoints, branches, the tree, marks:
  https://testflows.com/docs/machine/checkpoints-and-branches.md
- Replay, comparing entries and machines: https://testflows.com/docs/machine/replay.md
- Every command: https://testflows.com/docs/machine/commands.md
