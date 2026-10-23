# Render disposable demo

This configuration runs the complete **synthetic** stack in one free Render web
service: the built React UI, nginx, three Django APIs, the outbox worker, and
PostgreSQL 18. Only nginx is public. Separate database owner/runtime roles and
the existing ledger/RLS policies are retained. The LLM stays `mock`.

This is a demo deployment, not the production topology. Free Render services
sleep after 15 minutes without traffic and have no persistent disk. All local
database records, keys, sessions and synthetic payments can disappear on a
restart, redeploy or sleep. The startup recreates a single synthetic logistics
SME. Credentials configured below remain the same, but sessions and changes do
not. Startup includes migrations and seeding, so a cold start takes longer than
serving an existing static site. Runtime must fit within the free 512 MB limit.

## Deploy

1. Put these deployment files in a GitHub repository you control. The original
   `eminroot/safe` repository is read-only to the connected GitHub account.
2. Run `python ops/render/prepare_credentials.py`. It writes two **gitignored**
   private files under `ops/dev-keys/`; existing credentials are never replaced.
3. In Render, create a **Blueprint** from your repository and `render.yaml`.
   Confirm that its sole resource is `safepay-demo` on **Free**, with no database
   resource, worker resource, persistent disk, or paid add-on.
4. Supply `SAFEPAY_DEMO_PASSWORD` and `SAFEPAY_DEMO_TOTP_KEY` from the private
   `render.env` file when Render prompts. Never put these values in GitHub.
5. Deploy, wait for `/healthz` to return HTTP 200, and open the Render URL.
   The landing page is `/`; the operator console is `/console`.
6. Add the setup key from `render-authenticator.txt` to an authenticator app
   using TOTP/SHA1/6 digits/30 seconds. Sign in as `logistics.approver` with
   the supplied password and current code. Only this approver login is set to
   the private deployment values; other seed credentials remain private.

Automatic deploys are off. New changes need an explicit deployment. No real
bank or paid LLM is configured. Evidence metrics require a separately generated
evidence bundle; this demo does not invent results or run that costly harness
on every cold start. The initial approvals queue may be empty.

## Local verification under Render's memory limit

```powershell
python ops/render/prepare_credentials.py
docker build -f ops/render/Dockerfile -t safepay-render-demo:local .
docker run -d --name safepay-render-check --memory=512m --memory-swap=512m --cpus=0.1 --env-file ops/dev-keys/render.env -p 127.0.0.1:10000:10000 safepay-render-demo:local
curl.exe --fail http://127.0.0.1:10000/healthz
docker stats --no-stream safepay-render-check
docker logs --tail 60 safepay-render-check
```

Docker keeps an exited container's writable layer. Remove and recreate the
named **test** container to simulate Render losing its ephemeral filesystem;
stopping and starting that same container does not simulate it.

Original persistent Docker Compose packaging is unchanged. Official references:
[Render Free](https://render.com/docs/free),
[Blueprint specification](https://render.com/docs/blueprint-spec).
