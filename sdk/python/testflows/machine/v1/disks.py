# Copyright 2026 Katteli Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Disks: the account's catalog of what runs boot from."""

from __future__ import annotations

import builtins
import dataclasses
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator, Literal, Optional, overload

from testflows.machine._core import api
from testflows.machine._core import bake as _bake
from testflows.machine._core import build as _build
from testflows.machine._core import disks as _disks
from testflows.machine._core import storage_ops as _storage_ops
from . import _args
from . import _when
from . import results
from .errors import InvalidArgument, _translated


@dataclass(frozen=True)
class Disk:
    """One disk: a name for its content, identified by its SHA-256.

    Attributes:
        name (str): The disk's name, unique in the account.
        id (str): The content's SHA-256, `sha256:<hex>`.
        size_bytes (int): The content's size in bytes.
        state (str): `ready` once its content is verified, `transferring`
            while a build is under way.
        created_at (str, optional): When the disk was created, ISO 8601.
        labels (dict[str, str]): What the build attached to this name. A run
            created from this disk inherits them.
        data_bytes (int, optional): The bytes the disk takes in account
            storage, after deduplication and compression. A piece it shares
            with another disk is counted in both. None when the disk has not
            been measured.
    """

    name: str
    id: str
    size_bytes: int
    state: str
    created_at: Optional[str]
    labels: dict[str, str] = dataclasses.field(default_factory=dict)
    data_bytes: Optional[int] = None

    def as_json(self) -> dict[str, Any]:
        """The fields as a JSON-serializable dict.

        Returns:
            dict[str, Any]: One key per attribute.
        """
        return dataclasses.asdict(self)


class Disks:
    """The account's disks, as `Client.disks`; not created directly.

    Call it to list them, as `machine disks` does. A disk is what a run
    boots: `Client.create` takes a disk's name. They belong to the account,
    so every call here answers with no session.

    Example:
        >>> disk = client.disks.build("app", binary="build/app")
        >>> [d.name for d in client.disks()]
        ['app']
    """

    def __init__(self, client: api.client.Client, creds: dict[str, Any]) -> None:
        self._client = client
        self._creds = creds

    @overload
    def __call__(
        self,
        *,
        deleted: bool = ...,
        since: "_when.When | None" = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        label: Optional[dict[str, str]] = ...,
        watch: Literal[False] = ...,
        timeout: Optional[float] = ...,
    ) -> builtins.list[Disk]: ...

    @overload
    def __call__(
        self,
        *,
        deleted: bool = ...,
        since: "_when.When | None" = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        label: Optional[dict[str, str]] = ...,
        watch: Literal[True],
        timeout: Optional[float] = ...,
    ) -> Generator[builtins.list[Disk], None, None]: ...

    def __call__(
        self,
        *,
        deleted: bool = False,
        since: "_when.When | None" = None,
        limit: Optional[int] = None,
        offset: int = 0,
        label: Optional[dict[str, str]] = None,
        watch: bool = False,
        timeout: Optional[float] = None,
    ) -> "builtins.list[Disk] | Generator[builtins.list[Disk], None, None]":
        """List the account's disks, as `machine disks` does; the same as
        `list`.

        Args:
            deleted (bool, optional): List the disks that were removed
                instead. Defaults to False.
            since (datetime or int or str, optional): Keep those created, or
                removed, at or after this time.
            limit (int, optional): List at most this many; None lists all.
            offset (int, optional): Skip this many first. Defaults to 0.
            label (dict[str, str], optional): Keep only the disks carrying
                every one of these labels, by exact value.
            watch (bool, optional): Yield the list each time it changes.
                Defaults to False.
            timeout (float, optional): With `watch`, end the generator after
                this many seconds, without raising; None watches until the
                caller leaves the loop.

        Returns:
            list[Disk]: The disks; with `watch`, an iterator of such lists.
        """
        return _args.watched(
            lambda: self._list(deleted, since, limit, offset, label),
            _args.same,
            watch,
            timeout,
        )

    @overload
    def list(
        self,
        *,
        deleted: bool = ...,
        since: "_when.When | None" = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        label: Optional[dict[str, str]] = ...,
        watch: Literal[False] = ...,
        timeout: Optional[float] = ...,
    ) -> builtins.list[Disk]: ...

    @overload
    def list(
        self,
        *,
        deleted: bool = ...,
        since: "_when.When | None" = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        label: Optional[dict[str, str]] = ...,
        watch: Literal[True],
        timeout: Optional[float] = ...,
    ) -> Generator[builtins.list[Disk], None, None]: ...

    def list(
        self,
        *,
        deleted: bool = False,
        since: "_when.When | None" = None,
        limit: Optional[int] = None,
        offset: int = 0,
        label: Optional[dict[str, str]] = None,
        watch: bool = False,
        timeout: Optional[float] = None,
    ) -> "builtins.list[Disk] | Generator[builtins.list[Disk], None, None]":
        """List the account's disks, newest first, as `machine disks list`
        does.

        Every disk is returned unless `limit` is given. With `watch`, it
        yields the list at once, then again each time it changes: a disk
        added, renamed, deleted, or finishing its transfer. Use it to wait
        for an upload another program started.

        Example:
            >>> [d.name for d in client.disks.list(since="7d")]
            ['app', 'app-debug']
            >>> for disks in client.disks.list(watch=True, timeout=600):
            ...     if any(d.name == "app" and d.state == "ready" for d in disks):
            ...         break

        Args:
            deleted (bool, optional): List the disks that were removed
                instead. Defaults to False.
            since (datetime or int or str, optional): Keep those created, or
                removed, at or after this time: a datetime, Unix seconds, a
                date such as `2026-06-01` or an age such as `7d`.
            limit (int, optional): List at most this many; None lists all.
            offset (int, optional): Skip this many first. Defaults to 0.
            label (dict[str, str], optional): Keep only the disks carrying
                every one of these labels, by exact value.
            watch (bool, optional): Yield the list each time it changes.
                Defaults to False.
            timeout (float, optional): With `watch`, end the generator after
                this many seconds, without raising; None watches until the
                caller leaves the loop.

        Returns:
            list[Disk]: The disks; with `watch`, an iterator of such lists.

        Raises:
            InvalidArgument: `since` is not a time.
            NotSignedIn: There are no credentials.
        """
        return _args.watched(
            lambda: self._list(deleted, since, limit, offset, label),
            _args.same,
            watch,
            timeout,
        )

    def build(
        self,
        name: Optional[str] = None,
        *,
        binary: "str | Path | None" = None,
        from_: Optional[str] = None,
        image: Optional[str] = None,
        compose: "str | Path | None" = None,
        entrypoint: Optional[str] = None,
        command: Optional[builtins.list[str]] = None,
        add: Optional[builtins.list[str]] = None,
        env: Optional[dict[str, str]] = None,
        label: Optional[dict[str, str]] = None,
        size: Optional[int] = None,
        load_at_boot: bool = False,
    ) -> Disk:
        """Build a disk, as `machine disks build` does.

        Exactly one source. `binary` wraps a static x86_64 executable in an
        image built around it. `from_` names a public image, which the build
        pulls. Neither needs Docker. `image` takes a Docker image, as a save
        tar or a name in the local Docker. `compose` takes a project directory
        and the images its services name.

        A binary or an image runs as one container; a project runs every
        service in it. The build itself runs in the account, so the platform
        the disk is made of is never the caller's to supply.

        With `from_` or `binary`, `add` puts files in the image as one more
        layer. An entry is `path` or `path:dest`. Without a destination the
        path lands in the image's working directory under its own name; a
        destination ending in `/` is a directory.

        Example:
            >>> disk = client.disks.build("app", binary="build/app", command=["--port", "8080"])
            >>> run = client.create(disk=disk.name)
            >>> hello = client.disks.build(
            ...     "hello", from_="python:3.12", add=["hello.py"], command=["python", "hello.py"]
            ... )
            >>> stack = client.disks.build("stack", compose="./deploy")

        Args:
            name (str, optional): The disk's name; defaults to what the source
                is called.
            binary (str or Path, optional): A static x86_64 executable.
            from_ (str, optional): A public image, by name.
            image (str, optional): A Docker image, by save tar or by name.
            compose (str or Path, optional): A Compose project directory.
            entrypoint (str, optional): The executable the service runs. Not
                for `compose`, whose file says.
            command (list[str], optional): Arguments the service runs with.
                Not for `compose`, whose file says.
            add (list[str], optional): Files and directories to put in the
                image, each `path` or `path:dest`. For `from_` and `binary`.
            env (dict[str, str], optional): Environment variables the service
                runs with. Not for `compose`, whose file says. The disk holds
                each value as written.
            label (dict[str, str], optional): Labels to attach to the name,
                replacing whatever it carried. A run created from this disk
                inherits them. An empty value removes one.
            size (int, optional): The disk's size in MB, which is the size of
                the guest's root filesystem. Defaults to what the source needs
                plus a gigabyte, rounded up to a whole GB. The platform takes
                about 285MB of it, the images take what they unpack to, and
                the rest is what the guest can write. A size below what the
                source needs is refused.
            load_at_boot (bool): Keep the images on the disk as save tars
                that Docker loads each time the disk boots. By default the
                build writes them into the disk's Docker store, and the disk
                boots with every image present. A guest that runs `docker
                save` or `docker push` on one of them needs this. Such a disk
                holds each image twice and is sized for both.

        Returns:
            Disk: The disk built.

        Raises:
            InvalidArgument: No source, more than one, a source the build
                cannot use, or a project a disk cannot run.
            Conflict: A transfer to the name is under way, or runs still boot
                what the name holds.
        """
        named = [
            n
            for n in ("binary", "from_", "image", "compose")
            if locals()[n] is not None
        ]
        if len(named) != 1:
            raise InvalidArgument(
                "Pass exactly one of binary=, from_=, image= or compose=,"
                f" not {len(named)}."
            )
        kind = named[0]
        source = str(binary or from_ or image or compose)
        entry = [entrypoint] if entrypoint else None
        disk_name = name or _build.default_name(source)
        if add and kind not in ("binary", "from_"):
            raise InvalidArgument(f"add= goes with from_= or binary=, not {kind}=.")
        with _translated():
            added = [_build.parse_added(spec) for spec in add or []]
            # A project takes no env= at all, and is told that below: checking
            # the names first would refuse a malformed one for its name.
            environment = (
                {} if kind == "compose" else _build.checked_environment(env or {})
            )
            if kind == "binary":
                pack = _build.pack_from_binary(
                    Path(source),
                    tag=f"{disk_name}:built",
                    entrypoint=entry,
                    command=command,
                    added=added,
                    environment=environment,
                )
            elif kind == "from_":
                pack = _build.pack_from_registry(
                    source,
                    tag=f"{disk_name}:built",
                    entrypoint=entry,
                    command=command,
                    added=added,
                    environment=environment,
                )
            elif kind == "image":
                pack = _build.pack_from_image(
                    source, entrypoint=entry, command=command, environment=environment
                )
            else:
                if entry or command or env:
                    raise InvalidArgument(
                        "compose= takes no entrypoint=, no env= and no command=;"
                        " set them on the service in the compose file."
                    )
                pack = _build.pack_from_compose(Path(source))
            if load_at_boot:
                pack = _build.loading_at_boot(pack)
        footprint = pack.footprint
        disk_mb = footprint.default_mb if size is None else size
        if disk_mb < footprint.needed_mb:
            pack.remove()
            raise InvalidArgument(
                f"size={disk_mb} is smaller than {disk_name} needs: the platform"
                f" and the images take {footprint.used_mb}MB before the machine"
                f" writes anything, and its volumes grow to"
                f" {footprint.volumes_mb}MB."
            )
        carried = Path(tempfile.mkdtemp(prefix="machine-build-")) / f"{disk_name}.pack"
        try:
            with _translated():
                _bake.pack_tar(pack, carried)
                _disks.build(
                    self._client,
                    self._creds,
                    carried,
                    disk_name,
                    labels=label,
                    disk_mb=disk_mb,
                )
        finally:
            pack.remove()
            shutil.rmtree(carried.parent, ignore_errors=True)
        return self.show(disk_name)

    def show(self, name: "Disk | str") -> Disk:
        """Read a disk, as `machine disks show` does.

        Args:
            name (str): The disk's name.

        Returns:
            Disk: The disk.

        Raises:
            NotFound: No disk has this name.
        """
        named = _args.disk_ref(name)
        with _translated():
            row = api.auth.authed(
                self._creds, lambda t: self._client.disk_get(t, named)
            )
        return _disk(row)

    def verify(
        self,
        name: "Disk | str",
        *,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> results.StorageOp:
        """Check a stored disk against its id, as `machine disks verify`
        does.

        The disk is read back out of storage and its id computed again, which
        is what it is stored under. The check runs on the service: it goes on
        when a wait here ends, and `Storage.ops` lists it. One runs at a time
        for an account.

        Example:
            >>> client.disks.verify("app").state
            'done'

        Args:
            name (str): The disk's name.
            wait (bool, optional): Return once the check has ended. Defaults
                to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            StorageOp: The check. `state` is `done` for a disk that reads as
            it was stored, and `failed` with `error` saying what was wrong.
            With `wait` False it is the check as it started.

        Raises:
            NotFound: No disk has this name.
            Conflict: A check is already running, or the disk is still
                uploading.
            Timeout: The check had not ended within `timeout`. It goes on.
        """
        named = _args.disk_ref(name)
        with _translated():
            started = api.auth.authed(
                self._creds, lambda t: self._client.disk_verify(t, named)
            )
            op_id = int(started["op_id"])

            def read() -> dict[str, Any]:
                rows = api.auth.authed(
                    self._creds, lambda t: self._client.op_list(t, op_id=op_id)
                )
                if not rows:
                    raise RuntimeError(f"disk check {op_id} is not listed")
                return dict(rows[0])

            if not wait:
                return results.storage_op(read())
            return results.storage_op(
                _storage_ops.wait_op(
                    read, what=f"the check of {named}", timeout_s=timeout
                )
            )

    @overload
    def transfers(
        self,
        *,
        active: bool = ...,
        since: "_when.When | None" = ...,
        watch: Literal[False] = ...,
        timeout: Optional[float] = ...,
    ) -> builtins.list[results.Transfer]: ...

    @overload
    def transfers(
        self,
        *,
        active: bool = ...,
        since: "_when.When | None" = ...,
        watch: Literal[True],
        timeout: Optional[float] = ...,
    ) -> Generator[builtins.list[results.Transfer], None, None]: ...

    def transfers(
        self,
        *,
        active: bool = False,
        since: "_when.When | None" = None,
        watch: bool = False,
        timeout: Optional[float] = None,
    ) -> "builtins.list[results.Transfer] | Generator[builtins.list[results.Transfer], None, None]":
        """List uploads, as `machine disks transfers` does.

        Without `since`, the transfers under way; with it, the history from
        then on as well. With `watch`, it yields the list at once, then again
        each time it changes, progress included.

        Args:
            active (bool, optional): With `since`, keep only those under way.
                Defaults to False.
            since (datetime or int or str, optional): List every transfer
                from this time on, finished ones included: a datetime, Unix
                seconds, a date or an age such as `7d`.
            watch (bool, optional): Yield the list each time it changes.
                Defaults to False.
            timeout (float, optional): With `watch`, end the generator after
                this many seconds, without raising; None watches until the
                caller leaves the loop.

        Returns:
            list[Transfer]: One record per transfer, with its progress and
            speed; with `watch`, an iterator of such lists.

        Raises:
            InvalidArgument: `since` is not a time, or `active` was given
                without it: the list is already only what is under way.
        """
        cutoff = _when.iso(since)
        if active and cutoff is None:
            raise InvalidArgument(
                "active= needs since=: without it the list is only what is"
                " under way."
            )

        def read() -> builtins.list[results.Transfer]:
            with _translated():
                if cutoff is None:
                    rows: builtins.list[dict[str, Any]] = api.auth.authed(
                        self._creds, self._client.transfers_list
                    )
                else:
                    rows = api.auth.authed(
                        self._creds,
                        lambda t: self._client.transfers_list(
                            t, since=cutoff, active=active
                        ),
                    )
            return [results.transfer(row) for row in rows]

        return _args.watched(read, _args.same, watch, timeout)

    def delete(self, *names: "Disk | str", force: bool = False) -> None:
        """Delete disks, as `machine disks delete` does.

        Deletion is permanent. The name is free as soon as this returns, and
        the disk reads as `deleting` until its bytes are reclaimed, which is
        when they stop counting against the storage quota. A disk that runs
        still boot is refused unless `force` is True; with it, those runs can
        no longer start. A deleted disk is listed by `list(deleted=True)`.

        Example:
            >>> client.disks.delete("app-old", "app-debug")

        Args:
            *names (str): The disks' names.
            force (bool, optional): Delete even while runs still boot them.
                Defaults to False.

        Raises:
            InvalidArgument: No name is given.
            NotFound: No disk has one of the names.
            Conflict: Runs boot one and `force` is False, or a transfer to
                one is under way.
        """
        if not names:
            raise InvalidArgument("Disk name required.")
        for name in names:
            with _translated():
                api.auth.authed(
                    self._creds,
                    lambda t: self._client.disk_delete(
                        t, _args.disk_ref(name), force=force
                    ),
                )

    def rename(self, name: "Disk | str", new_name: str) -> Disk:
        """Rename a disk, as `machine disks rename` does.

        Args:
            name (str): The disk's name.
            new_name (str): Its new name.

        Returns:
            Disk: The disk, renamed.

        Raises:
            InvalidArgument: `new_name` is not a valid disk name.
            NotFound: No disk has the name `name`.
            Conflict: A disk has the name `new_name`, or a transfer to
                `name` is under way.
        """
        if not _disks.valid_name(new_name):
            raise InvalidArgument(f"Invalid disk name {new_name!r}.")
        with _translated():
            api.auth.authed(
                self._creds,
                lambda t: self._client.disk_rename(t, _args.disk_ref(name), new_name),
            )
        return self.show(new_name)

    def cancel(self, name: "Disk | str") -> None:
        """Cancel the transfer to a disk, as `machine disks cancel` does.

        Use it when an upload was interrupted and still holds the name, which
        refuses a new upload, a rename and a delete of that name.

        Args:
            name (str): The disk's name.

        Raises:
            NotFound: No transfer to the disk is under way.
        """
        with _translated():
            api.auth.authed(
                self._creds,
                lambda t: self._client.disk_cancel(t, _args.disk_ref(name)),
            )

    def _list(
        self,
        deleted: bool,
        since: "_when.When | None",
        limit: Optional[int],
        offset: int,
        label: Optional[dict[str, str]] = None,
    ) -> builtins.list[Disk]:
        cutoff = _when.iso(since)
        with _translated():
            rows = api.auth.authed(
                self._creds,
                lambda t: self._client.disk_list(
                    t,
                    deleted=deleted,
                    since=cutoff,
                    limit=limit,
                    offset=offset,
                    labels=label,
                ),
            )
        return [_disk(row) for row in rows]


def _disk(row: dict[str, Any]) -> Disk:
    """The public form of a disk row."""
    created = row.get("created_at")
    return Disk(
        labels={str(k): str(v) for k, v in (row.get("labels") or {}).items()},
        name=str(row["name"]),
        id=str(row["disk_id"]),
        size_bytes=int(row.get("size_bytes") or 0),
        state=str(row.get("state") or ""),
        created_at=None if created is None else str(created),
        data_bytes=None if row.get("data_bytes") is None else int(row["data_bytes"]),
    )
