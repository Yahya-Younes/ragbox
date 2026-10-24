# Security policy

ragbox is designed to run on your own computer and to be reachable only from it.

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository. You'll get an answer as soon as possible.

## Exposing ragbox to a network

By default the app is bound to `127.0.0.1` and has **no authentication**, because only you can reach it.
If you change the port binding or `RAGBOX_ALLOWED_HOSTS` to make it reachable from other machines,
put it behind a reverse proxy with authentication and HTTPS first.
