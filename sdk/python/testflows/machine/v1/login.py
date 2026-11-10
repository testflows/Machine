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
"""Sign in with a code sent by email, and sign out.

Signing in is two calls because the code arrives by email between them:

    machine.login.start("me@example.com")
    client = machine.login.verify("me@example.com", code="123456")

The login is stored where `machine login` keeps it, so the CLI and the SDK
share it. Automation uses TESTFLOWS_MACHINE_API_KEY instead.
"""

from __future__ import annotations

from typing import Optional

import testflows.machine._core.login as _login

from .client import Client
from .errors import _translated


def start(email: str, *, api_url: Optional[str] = None) -> None:
    """Send a sign-in code to `email`, as `machine login` does first.

    The code arrives by email; pass it to `verify` to finish signing in. The
    service answers the same whether or not an account exists, so this
    returning is not proof that a code was sent. A program that runs
    unattended sets TESTFLOWS_MACHINE_API_KEY instead of signing in.

    Args:
        email (str): The account's email address.
        api_url (str, optional): The service's URL; defaults to
            TESTFLOWS_MACHINE_API_URL, else the public service.

    Raises:
        ServiceUnavailable: The service could not be reached.
    """
    with _translated():
        client, _ = _login.client_for(api_url)
        _login.start(client, email)


def verify(
    email: str, *, code: str, api_url: Optional[str] = None, save: bool = True
) -> Client:
    """Sign in with the code `start` sent to `email`.

    With `save`, the login is stored where `machine login` keeps it, so the
    CLI and every later `Client` use it until it expires or `logout` removes
    it. Without it, only the returned client is signed in.

    Args:
        email (str): The account's email address, as passed to `start`.
        code (str): The code from the email.
        api_url (str, optional): The service's URL; defaults to
            TESTFLOWS_MACHINE_API_URL, else the public service.
        save (bool, optional): Store the login. Defaults to True.

    Returns:
        Client: A client with no session, signed in;
        `client.sessions.use` gives it one.

    Raises:
        Unauthorized: The code is wrong or has expired; call `start` again.
        ServiceUnavailable: The service could not be reached.
    """
    with _translated():
        client, base_url = _login.client_for(api_url)
        _, token = _login.verify(client, base_url, email, code, save=save)
    return Client._signed_in(
        client, {"access_token": token, "base_url": base_url}, api_url
    )


def logout(*, everywhere: bool = False, api_url: Optional[str] = None) -> bool:
    """Sign out the stored login, as `machine logout` does.

    The stored login is removed even when the service cannot be reached; the
    login then stays valid on the service until it expires, and this returns
    False. An API key in TESTFLOWS_MACHINE_API_KEY is not affected.

    Args:
        everywhere (bool, optional): Sign out every login of the account,
            on every machine. Defaults to False.
        api_url (str, optional): The service's URL; defaults to
            TESTFLOWS_MACHINE_API_URL, else the public service.

    Returns:
        bool: True when the service revoked the login; False when there was
        none, or the service could not be told.
    """
    with _translated():
        out = _login.logout(api_url, everywhere=everywhere)
    return out.revoked is _login.Revoked.YES
