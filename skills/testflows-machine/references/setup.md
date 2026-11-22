# Set up: install the client and sign it in

No command waits for your person: each one that sends an email returns at
once, and the next one takes the code. A code works once, for 10 minutes, from
this computer's network. When one is refused, send a new one.

## 1. The client

Run `machine --version`. If it prints a version, go to step 2.

If `machine` is missing, ask your person: "Do you have a TestFlows Machine
account? What is your email address?" Then install. The installer signs in
before it downloads, so it takes the same steps as signing in:

```bash
# Has an account: emails a sign-in code
curl https://testflows.com/machine/install -fsS | bash -s -- --email <address>
# Needs one: emails a sign-up link; its page shows the Terms to accept, then a code
curl https://testflows.com/machine/install -fsS | bash -s -- --email <address> --signup
# With the code: installs the client and saves an API key for it
curl https://testflows.com/machine/install -fsS | bash -s -- --email <address> --code <code>
```

The client runs on Linux x86_64 and arm64, and on Macs with Apple Silicon. If
the installer says `~/.local/bin` is not on `PATH`, run
`export PATH="$HOME/.local/bin:$PATH"`. Then go to step 2.

## 2. The key

Run `machine -o json account whoami`. If it answers, run `machine upgrade`,
which installs a newer client when there is one, and go on. If it fails with
exit code `2`, go to step 3.

## 3. Sign in

Ask your person whether they have an account and its email address.

```bash
# Has an account: emails a sign-in code
machine login --email <address>
# Needs one: emails a sign-up link; its page shows the Terms to accept, then a code
machine account signup --email <address>
# With the code from the email or the page
machine login --email <address> --code <code>
# Save an API key, so the client stays signed in after the login ends
machine account api-keys create <name> --save
```

Name the key for this computer, such as its hostname. If the key command says
it emailed a confirmation code, ask for it and run it again with
`--code <code>`.

The client signs in with `TESTFLOWS_MACHINE_API_KEY` when it is set, then the
login, then the saved key. If a refusal says the Agreement changed, your
person accepts it at https://testflows.com/machine/portal/account/.

## Commands and docs

- [`machine login`](https://testflows.com/docs/machine/commands.md#machine-login),
  [`machine account signup`](https://testflows.com/docs/machine/commands.md#machine-account-signup),
  [`machine account whoami`](https://testflows.com/docs/machine/commands.md#machine-account-whoami),
  [`machine account api-keys create`](https://testflows.com/docs/machine/commands.md#machine-account-api-keys-create)
- Installing and signing up, step by step:
  https://testflows.com/docs/machine/getting-started.md
- The install guide for agents: https://testflows.com/install.md
