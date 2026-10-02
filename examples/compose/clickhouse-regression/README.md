# clickhouse-regression

Altinity's [ClickHouse regression suite](https://github.com/Altinity/clickhouse-regression)
in a machine. It is a test suite that drives Docker Compose itself:
`regression.py` brings up its own project inside the machine, runs its tests
against it and tears it down. So the disk holds two projects: this one, which
the machine starts at boot, and the suite's, which the suite starts.

Only this project is here. The suite is copied into `suite/`, which
`.gitignore` keeps untracked.

## 📦 1. The Sources

The suite is used at a pinned commit of its `main`, with
[prepare-env.patch](prepare-env.patch) applied. The patch is Altinity
[PR 170](https://github.com/Altinity/clickhouse-regression/pull/170), which
adds `--prepare-env`; when it is merged, the patch goes.

```bash
cd compose/clickhouse-regression
git clone https://github.com/Altinity/clickhouse-regression.git /tmp/clickhouse-regression
git -C /tmp/clickhouse-regression checkout 5f7decbac4a3896931be5cfe1882ab1ad1e72fab
git -C /tmp/clickhouse-regression apply "$PWD/prepare-env.patch"
mkdir -p suite
cp -a /tmp/clickhouse-regression/helpers \
      /tmp/clickhouse-regression/docker-compose \
      /tmp/clickhouse-regression/requirements.txt \
      /tmp/clickhouse-regression/example \
      suite/
```

Four entries, not the repository: `disks build` packs the whole project
directory, and that repository is 2.1GB of tracked files. A suite needs
`helpers/`, the shared `docker-compose/`, `requirements.txt` and its own
directory, which is about 1MB. `helpers` has to stay a sibling of the suite,
because `regression.py` finds it at `..`.

## 🐳 2. The Runner Image

```bash
docker build -t regression-runner:local runner/
```

## 🗂️ 3. The Images the Suite Starts

A machine reaches no registry, so everything the suite will run has to be in
the local Docker under the names it asks for. `--prepare-env` builds and pulls
them and runs no tests:

```bash
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD/suite:$PWD/suite" -w "$PWD/suite" \
  regression-runner:local \
  python3 example/regression.py \
    --clickhouse docker://clickhouse/clickhouse-server:24.8.14.39-alpine \
    --prepare-env
```

The `carried_*` services in `docker-compose.yml` name those images so the build
packs them. They never start.

## 💾 4. The Disk

```bash
machine disks build --compose . chreg --dry-run
machine disks build --compose . chreg --size 6144
machine sessions create --cpus 6 --mem 24G
machine create --disk chreg --mem 8192 --cpus 2 --daemon
until machine --timeout 0 wait <run> --for halted; do
    machine run <run> --iters 200000000 --mode free
done
machine console <run> -f
```

`--dry-run` reports what the disk holds and builds nothing:

```
content
  rootfs           285MB
  images (tar)     422MB
  images (loaded)  896MB
  project          1MB
size
  used             1604MB
  capacity         3072MB
```

Leaving `--size` out takes `used` plus a gigabyte, rounded up to a whole GB; a
smaller `--size` is refused before anything uploads. The 6144 here is for what
the suite itself writes, which no footprint predicts: it copies the 600MB
clickhouse binary out of its own container.

The 8192MB machine needs a session with at least 24GB. `run` returns several
times while the machine boots, so the loop keeps it running until the machine
halts. The machine runs the project with `--abort-on-container-exit`, so it
powers off when the suite exits and the run lands in `halted`.

## ➕ Another Suite

Copy that suite's directory into `suite/` as well and change `command:`. A
suite whose environment names the same two images needs nothing else; one that
starts ZooKeeper, Keeper or MinIO needs another `carried_*` entry and more
memory. Run it on your own computer first: a suite that fails there fails in a
machine for the same reason.
